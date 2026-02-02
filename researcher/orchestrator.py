import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

from researcher.state_manager import load_state, ROOT_DIR, DEFAULT_STATE
from researcher.llm_utils import _post_responses, _extract_output_text, MODEL_MINI, diagnose_failure
from researcher.command_utils import extract_commands
from researcher.dev_flow import dev_flow
from researcher.system_context import get_system_context
from researcher.resource_registry import list_resources, read_resource
from researcher.system_info import system_snapshot
from researcher.librarian_client import LibrarianClient
from researcher.config_loader import load_config
from researcher.index import FaissIndex
from researcher.schemas import Response, ProvenanceEntry, Confidence

# --- Constants (adapted from Martin) ---
SHOW_TURN_BAR = True  # To be controlled by researcher's config/verbosity


def decide_next_step(user_input: str) -> Dict[str, Any]:
    """
    Decides the next step based on user input, replacing the Chef/Waiter model.
    """
    snapshot = system_snapshot()
    text = (user_input or "").strip().lower()

    # Simple heuristic for behavior
    wants_plan = bool(re.search(r"\b(plan|schedule|track|tracker|checklist|chore|chores|todo|to-do|organize|organise|keep me honest|remind|reminder|queue|sequence)\b", text, re.IGNORECASE))
    wants_build = bool(re.search(r"\b(build|implement|code|fix|create|add|develop|make|generate|write|script|patch|resolve|solve|refactor|change|modify|update|edit|configure|set\s+up|setup|debug|troubleshoot|repair|adjust|alter|amend|produce|formulate|want|cd|directory|navigate)\b", text, re.IGNORECASE))
    wants_review = bool(re.search(r"\b(review|audit|critique|code review|analyze|check|inspect|examine|assess|evaluate|verify|look|understand|explain|investigate|explore|read|show|display|present|report|find|locate|search|comprehend|interpret|decipher|inform|clarify|detail|want)\b", text, re.IGNORECASE))

    if wants_plan:
        behavior = "plan"
        guidance = (
            "Planning focus: turn the request into a short ordered checklist with time cues. "
            "Confirm you will track progress and propose a check-in cadence."
        )
    elif wants_review:
        behavior = "review"
        guidance = (
            "Review focus: bugs, risks, regressions, and missing tests. "
            "Be concise and specific; cite files/lines when possible. "
            "Avoid unnecessary changes."
        )
    elif wants_build:
        behavior = "build"
        guidance = (
            "Be safe and explicit. Use 'command:' only for non-interactive commands. "
            "Avoid destructive actions and request confirmation when needed."
        )
    else:
        behavior = "chat"
        guidance = "Be helpful and answer the user's questions directly."

    # Simple question extraction
    sentences = re.split(r'(?<=[.!?])\s+', (user_input or "").strip())
    questions = [s.strip() for s in sentences if "?" in s and s.strip()]

    return {
        "guidance_banner": guidance,
        "behavior": behavior,
        "question_summaries": questions,
        "inventory": sorted(list(ABILITY_REGISTRY.keys())),
        "snapshot": snapshot,
        "user_intent_summary": text[:200],  # Add a simple summary
    }


def _safe_json(text: str) -> Dict[str, Any]:
    """Best-effort JSON extraction from a model response."""
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        pass
    # Try to extract the first JSON object in the text.
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def _ability_env_check(_: str) -> str:
    return json.dumps(system_snapshot(), ensure_ascii=False, indent=2)

def _ability_system_context(_: str) -> str:
    return json.dumps(get_system_context(), ensure_ascii=False, indent=2)


def _ability_plan_extract(payload: str) -> str:
    cmds = extract_commands(payload or "")
    return json.dumps({"commands": cmds}, ensure_ascii=False)


def _ability_dev_create(payload: str) -> str:
    # Accept JSON payload with path/content for append-only write.
    try:
        data = json.loads(payload) if payload and payload.strip().startswith("{") else {}
    except Exception:
        data = {}
    path = data.get("path")
    content = data.get("content")
    if path and content:
        p = Path(path)
        if not p.exists():
            return "failed: path does not exist (append-only)"
        try:
            from researcher.file_utils import preview_write
            before = p.read_text(encoding="utf-8")
        except Exception:
            before = ""
        after = before + ("\n" + content.rstrip() + "\n")
        if preview_write(p, after):
            with p.open("a", encoding="utf-8") as f:
                f.write("\n" + content.rstrip() + "\n")
        else:
            return "skipped: change not approved"
        return "ok"
    ok = dev_flow(payload or "")
    return "ok" if ok else "failed"


def _ability_diagnose(payload: str) -> str:
    cmd = ""
    out = ""
    try:
        data = json.loads(payload) if payload and payload.strip().startswith("{") else {}
        cmd = data.get("cmd", "") or ""
        out = data.get("output", "") or ""
    except Exception:
        out = payload or ""
    if not cmd and not out:
        return "diagnose requires JSON with cmd/output or payload text."
    return diagnose_failure(cmd or "<unknown>", out or "")

def _ability_resource_list(payload: str) -> str:
    try:
        data = json.loads(payload) if payload and payload.strip().startswith("{") else {}
    except Exception:
        data = {}
    max_items = int(data.get("max_items", 200))
    max_depth = int(data.get("max_depth", 4))
    items = list_resources(ROOT_DIR, max_items=max_items, max_depth=max_depth)
    return json.dumps({"root": str(ROOT_DIR), "items": items}, ensure_ascii=False)

def _ability_resource_read(payload: str) -> str:
    try:
        data = json.loads(payload) if payload and payload.strip().startswith("{") else {}
    except Exception:
        data = {}
    path = data.get("path") or payload
    ok, result = read_resource(str(path), ROOT_DIR)
    result["ok"] = ok
    return json.dumps(result, ensure_ascii=False)

def _ability_catalog_list(_: str) -> str:
    """Gets the card catalog from the Librarian."""
    client = LibrarianClient()
    try:
        response = client.request_card_catalog()
    finally:
        client.close()
    
    if response.get("status") == "success":
        return json.dumps(response.get("result", {}), ensure_ascii=False, indent=2)
    else:
        # Return the whole error response if status is not success
        return json.dumps(response, ensure_ascii=False, indent=2)


def _ability_ask_query(payload: str) -> str:
    """
    Queries local RAG, and optionally the cloud, to answer a question.
    This implements the core logic for the "ask" behavior with a cloud hop.
    """
    query = payload
    config = load_config()
    req_id = str(uuid.uuid4())

    # --- 1. Query Local RAG ---
    vs_config = config.get("vector_store", {})
    embedding_model = config.get("embedding_model")
    index_path = Path(vs_config.get("index_path"))

    local_provenance = []
    local_confidence = 0.0
    answer = "No answer found."

    if vs_config.get("type") == "faiss" and index_path.exists():
        index = FaissIndex.load(model_name=embedding_model, index_path=index_path)
        local_results = index.search(query, k=5)

        if local_results:
            top_score = local_results[0][0]
            local_confidence = top_score
            for score, meta in local_results:
                provenance_entry = ProvenanceEntry(
                    source="local",
                    score=score,
                    text=meta.get("text", ""),
                    meta=meta,
                )
                local_provenance.append(provenance_entry)
            answer = local_provenance[0].text # Use top result as answer for now

    # --- 2. Decide on Cloud Hop ---
    cloud_config = config.get("cloud", {})
    auto_update_config = config.get("auto_update", {})
    cloud_provenance = []
    cloud_confidence = 0.0
    
    should_hop = (
        cloud_config.get("enabled") and
        cloud_config.get("trigger_on_low_confidence") and
        local_confidence < cloud_config.get("low_confidence_threshold", 0.25)
    )

    if should_hop:
        client = LibrarianClient()
        try:
            cloud_response = client.query_cloud(
                prompt=query,
                cloud_mode="auto" # or some other configured mode
            )
            if cloud_response.get("status") == "success":
                cloud_result = cloud_response.get("result", {})
                cloud_output = cloud_result.get("output", "")
                if cloud_output:
                    # For now, we'll treat the whole cloud output as one provenance entry
                    cloud_provenance_entry = ProvenanceEntry(
                        source="cloud",
                        score=cloud_result.get("confidence", 0.8), # Use a fixed high confidence for cloud
                        text=cloud_output,
                        meta={
                            "provider": cloud_result.get("provider"),
                            "model": cloud_result.get("model"),
                            "sanitized": cloud_result.get("sanitized"),
                            "hash": cloud_result.get("hash"),
                        }
                    )
                    cloud_provenance.append(cloud_provenance_entry)
                    cloud_confidence = cloud_provenance_entry.score
                    # If cloud has a good answer, it becomes the main answer
                    answer = cloud_output

                    # --- Ingest cloud answer back into RAG ---
                    if auto_update_config.get("ingest_cloud_answers"):
                        processed_path = Path(config.get("data_paths", {}).get("processed", "data/processed"))
                        cloud_answers_path = processed_path / "cloud_answers"
                        cloud_answers_path.mkdir(parents=True, exist_ok=True)
                        
                        content_hash = hashlib.sha256(cloud_output.encode("utf-8")).hexdigest()
                        temp_file_path = cloud_answers_path / f"{content_hash}.txt"
                        
                        if not temp_file_path.exists():
                            temp_file_path.write_text(cloud_output, encoding="utf-8")
                            client.request_ingestion([str(temp_file_path)])
        finally:
            client.close()

    # --- 3. Assemble Response ---
    response = Response(
        id=req_id,
        answer=answer,
        provenance={"local": local_provenance, "cloud": cloud_provenance},
        confidence=Confidence(local=local_confidence, cloud=cloud_confidence),
    )

    return response.model_dump_json(indent=2)


ABILITY_REGISTRY = {
    "env.check": _ability_env_check,
    "system.context": _ability_system_context,
    "plan.extract_commands": _ability_plan_extract,
    "dev.create_file": _ability_dev_create,
    "diagnose": _ability_diagnose,
    "resource.list": _ability_resource_list,
    "resource.read": _ability_resource_read,
    "catalog.list": _ability_catalog_list,
    "ask.query": _ability_ask_query,
}


def dispatch_internal_ability(ability_key: str, payload: str) -> Tuple[bool, str]:
    handler = ABILITY_REGISTRY.get(ability_key)
    if not handler:
        return False, f"unknown ability: {ability_key}"
    try:
        return True, handler(payload)
    except Exception as e:
        return False, f"ability '{ability_key}' failed: {e}"

from typing import List, Dict, Any

from researcher.schemas import Response, ProvenanceEntry, Confidence


def build_response(
    req_id: str,
    answer: str,
    hits: List[Any],
    logs_ref: str = None,
) -> Response:
    prov_entries = [
        ProvenanceEntry(source=meta.get("path", "unknown"), score=score, text=meta.get("chunk", ""))
        for score, meta in hits
    ]
    conf_local = max([h[0] for h in hits], default=0.0)
    return Response(
        id=req_id,
        answer=answer,
        provenance={"local": prov_entries},
        confidence=Confidence(local=conf_local, cloud=0.0),
        logs_ref=logs_ref,
    )

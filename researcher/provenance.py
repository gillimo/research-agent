from typing import List, Dict, Any, Optional

from researcher.schemas import Response, ProvenanceEntry, Confidence


def build_response(
    req_id: str,
    answer: str,
    hits: List[Any],
    logs_ref: str = None,
    cloud_hits: Optional[List[Any]] = None,
) -> Response:
    prov_entries = [
        ProvenanceEntry(source=meta.get("path", "unknown"), score=score, text=meta.get("chunk", ""))
        for score, meta in hits
    ]
    prov: Dict[str, List[ProvenanceEntry]] = {"local": prov_entries}
    conf_local = max([h[0] for h in hits], default=0.0)
    conf_cloud = 0.0
    if cloud_hits:
        cloud_entries = [
            ProvenanceEntry(source=meta.get("path", "cloud"), score=score, text=meta.get("chunk", ""))
            for score, meta in cloud_hits
        ]
        prov["cloud"] = cloud_entries
        conf_cloud = max([h[0] for h in cloud_hits], default=0.0)
    return Response(
        id=req_id,
        answer=answer,
        provenance=prov,
        confidence=Confidence(local=conf_local, cloud=conf_cloud),
        logs_ref=logs_ref,
    )

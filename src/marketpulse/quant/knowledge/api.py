"""Read-only library API; developer presentation flags never grant write access."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from pydantic import AwareDatetime

from marketpulse.infrastructure.storage.models import BlobError, BlobRef

from .rule_retrieval import retrieve

router = APIRouter(prefix="/api/quant/knowledge", tags=["Investment source library"])
DocumentId = Annotated[str, Path(pattern=r"^[A-Z0-9-]{3,80}$")]
Version = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]


def checked(action):
    try:
        return action()
    except KeyError:
        raise HTTPException(404, detail="未找到该资料版本。") from None
    except (BlobError, ValueError):
        raise HTTPException(409, detail="原文或版本校验失败，已停止展示这份资料。") from None


@router.get("/release")
def release_record(request: Request):
    from .release import read
    from .research_store import InvestmentStore

    return checked(
        lambda: read(
            InvestmentStore(request.app.state.research_store), request.app.state.knowledge_rules
        )
    )


@router.get("/release/comparison")
def release_comparison(request: Request):
    record = release_record(request)
    if record.get("status") != "COMPLETED":
        raise HTTPException(404, "尚未保存统一交付记录。")
    return checked(lambda: request.app.state.knowledge_rules.audit(record["ablation_audit_id"]))


@router.get("/release/export")
def release_export(request: Request):
    from .release import export
    from .research_store import InvestmentStore

    raw = checked(
        lambda: export(
            InvestmentStore(request.app.state.research_store), request.app.state.knowledge_rules
        )
    )
    return Response(
        raw,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="alphaagent-knowledge-k7.zip"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/rules")
def rules(request: Request):
    return checked(request.app.state.knowledge_rules.catalog)


@router.get("/rules/search")
def search_rules(
    request: Request,
    q: str = Query(min_length=1, max_length=120),
    horizon: str | None = None,
    cutoff: AwareDatetime | None = None,
    limit: int = Query(default=12, ge=1, le=12),
    diversify: bool = True,
):
    return checked(
        lambda: retrieve(
            request.app.state.knowledge_rules,
            q,
            horizon=horizon,
            cutoff=cutoff,
            limit=limit,
            diversify=diversify,
        )
    )


@router.get("/rules/{rule_id}/versions/{version}")
def rule(request: Request, rule_id: DocumentId, version: Version):
    return checked(lambda: request.app.state.knowledge_rules.rule(rule_id, version))


@router.get("/reviews/{review_id}")
def review(request: Request, review_id: Version):
    return checked(lambda: request.app.state.knowledge_rules.review(review_id))


@router.get("/audits/{audit_id}")
def audit(request: Request, audit_id: Version):
    return checked(lambda: request.app.state.knowledge_rules.audit(audit_id))


@router.get("/catalog")
def catalog(request: Request):
    return checked(request.app.state.knowledge_store.catalog)


@router.get("/search")
def search(
    request: Request,
    q: str = Query(min_length=1, max_length=120),
    category: str | None = Query(default=None, max_length=40),
    cutoff: AwareDatetime | None = None,
    limit: int = Query(default=30, ge=1, le=50),
):
    if not q.strip():
        raise HTTPException(422, detail="请输入有效的检索关键词。")
    return checked(
        lambda: request.app.state.knowledge_store.search(
            q, category=category, cutoff=cutoff, limit=limit
        )
    )


@router.get("/documents/{document_id}/versions")
def versions(request: Request, document_id: DocumentId):
    return dict(versions=request.app.state.knowledge_store.versions(document_id))


@router.get("/documents/{document_id}/versions/{version}")
def document(request: Request, document_id: DocumentId, version: Version):
    return checked(lambda: request.app.state.knowledge_store.document(document_id, version))


@router.get("/documents/{document_id}/versions/{version}/original")
def original(request: Request, document_id: DocumentId, version: Version):
    store = request.app.state.knowledge_store
    doc = checked(lambda: store.document(document_id, version))
    raw = checked(lambda: store.blobs.get_bytes(BlobRef.from_uri(doc["raw_ref"])))
    extension = "pdf" if doc["source"]["media_type"] == "application/pdf" else "html"
    return Response(
        raw,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{document_id}-{version[:12]}.{extension}"'
            ),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
    )

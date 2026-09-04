"""知识库 RAG 路由。

接口：
  POST /api/knowledge/ingest    — 文档导入（ multipart/form-data，支持 PDF/MD/TXT/Word ）
  POST /api/knowledge/search    — 检索（返回 Top-N 引用结果）
  DELETE /api/knowledge/{doc_id} — 删除文档索引
  GET  /api/knowledge/docs      — 文档列表（命中次数统计）
"""
import hashlib
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from ..rag import RagPipeline
from ..storage import tool_registry_repo

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
logger = logging.getLogger(__name__)

# 单例（由 main lifespan 初始化）
pipeline: RagPipeline | None = None


def get_pipeline() -> RagPipeline:
    if pipeline is None:
        raise HTTPException(status_code=503, detail="rag_pipeline_not_initialized")
    return pipeline


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    doc_id: str | None = None  # 可选过滤


class SearchResponse(BaseModel):
    results: List[Dict[str, Any]]
    total: int


@router.post("/ingest")
async def ingest(file: UploadFile, doc_id: str | None = None):
    """导入单个文档。doc_id 不传则按文件名 + 内容 hash 生成。"""
    p = get_pipeline()
    if not doc_id:
        content = await file.read()
        doc_id = hashlib.sha256(f"{file.filename}:{content}".encode()).hexdigest()[:16]
    meta = {"source": file.filename, "size_bytes": len(content) if hasattr(file, "_file") else 0}
    try:
        inserted = await p.ingest(file.filename, await file.read(), doc_id, meta)
        return {"doc_id": doc_id, "chunks": inserted, "source": file.filename}
    except Exception as e:
        logger.exception("ingest failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest):
    p = get_pipeline()
    results = await p.search(req.query, top_k=req.top_k, doc_id=req.doc_id)
    return {"results": results, "total": len(results)}


@router.delete("/{doc_id}")
async def delete_doc(doc_id: str):
    p = get_pipeline()
    deleted = await p.delete_doc(doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="doc_not_found")
    return {"ok": True, "doc_id": doc_id}


@router.get("/docs")
async def list_docs():
    p = get_pipeline()
    return await p.list_docs()

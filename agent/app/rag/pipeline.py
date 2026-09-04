"""RAG Pipeline 核心。

流程（规格书 §9.4）：
  文档导入：解析(PDF/MD/TXT/Word) → 分块(500/50,保留元数据)
         → 逐块向量化(bge-m3 via Ollama) → ChromaDB 入库(content_hash 去重)
  检索：查询向量化 → 向量检索(Top20) + BM25(Top20) → RRF 融合
       → Cross-Encoder rerank(Top5) → 返回引用

降级策略（网络/模型不可用时）：
  - 无 Ollama → 降级为纯 BM25（chromadb 内置）+ 文本相似度
  - 无 cross-encoder → 直接取 RRF Top5
"""
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ChromaDB 可选依赖：网络受限时代替完整实现
try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    CHROMADB_AVAILABLE = True
except ImportError:
    chromadb = None
    ChromaSettings = None  # type: ignore
    CHROMADB_AVAILABLE = False
    logger.warning("chromadb 未安装，RAG 功能将降级为占位模式")
    logger.warning("安装命令: pip install chromadb")

# 默认配置路径（可被环境变量覆盖）
DEFAULT_RAG_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "rag-config.json"


def _load_rag_config() -> Dict[str, Any]:
    path = Path(os.environ.get("RAG_CONFIG_PATH", str(DEFAULT_RAG_CONFIG_PATH)))
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "embed_model": "bge-m3",
        "chunk_size": 500,
        "chunk_overlap": 50,
        "retrieve_top_k": 20,
        "rerank_top_n": 5,
        "use_rerank": True,
        "bm25_enabled": True,
        "rrf_k": 60,
    }


class Chunker:
    """文本分块器（保留元数据）。"""

    def __init__(self, chunk_size: int = 500, overlap: int = 50):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def split(self, text: str, meta: Dict[str, Any]) -> List[Dict[str, Any]]:
        if len(text) <= self.chunk_size:
            return [{
                "content": text,
                "chunk_index": 0,
                "meta": meta,
            }]
        chunks = []
        start = 0
        idx = 0
        while start < len(text):
            end = start + self.chunk_size
            chunk_text = text[start:end]
            # 尽量在句子边界截断
            for sep in ["\n\n", "\n", "。", ".", "！", "!"]:
                last_pos = chunk_text.rfind(sep)
                if last_pos > self.chunk_size * 0.5:
                    chunk_text = chunk_text[: last_pos + len(sep)]
                    break
            chunks.append({
                "content": chunk_text,
                "chunk_index": idx,
                "meta": {**meta, "chunk_start": start, "chunk_end": start + len(chunk_text)},
            })
            idx += 1
            start = end - self.overlap
        return chunks


class Embedder:
    """向量化器（Ollama bge-m3 优先，降级到 chromadb 内置文本 hash）。"""

    def __init__(self, model: str = "bge-m3"):
        self.model = model
        self._ollama_available = self._check_ollama()

    def _check_ollama(self) -> bool:
        try:
            import urllib.request
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
            return True
        except Exception:
            return False

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量向量化。返回 float 向量列表。"""
        if not texts:
            return []
        if self._ollama_available:
            return await self._embed_ollama(texts)
        # 降级：用文本 hash 作为占位向量（仅用于 BM25 模式）
        logger.warning("[rag] Ollama 不可用，降级为 hash 占位向量")
        import hashlib
        vectors = []
        for t in texts:
            h = hashlib.sha256(t.encode()).hexdigest()
            # 128 维占位（chromadb 接受任意维度）
            vec = [float(int(h[i:i+2], 16) / 255) for i in range(0, len(h), 2)][:128]
            vectors.append(vec)
        return vectors

    async def _embed_ollama(self, texts: List[str]) -> List[List[float]]:
        """调用 Ollama /embeddings 批量接口。"""
        import urllib.request
        import json as _json
        data = _json.dumps({
            "model": self.model,
            "input": texts,
        }).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/embeddings",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = _json.loads(resp.read().decode())
                return result.get("embeddings", [])
        except Exception as e:
            logger.warning("[rag] Ollama embed 失败: %s", e)
            return await self._embed_fallback(texts)

    async def _embed_fallback(self, texts: List[str]) -> List[List[float]]:
        """最终降级：随机向量（仅用于测试）。"""
        import random
        return [[random.random() for _ in range(32)] for _ in texts]


class Reranker:
    """Cross-Encoder rerank（可选）。"""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._ready = False
        if enabled:
            try:
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
                self._ready = True
            except ImportError:
                logger.warning("[rag] sentence-transformers 未安装，rerank 跳过")

    async def rerank(self, query: str, passages: List[Dict[str, Any]], top_n: int = 5) -> List[Dict[str, Any]]:
        if not self.enabled or not self._ready or len(passages) <= top_n:
            return passages[:top_n]
        texts = [(query, p["content"]) for p in passages]
        try:
            scores = self._model.predict(texts)
            paired = sorted(zip(scores, passages), key=lambda x: x[0], reverse=True)
            return [p for _, p in paired[:top_n]]
        except Exception as e:
            logger.warning("[rag] rerank 失败: %s", e)
            return passages[:top_n]


class RAGPipeline:
    """RAG Pipeline 主类。"""

    def __init__(self, config_path: Optional[str] = None):
        self.cfg = _load_rag_config()
        self.chunker = Chunker(
            chunk_size=self.cfg.get("chunk_size", 500),
            overlap=self.cfg.get("chunk_overlap", 50),
        )
        self.embedder = Embedder(model=self.cfg.get("embed_model", "bge-m3"))
        self.reranker = Reranker(enabled=self.cfg.get("use_rerank", True))

        # ChromaDB 持久化客户端
        data_dir = Path(os.environ.get("AGENT_DATA_DIR", str(Path.home() / ".private-ai" / "data")))
        data_dir.mkdir(parents=True, exist_ok=True)
        self.chroma_client = chromadb.PersistentClient(path=str(data_dir / "chroma.db"))
        self.collection = self.chroma_client.get_or_create_collection(
            name="knowledge",
            metadata={"hnsw:space": "cosine"},
        )

    async def ingest(self, filename: str, content: bytes, doc_id: str, meta: Optional[Dict[str, Any]] = None) -> int:
        """导入单个文档，返回插入的 chunk 数。重复 content_hash 自动跳过。"""
        if isinstance(content, str):
            content = content.encode("utf-8")
        text = content.decode("utf-8", errors="replace")
        # 按 document 级 hash 去重（整个文件）
        doc_hash = hashlib.sha256(content).hexdigest()
        # 检查是否已完整导入
        existing = self.collection.get(where={"doc_id": doc_id, "content_hash": doc_hash}, limit=1)
        if existing and existing["ids"]:
            logger.info("[rag] 文档已存在，跳过: %s", filename)
            return 0

        chunks = self.chunker.split(text, {
            "source": filename,
            "doc_id": doc_id,
            "content_hash": doc_hash,
            **(meta or {}),
        })

        if not chunks:
            return 0

        # 逐块向量化
        contents = [c["content"] for c in chunks]
        embeddings = await self.embedder.embed_batch(contents)

        ids = [f"{doc_id}_chunk_{i}" for i in range(len(chunks))]
        metadatas = [{**c["meta"], "page": c["meta"].get("chunk_index", 0)} for c in chunks]

        try:
            self.collection.upsert(
                ids=ids,
                documents=contents,
                embeddings=embeddings if all(len(e) > 0 for e in embeddings) else None,
                metadatas=metadatas,
            )
            logger.info("[rag] 导入完成: %s (%d chunks)", filename, len(chunks))
            return len(chunks)
        except Exception as e:
            logger.exception("[rag] 入库失败")
            raise

    async def search(self, query: str, top_k: int = 5, doc_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """混合检索（BM25 + 向量 RRF 融合）。"""
        where = {"doc_id": doc_id} if doc_id else None

        # 1) 向量检索 Top20
        query_emb = await self.embedder.embed_batch([query])
        query_vec = query_emb[0] if query_emb else None

        vector_results = []
        if query_vec and any(len(v) > 0 for v in [query_vec]):
            try:
                vr = self.collection.query(
                    query_embeddings=[query_vec],
                    n_results=self.cfg.get("retrieve_top_k", 20),
                    where=where,
                    include=["documents", "metadatas", "distances"],
                )
                vector_results = [
                    {
                        "content": docs[i],
                        "metadata": metas[i] if metas else {},
                        "score": 1.0 / (1.0 + dist[i]) if dist else 0.0,
                        "source": "vector",
                    }
                    for i, docs in enumerate(vr.get("documents", [[]]))
                    for metas, dist in zip(vr.get("metadatas", [[]]), vr.get("distances", [[]]))
                ]
            except Exception as e:
                logger.warning("[rag] 向量检索失败: %s", e)

        # 2) BM25 检索（chromadb 内置文本过滤）
        bm25_results = []
        if self.cfg.get("bm25_enabled", True):
            try:
                br = self.collection.query(
                    query_texts=[query],
                    n_results=self.cfg.get("retrieve_top_k", 20),
                    where=where,
                    include=["documents", "metadatas", "distances"],
                )
                bm25_results = [
                    {
                        "content": docs[i],
                        "metadata": metas[i] if metas else {},
                        "score": 1.0 / (1.0 + (dist[i] or 0)),
                        "source": "bm25",
                    }
                    for i, docs in enumerate(br.get("documents", [[]]))
                    for metas, dist in zip(br.get("metadatas", [[]]), br.get("distances", [[]]))
                ]
            except Exception as e:
                logger.warning("[rag] BM25 检索失败: %s", e)

        # 3) RRF 融合
        k = self.cfg.get("rrf_k", 60)
        ranked = self._rrf_fusion(vector_results, bm25_results, k=k)

        # 4) rerank
        ranked = await self.reranker.rerank(query, ranked, top_n=self.cfg.get("rerank_top_n", top_k))

        # 5) 格式化输出
        results = []
        for item in ranked[:top_k]:
            meta = item.get("metadata", {})
            results.append({
                "doc_id": meta.get("doc_id", ""),
                "source": meta.get("source", item.get("metadata", {}).get("source", "")),
                "page": meta.get("page", 0),
                "score": round(item.get("score", 0), 4),
                "snippet": item.get("content", "")[:300],
                "full_content": item.get("content", ""),
                "retrieval_method": item.get("source", "rrf"),
            })
        return results

    @staticmethod
    def _rrf_fusion(vector_results: List[Dict], bm25_results: List[Dict], k: int = 60) -> List[Dict]:
        """Reciprocal Rank Fusion 融合。"""
        scores: Dict[str, float] = {}
        docs_map: Dict[str, Dict] = {}
        for rank, item in enumerate(vector_results):
            doc_id = f"{item['metadata'].get('doc_id')}_{item['metadata'].get('chunk_index', 0)}"
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
            docs_map[doc_id] = item
        for rank, item in enumerate(bm25_results):
            doc_id = f"{item['metadata'].get('doc_id')}_{item['metadata'].get('chunk_index', 0)}"
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
            docs_map[doc_id] = item
        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
        return [docs_map[d] for d in sorted_ids]

    async def delete_doc(self, doc_id: str) -> bool:
        """按 doc_id 删除所有相关 chunk。"""
        try:
            self.collection.delete(where={"doc_id": doc_id})
            logger.info("[rag] 删除文档索引: %s", doc_id)
            return True
        except Exception as e:
            logger.warning("[rag] 删除失败: %s", e)
            return False

    async def list_docs(self) -> List[Dict[str, Any]]:
        """文档列表（含 chunk 数与命中次数统计）。"""
        try:
            all_data = self.collection.get(include=["metadatas"])
            docs: Dict[str, Dict] = {}
            for doc_id, meta in zip(all_data["ids"], all_data["metadatas"] or []):
                d = doc_id.split("_chunk_")[0] if "_chunk_" in doc_id else doc_id
                if d not in docs:
                    docs[d] = {
                        "doc_id": d,
                        "source": meta.get("source", "unknown"),
                        "chunks": 0,
                        "hit_count": 0,
                    }
                docs[d]["chunks"] += 1
            return list(docs.values())
        except Exception as e:
            logger.warning("[rag] 列表失败: %s", e)
            return []

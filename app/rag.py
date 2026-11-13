"""RAG-тің іздеу бөлігі: әр бизнестің өз білім базасы бар.

knowledge.txt → шағын бөліктер → векторлар → ChromaDB (бизнеске бөлек коллекция).
Сұрақ келгенде — тек сол бизнестің бөліктерінен ең жақындары ізделеді.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import chromadb

Embedder = Callable[[list[str]], list[list[float]]]

# Қазақ және орыс тілін түсінетін көп тілді модель (~470 МБ, алғаш рет жүктеледі).
EMBEDDING_MODEL = "intfloat/multilingual-e5-small"
CHUNK_SIZE = 500


@dataclass(frozen=True)
class Hit:
    text: str
    part: int
    distance: float


def default_embedder() -> Embedder:
    """sentence-transformers моделін тек алғаш қажет болғанда жүктейді."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(EMBEDDING_MODEL)

    def embed(texts: list[str]) -> list[list[float]]:
        return model.encode(texts, normalize_embeddings=True).tolist()

    return embed


def split_into_chunks(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """Абзацтарды шамамен size таңбалық бөліктерге жинайды.
    Тым ұзын абзацты сөйлемдер бойынша бөледі."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    pieces: list[str] = []
    for p in paragraphs:
        if len(p) <= size:
            pieces.append(p)
            continue
        buf = ""
        for sentence in p.replace("\n", " ").split(". "):
            if buf and len(buf) + len(sentence) > size:
                pieces.append(buf.strip())
                buf = ""
            buf += sentence + ". "
        if buf.strip():
            pieces.append(buf.strip())

    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        if buf and len(buf) + len(piece) > size:
            chunks.append(buf.strip())
            buf = ""
        buf += piece + "\n\n"
    if buf.strip():
        chunks.append(buf.strip())
    return chunks


class KnowledgeBase:
    def __init__(self, db_dir: Path | None = None, embedder: Embedder | None = None):
        self._client = (
            chromadb.PersistentClient(path=str(db_dir)) if db_dir else chromadb.EphemeralClient()
        )
        self._embed_fn: Embedder | None = embedder

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if self._embed_fn is None:
            self._embed_fn = default_embedder()
        return self._embed_fn(texts)

    @staticmethod
    def _name(business_id: str) -> str:
        return f"biz_{business_id.replace('-', '_')}"

    def index(self, business_id: str, text: str) -> int:
        """Бизнестің ескі деректерін өшіріп, жаңадан құрады. Бөлік санын қайтарады."""
        name = self._name(business_id)
        try:
            self._client.delete_collection(name)
        except Exception:
            pass
        collection = self._client.create_collection(name=name, metadata={"hnsw:space": "cosine"})

        chunks = split_into_chunks(text)
        if not chunks:
            return 0
        # e5 моделі: құжат бөлігінің алдына "passage: ", сұрақтың алдына "query: ".
        vectors = self._embed(["passage: " + c for c in chunks])
        collection.add(
            ids=[str(i) for i in range(len(chunks))],
            documents=chunks,
            embeddings=vectors,
            metadatas=[{"part": i + 1} for i in range(len(chunks))],
        )
        return len(chunks)

    def search(self, business_id: str, question: str, k: int = 3) -> list[Hit]:
        try:
            collection = self._client.get_collection(self._name(business_id))
        except Exception:
            return []
        count = collection.count()
        if count == 0:
            return []
        result = collection.query(
            query_embeddings=self._embed(["query: " + question]),
            n_results=min(k, count),
        )
        return [
            Hit(text=doc, part=int(meta["part"]), distance=float(dist))
            for doc, meta, dist in zip(
                result["documents"][0], result["metadatas"][0], result["distances"][0]
            )
        ]

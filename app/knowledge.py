"""Lightweight RAG over curated, reviewed website content.

The corpus is ~30 sections, so an in-memory BM25 index is enough for the demo.
The retrieval interface (search(query, scope) -> chunks with source URL) stays the same
when it moves to Postgres FTS + pgvector.
"""

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

CONTENT_DIR = Path(__file__).parent / "knowledge_content"
_TOKEN = re.compile(r"[a-z0-9+]+")
_STOP = set("a an the and or of to for in on is are be with by it this that as at from your you can what how do does i my me".split())


@dataclass(frozen=True)
class Chunk:
    id: str
    title: str
    heading: str
    url: str
    scope: str
    text: str


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


def _load_chunks() -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(CONTENT_DIR.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        _, front, body = raw.split("---", 2)
        meta = yaml.safe_load(front)
        for i, section in enumerate(re.split(r"^## ", body, flags=re.M)[1:]):
            heading, _, text = section.partition("\n")
            chunks.append(
                Chunk(
                    id=f"{path.stem}#{i}",
                    title=meta["title"],
                    heading=heading.strip(),
                    url=meta["url"],
                    scope=meta.get("scope", "service_info"),
                    text=text.strip(),
                )
            )
    return chunks


class BM25Index:
    def __init__(self, chunks: list[Chunk], k1: float = 1.4, b: float = 0.75) -> None:
        self.chunks = chunks
        self.docs = [_tokens(f"{c.title} {c.heading} {c.heading} {c.text}") for c in chunks]
        self.k1, self.b = k1, b
        self.avgdl = sum(map(len, self.docs)) / max(len(self.docs), 1)
        df: dict[str, int] = {}
        for d in self.docs:
            for t in set(d):
                df[t] = df.get(t, 0) + 1
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, scope: str, k: int = 4) -> list[Chunk]:
        q = _tokens(query)
        scored = []
        for chunk, doc in zip(self.chunks, self.docs):
            if chunk.scope != scope:
                continue
            score = 0.0
            for t in q:
                tf = doc.count(t)
                if tf:
                    score += self.idf[t] * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * len(doc) / self.avgdl))
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda x: -x[0])
        return [c for _, c in scored[:k]]


@lru_cache
def get_index() -> BM25Index:
    return BM25Index(_load_chunks())


def search(query: str, scope: str = "service_info", k: int = 4) -> list[dict]:
    return [
        {"id": c.id, "source": f"{c.title} › {c.heading}", "url": c.url, "text": c.text}
        for c in get_index().search(query, scope, k)
    ]

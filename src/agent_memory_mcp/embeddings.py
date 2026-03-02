from __future__ import annotations

from dataclasses import dataclass

import httpx

from agent_memory_mcp.vector_index import SimpleVectorIndex


class Embedder:
    backend_name: str

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError


@dataclass
class HashEmbedder(Embedder):
    dimensions: int = 256
    backend_name: str = "hash"

    def __post_init__(self) -> None:
        self._index = SimpleVectorIndex(dimensions=self.dimensions)

    def embed(self, text: str) -> list[float]:
        return self._index.embed(text)


@dataclass
class OpenAIEmbedder(Embedder):
    api_key: str
    model: str = "text-embedding-3-small"
    timeout_seconds: float = 30.0
    backend_name: str = "openai"

    def embed(self, text: str) -> list[float]:
        payload = {"input": text, "model": self.model}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post("https://api.openai.com/v1/embeddings", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        return list(data["data"][0]["embedding"])


def build_embedder(backend: str, openai_api_key: str | None, openai_model: str) -> Embedder:
    normalized = (backend or "hash").strip().lower()
    if normalized == "openai":
        if not openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when AGENT_MEMORY_EMBEDDING_BACKEND=openai")
        return OpenAIEmbedder(api_key=openai_api_key, model=openai_model)
    return HashEmbedder()

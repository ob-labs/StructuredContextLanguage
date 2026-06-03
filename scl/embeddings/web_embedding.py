# pip install openai

"""
File path:
scl/embedding/web_embedding.py

Generic OpenAI‑compatible embedding client (used as the final fallback).
Does NOT maintain a local cache – that is handled by the coordinator.
"""

import time

from openai import OpenAI

from scl.embeddings.base_embedding import BaseEmbeddingClient
from scl.otel.otel import tracer

try:
    from scl.config import config
except ImportError:

    class ConfigFallback:
        embedding_model = "BAAI/bge-m3"
        embedding_model_dims = 1024
        embedding_api_key = "your-api-key"
        embedding_base_url = "https://api.siliconflow.cn/v1"

    config = ConfigFallback()


class WebEmbeddingClient(BaseEmbeddingClient):
    """Singleton web embedding client (OpenAI‑compatible)."""

    def _init_subclass(self):
        self.model = getattr(config, "embedding_model", "BAAI/bge-m3")
        self.embedding_dims = getattr(config, "embedding_model_dims", 1024)
        api_key = getattr(config, "embedding_api_key", "your-api-key")
        base_url = getattr(config, "embedding_base_url", "https://api.siliconflow.cn/v1")

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.supports_dimensions = "openai.com" in base_url.lower()

        self.embed_counter = self._meter.create_counter(
            "web_embedding_requests_total",
            description="Total number of web embedding API calls (not cache hits)",
        )
        self.logger.info(
            "WebEmbeddingClient initialized (model=%s, base_url=%s)", self.model, base_url
        )

    @tracer.start_as_current_span("embed")
    def embed(self, text):
        """Call the web API and return the embedding vector."""
        time.sleep(5)  # rate limit / timeout guard
        self.logger.info("Web embedding request (text length: %d)", len(text))
        self.logger.debug("Full text: %s", text)

        text_clean = text.replace("\n", " ")
        params = {"input": [text_clean], "model": self.model}
        if self.supports_dimensions:
            params["dimensions"] = int(self.embedding_dims)

        result = self.client.embeddings.create(**params).data[0].embedding
        self.embed_counter.add(1, {"model": self.model})
        self.logger.debug("Web embedding successful")
        return result

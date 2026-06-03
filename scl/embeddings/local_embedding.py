# pip install sentence-transformers numpy

"""
File path:
scl/embedding/local_embedding.py

Features and design goals
- Singleton local embedding client using SentenceTransformer.
- Configuration via `scl.config` for model path.
- OpenTelemetry tracing, metrics, and logging.
"""

from functools import lru_cache

from scl.embeddings.base_embedding import BaseEmbeddingClient
from scl.otel.otel import tracer

try:
    from scl.config import config
except ImportError:

    class ConfigFallback:
        embedding_local_model_path = "/path/to/bge-m3"

    config = ConfigFallback()


class LocalEmbeddingClient(BaseEmbeddingClient):
    def _init_subclass(self):
        # Imported lazily: the local backend is an optional extra (`pip install .[local]`).
        from sentence_transformers import SentenceTransformer

        model_path = getattr(config, "embedding_local_model_path", "/path/to/bge-m3")
        self.logger.debug("Initializing LocalEmbeddingClient with model: %s", model_path)
        self.model = SentenceTransformer(model_path)
        self.request_counter = self._meter.create_counter(
            "local_embedding_requests", description="Total number of local embedding requests"
        )
        self.logger.info("LocalEmbeddingClient initialized")

    @tracer.start_as_current_span("embed")
    def embed(self, sentence: str):
        self.logger.debug("Embedding %s sentence", sentence)
        embedding = self.model.encode(sentence)
        self.request_counter.add(1, {"status": "success"})
        return embedding


# Convenience functions for direct use of local client
@lru_cache(maxsize=1)
def get_local_embedding_client():
    return LocalEmbeddingClient()


def local_embed(text):
    return get_local_embedding_client().embed(text)

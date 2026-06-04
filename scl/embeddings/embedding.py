# pip install openai sentence-transformers numpy  (dependencies for the backends)

"""
File path:
scl/embedding/embedding.py

Unified embedding function with priority:
  1. Check cache (persistent JSON file).
  2. Try local embedding (SentenceTransformer) if configured.
  3. Fall back to web API (OpenAI‑compatible) if configured.
All results are stored in the shared cache.
"""

import logging

from opentelemetry import trace

# Optional backends — imported lazily in CompositeEmbedding.__init__
# to avoid hard failures when optional dependencies are missing.
from scl.otel.otel import meter, tracer

try:
    from scl.config import config
except ImportError:
    # Fallback – all backends disabled
    class ConfigFallback:
        pass

    config = ConfigFallback()


class CompositeEmbedding:
    """Singleton coordinator that chooses the best backend for each request."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.logger = logging.getLogger(__name__)
        self._embedding_cache_path = hasattr(config, "embedding_cache_path")
        self.cache = None
        # if self._embedding_cache_path:
        #    self.cache = EmbeddingCache(cache_file=config.embedding_cache_path)
        # Backends are lazy‑loaded – only if config provides the required settings.
        # We use getattr + bool check so a default-None attribute doesn't activate
        # a backend that lacks a real configuration value.
        self._local_available = bool(getattr(config, "embedding_local_model_path", None))
        self._web_available = bool(
            getattr(config, "embedding_base_url", None)
        )
        if self._local_available:
            from scl.embeddings.local_embedding import LocalEmbeddingClient

            self.local_client = LocalEmbeddingClient()
            self.logger.info("Local embedding backend enabled")
        if self._web_available:
            from scl.embeddings.web_embedding import WebEmbeddingClient

            # openai.OpenAIError: The api_key client option must be set either by passing api_key to the client or by setting the OPENAI_API_KEY environment variable
            self.web_client = WebEmbeddingClient()
            self.logger.info("Web embedding backend enabled")

        self._counter = meter.create_counter(
            "composite_embedding_requests",
            description="Total composite embedding requests (cache hits + computations)",
        )
        self._initialized = True

    @tracer.start_as_current_span("embed")
    def embed(self, text):
        """Return the embedding for `text` using the configured priority."""
        current_span = trace.get_current_span()
        if current_span:
            current_span.set_attribute("text_length", len(text))
        # 1. Check cache
        if self.cache is not None:
            cached = self.cache.get(text)
            if cached is not None:
                self.logger.debug("Cache hit for text: %s...", text[:50])
                self._counter.add(1, {"source": "cache"})
            return cached

        # 2. Try local embedding
        if self._local_available:
            self.logger.info("Cache miss – using local embedding")
            try:
                embedding = self.local_client.embed(text)
                # Save single vector (already extracted) into cache
                # self.cache.set(text, embedding.tolist() if hasattr(embedding, 'tolist') else embedding)
                self._counter.add(1, {"source": "local"})
                return embedding
            except Exception as e:
                self.logger.warning("Local embedding failed: %s, falling back to web", e)

        # 3. Fall back to web API
        if self._web_available:
            self.logger.info("Using web API for embedding")
            embedding = self.web_client.embed(text)
            #self.cache.set(text, embedding)
            self._counter.add(1, {"source": "web"})
            return embedding

        raise RuntimeError("No embedding backend available – configure local model or web API.")


def get_embedding_client():
    return CompositeEmbedding()


def embed(text):
    return get_embedding_client().embed(text)


"""
Example usage:
    from scl.embedding.embedding import embed

    # All requests follow the 1‑2‑3 priority
    vector = embed("What is the capital of France?")
    # Subsequent calls with the same text hit the cache instantly
    vector2 = embed("What is the capital of France?")
    assert vector == vector2
"""

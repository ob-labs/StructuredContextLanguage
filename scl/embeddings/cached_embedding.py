"""
File path:
scl/embedding/cached_embedding.py

Features and design goals
- Singleton embedding client using SiliconFlow's embedding API (https://api.siliconflow.cn/v1/embeddings).
- Configuration via `scl.config` for model name, API key, and base URL.
- Local JSON cache file (`./embedding_cache.json`) to store and reuse embeddings.
- Cache key = raw input text, cache value = the embedding vector (list of floats).
- On cache miss, the text is sent to the SiliconFlow API.
- Retry logic for 4xx HTTP errors: waits 60 seconds and retries once.
- OpenTelemetry tracing: each `embed` call is wrapped in a span named "embed".
- Metrics: an OTel counter increments for every successful API call (cache hits are not counted).
- Logging: info level for each request, debug level for internal details (class-level logger).
- Global convenience functions `get_embedding_client` (cached via `lru_cache`) and `embed` for easy import and use.
- Singleton property: only one `CachedEmbedding` instance exists across the application.
"""

import json
import logging
import os
import time
import requests
from scl.otel.otel import tracer, meter
from functools import lru_cache
from scl.config import config

class CachedEmbedding:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self.model = config.embedding_model
        self.api_key = config.embedding_api_key
        self.endpoint = config.embedding_base_url   # e.g., "https://api.siliconflow.cn/v1/embeddings"
        self.cache_file = "/Users/yuanyi/OpenSource/OBTest/StructuredContextLanguage/embeddings_cache.json"
        
        # Logger must be initialised before any logging happens (e.g., in _load_cache)
        self.logger = logging.getLogger(__name__)
        
        # Load existing cache from disk (if any)
        self._load_cache()
        
        # Counter: each successful API call (not cache hits)
        self.embed_counter = meter.create_counter(
            "cached_embedding_requests_total",
            description="Total number of embedding API calls to SiliconFlow (cache misses)"
        )
        
        self._initialized = True

    def _load_cache(self):
        """Load the embedding cache from the JSON file."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self.cache = json.load(f)
                self.logger.warning("Cache loaded from disk.")
            except Exception as e:
                self.logger.error(f"Failed to load embedding cache: {e}")
                self.cache = {}
        else:
            self.cache = {}
            self.logger.warning("No cache file found, starting with empty cache.")

    def _save_cache(self):
        """Save the current embedding cache to the JSON file."""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
            self.logger.debug("Cache saved to disk.")
        except Exception as e:
            self.logger.error(f"Failed to save embedding cache: {e}")

    @tracer.start_as_current_span("embed")
    def embed(self, text):
        """
        Get the embedding for the given text. Uses local cache if available,
        otherwise fetches from SiliconFlow API with retry on 4xx errors.

        Args:
            text (str): The text to embed.
        Returns:
            list: The embedding vector (list of floats).
        """
        # Check cache first
        if text in self.cache:
            self.logger.debug(f"Cache hit for text: {text[:50]}...")
            return self.cache[text]
        
        # Cache miss – log at info level
        self.logger.warning(f"Embedding text (cache miss): {text[:100]}...")
        self.logger.debug(f"Full embedding request text: {text}")
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "input": text
        }
        
        max_retries = 1
        retry_count = 0
        
        while retry_count <= max_retries:
            try:
                response = requests.post(
                    self.endpoint,
                    json=payload,
                    headers=headers,
                    timeout=30
                )
                
                if response.status_code == 200:
                    data = response.json()
                    # SiliconFlow returns OpenAI‑compatible format:
                    # {"data": [{"embedding": [...], "index": 0, "object": "embedding"}], ...}
                    embedding = data["data"][0]["embedding"]
                    
                    # Save to cache
                    self.cache[text] = embedding
                    self._save_cache()
                    
                    # Increment metric (only API calls)
                    self.embed_counter.add(1, {"model": self.model})
                    self.logger.debug("Embedding fetched from SiliconFlow API and cached.")
                    return embedding
                    
                elif 400 <= response.status_code < 500:
                    # 4xx error – wait and retry if retries remain
                    if retry_count < max_retries:
                        self.logger.warning(
                            f"Received {response.status_code} error, retrying in 60s... ({response.text})"
                        )
                        time.sleep(60)
                        retry_count += 1
                    else:
                        self.logger.error(
                            f"Failed after retries: {response.status_code}, {response.text}"
                        )
                        raise Exception(
                            f"SiliconFlow API error: {response.status_code}, {response.text}"
                        )
                else:
                    # Other errors (5xx, etc.) – raise immediately
                    self.logger.error(
                        f"API request failed: {response.status_code}, {response.text}"
                    )
                    raise Exception(
                        f"SiliconFlow API error: {response.status_code}, {response.text}"
                    )
                    
            except requests.exceptions.RequestException as e:
                if retry_count < max_retries:
                    self.logger.warning(f"Request exception: {e}, retrying in 60s...")
                    time.sleep(60)
                    retry_count += 1
                else:
                    self.logger.error(f"Request failed after retries: {e}")
                    raise

        # Should never reach here
        raise Exception("Exhausted all retries without a successful response.")


@lru_cache(maxsize=1)
def get_embedding_client():
    """获取带缓存的嵌入客户端（单例，通过 lru_cache 保证）"""
    return CachedEmbedding()


def embed(text):
    """全局嵌入函数，自动使用本地缓存和 SiliconFlow API"""
    client = get_embedding_client()
    return client.embed(text)


"""
Example usage:
    # 1. Direct import and use of the global embed function
    from scl.embedding.cached_embedding import embed
    vector = embed("Hello world")  # fetches from API or cache

    # 2. Access the singleton client for custom needs
    from scl.embedding.cached_embedding import CachedEmbedding
    client = CachedEmbedding()
    vector = client.embed("Some text")

    # 3. Verify singleton behaviour
    assert CachedEmbedding() is CachedEmbedding()

    # 4. Ensure configuration is set in scl.config:
    #    - embedding_model       (e.g., "BAAI/bge-m3")
    #    - embedding_api_key     (your SiliconFlow API key)
    #    - embedding_base_url    ("https://api.siliconflow.cn/v1/embeddings")
"""
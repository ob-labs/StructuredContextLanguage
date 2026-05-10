"""
File path:
scl/embedding/openai_embedding.py

Features and design goals
- Singleton OpenAI embedding client using the OpenAI Python SDK.
- Configuration via `scl.config` for model name, API key, base URL, and embedding dimensions.
- Conditional inclusion of the `dimensions` parameter: enabled only for providers that support it (e.g., OpenAI), omitted for others (e.g., SiliconFlow).
- OpenTelemetry tracing: each `embed` call is traced under the span "embed".
- Metrics: an OTel counter tracks the total number of embedding requests.
- Logging: info-level log for each request, debug-level logs for internal details (via a class-level logger).
- Global convenience functions `get_embedding_client` and `embed` for easy, cached access.
- Singleton ensures only one client instance is used across the application.
- Lightweight caching of the client instance using `lru_cache` to avoid repeated initialisation.
"""

import os
import time
import logging
from openai import OpenAI
from scl.otel.otel import tracer, meter
from functools import lru_cache
from scl.config import config

class OpenAIEmbedding:
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
        self.embedding_dims = config.embedding_model_dims

        api_key = config.embedding_api_key
        base_url = config.embedding_base_url

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        # Check if API supports dimensions parameter (OpenAI supports it, SiliconFlow doesn't)
        self.supports_dimensions = "openai.com" in base_url.lower()
        
        # Logger for this class
        self.logger = logging.getLogger(__name__)
        
        # Metric counter for embed requests
        self.embed_counter = meter.create_counter(
            "embedding_requests_total",
            description="Total number of embedding API calls"
        )
        
        self._initialized = True

    @tracer.start_as_current_span("embed")
    def embed(self, text):
        """
        Get the embedding for the given text using the configured OpenAI-compatible API.

        Args:
            text (str): The text to embed.
        Returns:
            list: The embedding vector.
        """
        time.sleep(5)  # deliberate delay to avoid hitting rate limits / timeouts
        self.logger.info(f"Embedding text: {text[:100]}...")  # truncate to avoid huge logs
        self.logger.debug(f"Full embedding request text: {text}")
        text = text.replace("\n", " ")
        
        # Build parameters - only include dimensions if API supports it
        params = {
            "input": [text],
            "model": self.model
        }
        if self.supports_dimensions:
            params["dimensions"] = int(self.embedding_dims)
        
        # Call API and increment counter
        result = self.client.embeddings.create(**params).data[0].embedding
        self.embed_counter.add(1, {"model": self.model})
        self.logger.debug("Embedding retrieved successfully")
        return result

# 创建全局函数
@lru_cache(maxsize=1)
def get_embedding_client():
    """获取嵌入客户端（带缓存）"""
    return OpenAIEmbedding()

def embed(text):
    """全局嵌入函数"""
    client = get_embedding_client()
    return client.embed(text)

"""
Example usage:
    # 1. Simplest usage: import and call the global embed function
    from scl.embedding.openai_embedding import embed
    vector = embed("Hello world")

    # 2. Access the singleton client directly (e.g., for testing or custom logic)
    from scl.embedding.openai_embedding import OpenAIEmbedding
    client = OpenAIEmbedding()
    vector = client.embed("Some text")

    # 3. The singleton property – both methods return the same instance
    assert OpenAIEmbedding() is OpenAIEmbedding()

    # 4. Configuration is read from scl.config; ensure your config files define:
    #    - embedding_model
    #    - embedding_model_dims
    #    - embedding_api_key
    #    - embedding_base_url
"""
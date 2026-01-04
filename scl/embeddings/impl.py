import os
import time
import logging
from openai import OpenAI
from scl.trace import tracer
from functools import lru_cache

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
            
        self.model = os.getenv("EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5")
        self.embedding_dims = os.getenv("EMBEDDING_MODEL_DIMS", 1024)

        api_key = os.getenv("EMBEDDING_API_KEY")
        base_url = os.getenv("EMBEDDING_BASE_URL", "https://api.siliconflow.cn/v1")

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        # Check if API supports dimensions parameter (OpenAI supports it, SiliconFlow doesn't)
        self.supports_dimensions = "openai.com" in base_url.lower()
        self._initialized = True

    @tracer.start_as_current_span("embed")
    def embed(self, text):
        """
        Get the embedding for the given text using OpenAI.

        Args:
            text (str): The text to embed.
        Returns:
            list: The embedding vector.
        """
        time.sleep(5)  # to avoid timeout
        logging.info(f"Embedding text: {text}")
        text = text.replace("\n", " ")
        
        # Build parameters - only include dimensions if API supports it
        params = {
            "input": [text],
            "model": self.model
        }
        if self.supports_dimensions:
            params["dimensions"] = int(self.embedding_dims)
        
        return self.client.embeddings.create(**params).data[0].embedding

# 创建全局函数
@lru_cache(maxsize=1)
def get_embedding_client():
    """获取嵌入客户端（带缓存）"""
    return OpenAIEmbedding()

def embed(text):
    """全局嵌入函数"""
    client = get_embedding_client()
    return client.embed(text)

# 可以直接导入和使用
# from your_module import embed
# result = embed("hello world")
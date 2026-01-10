import os
from typing import Any, Optional
from dataclasses import dataclass


@dataclass
class Config:
    """使用dataclass的极简配置类"""
    
    # 直接从环境变量获取值, something ground truth won't change
    otlp_endpoint: str = os.getenv("OTLP_ENDPOINT", "http://localhost:4318")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5")
    embedding_model_dims: int = int(os.getenv("EMBEDDING_MODEL_DIMS", "1024"))
    embedding_api_key: Optional[str] = os.getenv("EMBEDDING_API_KEY")
    embedding_base_url: str = os.getenv("EMBEDDING_BASE_URL", "https://api.siliconflow.cn/v1")

    ## todo, vars here may changes
    limit: int = int(os.getenv("LIMIT", "5"))
    min_similarity: float = float(os.getenv("MIN_SIMILARITY", "0.5"))
    
    @property
    def has_api_key(self) -> bool:
        return bool(self.embedding_api_key)
    
    def validate(self) -> bool:
        """简单的验证"""
        if not self.otlp_endpoint.startswith(('http://', 'https://')):
            raise ValueError(f"Invalid OTLP endpoint: {self.otlp_endpoint}")
        return True

config = Config()

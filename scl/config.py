import os
from dataclasses import dataclass


@dataclass
class Config:
    """使用dataclass的极简配置类"""

    # otel related settings
    otlp_endpoint: str = os.getenv("OTLP_ENDPOINT", "http://localhost:4318")
    otlp_metrics_endpoint: str = os.getenv(
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "http://localhost:4318"
    )
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    service_name: str = os.getenv("SERVICE_NAME", "SCL")

    # 文件监听目录
    todo_watch_dir = os.getenv("TODO_WATCH_DIR", "./todo_folder")
    # REST API 监听配置
    api_host = os.getenv("API_HOST", "0.0.0.0")
    api_port = os.getenv("API_PORT", "8080")

    # embedding related settings
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    embedding_model_dims: int = int(os.getenv("EMBEDDING_MODEL_DIMS", "1024"))
    embedding_api_key: str | None = os.getenv("EMBEDDING_API_KEY")
    embedding_base_url: str = os.getenv("EMBEDDING_BASE_URL", "https://api.siliconflow.cn/v1")
    embedding_local_model_path: str | None = os.getenv("EMBEDDING_LOCAL_MODEL_PATH")
    embedding_cache_path: str | None = os.getenv("EMBEDDING_CACHE_PATH")

    ## todo, vars here may changes
    limit: int = int(os.getenv("LIMIT", "5"))
    min_similarity: float = float(os.getenv("MIN_SIMILARITY", "0.5"))

    @property
    def has_api_key(self) -> bool:
        return bool(self.embedding_api_key)

    def validate(self) -> bool:
        """简单的验证"""
        if not self.otlp_endpoint.startswith(("http://", "https://")):
            raise ValueError(f"Invalid OTLP endpoint: {self.otlp_endpoint}")
        return True


config = Config()

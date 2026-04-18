"""
When running in test, this module should ensure test script can be run without otel features.
"""
import os
import logging
from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.instrumentation.logging import LoggingInstrumentor

# OTLP gRPC Log Exporter
try:
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    OTLP_GRPC_LOG_AVAILABLE = True
except ImportError:
    OTLP_GRPC_LOG_AVAILABLE = False
    logging.warning("OTLP gRPC log exporter not available, logs will only go to console")

# 尝试导入配置对象 (如果存在)
try:
    from scl.config import config
    HAS_CONFIG = True
except ImportError:
    HAS_CONFIG = False
    config = None

# 服务名称
SERVICE_NAME_VALUE = "SCL"

def get_otlp_endpoint() -> str:
    """获取 OTLP 端点地址，优先从 config 读取，否则从环境变量读取"""
    if HAS_CONFIG and hasattr(config, 'otlp_endpoint') and config.otlp_endpoint:
        return config.otlp_endpoint
    return os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

def setup_logs():
    """初始化 Logs"""
    resource = Resource(attributes={SERVICE_NAME: SERVICE_NAME_VALUE})
    otlp_endpoint = get_otlp_endpoint()
    
    logger_provider = LoggerProvider(resource=resource)
    set_logger_provider(logger_provider)
    
    if OTLP_GRPC_LOG_AVAILABLE:
        # 注意: gRPC 导出器通常使用 4317 端口，如果用户配置的是 4318，尝试替换端口
        log_endpoint = otlp_endpoint.replace(":4318", ":4317") if ":4318" in otlp_endpoint else otlp_endpoint
        log_exporter = OTLPLogExporter(endpoint=log_endpoint, insecure=True)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    else:
        logging.warning("OTLP log exporter unavailable, logs will not be exported to collector")

    # 将 Python logging 桥接到 OpenTelemetry
    LoggingInstrumentor().instrument(set_logging_format=True)
    handler = LoggingHandler(logger_provider=logger_provider)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(os.getenv("LOG_LEVEL", "INFO"))
    
    return logger_provider

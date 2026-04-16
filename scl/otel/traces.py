import os
import logging
from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

# OTLP HTTP Trace Exporter
try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    OTLP_HTTP_AVAILABLE = True
except ImportError:
    OTLP_HTTP_AVAILABLE = False
    logging.warning("OTLP HTTP exporter not available, falling back to console for traces")

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

def setup_traces():
    """初始化 Traces"""
    resource = Resource(attributes={SERVICE_NAME: SERVICE_NAME_VALUE})
    otlp_endpoint = get_otlp_endpoint()
    
    tracer_provider = TracerProvider(resource=resource)
    if OTLP_HTTP_AVAILABLE:
        span_exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")
    else:
        span_exporter = ConsoleSpanExporter()
    
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(tracer_provider)
    
    # 返回 Tracer
    return trace.get_tracer(__name__)

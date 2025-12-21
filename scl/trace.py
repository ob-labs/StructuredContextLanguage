import os
import logging

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)

# Try to import OTLP exporter, fallback to console if not available
try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    OTLP_AVAILABLE = True
except ImportError:
    OTLP_AVAILABLE = False
    logging.info("OTLP exporter not available, falling back to console exporter")

# 创建TracerProvider
provider = TracerProvider()

# Use OTLP exporter if available, otherwise use console exporter
if OTLP_AVAILABLE:
    OTLP_ENDPOINT = os.getenv("OTLP_ENDPOINT", "http://localhost:4318")  # 注意：默认端口通常是4318而不是4317
    span_exporter = OTLPSpanExporter(
        endpoint=f"{OTLP_ENDPOINT}/v1/traces"
    )
else:
    span_exporter = ConsoleSpanExporter()

# 创建并添加span processor
span_processor = BatchSpanProcessor(span_exporter)
provider.add_span_processor(span_processor)

# 设置全局tracer provider
trace.set_tracer_provider(provider)

# 创建tracer实例 - 建议使用模块名或应用名
tracer = trace.get_tracer("scl")

# 测试函数，验证tracer是否工作
#def test_tracing():
#    """测试trace是否正常工作"""
#    with tracer.start_as_current_span("test_operation") as span:
#        span.set_attribute("test.key", "test.value")
#        print("Tracing is working! Check your exporter for traces.")
#        return "Trace created successfully"

# 如果需要立即测试，可以取消注释下面的行
# test_tracing()
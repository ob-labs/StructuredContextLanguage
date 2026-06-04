"""
When running in test, this module should ensure test script can be run without otel features.
"""

import logging

from opentelemetry._logs import set_logger_provider
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource

# OTLP gRPC Log Exporter
try:
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter

    OTLP_GRPC_LOG_AVAILABLE = True
except ImportError:
    OTLP_GRPC_LOG_AVAILABLE = False
    logging.warning("OTLP gRPC log exporter not available, logs will only go to console")

# 导入配置对象
from scl.config import config

# 服务名称（可从 config 获取，若无则使用默认值）
SERVICE_NAME_VALUE = getattr(config, "service_name", "SCL")


def get_otlp_endpoint() -> str:
    """直接从 config 获取 OTLP 端点地址"""
    return config.otlp_endpoint


def setup_logs():
    """初始化 Logs"""
    resource = Resource(attributes={SERVICE_NAME: SERVICE_NAME_VALUE})
    otlp_endpoint = get_otlp_endpoint()

    logger_provider = LoggerProvider(resource=resource)
    set_logger_provider(logger_provider)

    if OTLP_GRPC_LOG_AVAILABLE:
        # gRPC 导出器通常使用 4317 端口，若配置的是 4318 则替换端口
        log_endpoint = (
            otlp_endpoint.replace(":4318", ":4317") if ":4318" in otlp_endpoint else otlp_endpoint
        )
        log_exporter = OTLPLogExporter(endpoint=log_endpoint, insecure=True)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    else:
        logging.warning("OTLP log exporter unavailable, logs will not be exported to collector")

    # 将 Python logging 桥接到 OpenTelemetry
    LoggingInstrumentor().instrument(set_logging_format=True)
    handler = LoggingHandler(logger_provider=logger_provider)
    logging.getLogger().addHandler(handler)

    # 日志级别直接从 config 获取
    logging.getLogger().setLevel(config.log_level)

    return logger_provider

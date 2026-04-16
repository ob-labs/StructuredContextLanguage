import os
import logging
from typing import Iterable
from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, ConsoleMetricExporter

# OTLP HTTP Metric Exporter
try:
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    OTLP_HTTP_AVAILABLE = True
except ImportError:
    OTLP_HTTP_AVAILABLE = False
    logging.warning("OTLP HTTP exporter not available, falling back to console for metrics")

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

def setup_metrics():
    """初始化 Metrics 并创建自定义指标"""
    
    resource = Resource(attributes={SERVICE_NAME: SERVICE_NAME_VALUE})
    otlp_endpoint = get_otlp_endpoint()
    
    # 初始化 Metrics
    if OTLP_HTTP_AVAILABLE:
        metric_exporter = OTLPMetricExporter(endpoint=f"{otlp_endpoint}/v1/metrics")
    else:
        metric_exporter = ConsoleMetricExporter()
    
    metric_reader = PeriodicExportingMetricReader(metric_exporter)
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)
    
    # 返回 Meter
    meter = metrics.get_meter(__name__)
    
    # ========== 应用特定指标 ==========
    # 直方图
    search_time_histogram = meter.create_histogram(
        name="cap_search_time",
        description="Time taken for search operations",
        explicit_bucket_boundaries_advisory=[1.0, 5.0, 10.0],
        unit="s"
    )
    
    tool_execute_time_histogram = meter.create_histogram(
        name="cap_execute_time",
        description="Time taken for cap execution",
        explicit_bucket_boundaries_advisory=[1.0, 5.0, 10.0],
        unit="s"
    )

    # 可观测计数器 (Gauge)
    cap_counts = {
        "search": 0,
        "total": 0,
        "duplicate": 0,
        "hit": 0,
    }

    task_enqueue_counter = meter.create_counter(
        "task_enqueue",
        description="Number of items added to the queue"
    )

    task_dequeue_counter = meter.create_counter(
        "task_dequeue",
        description="Number of items removed from the queue"
    )

    processed_counter = meter.create_counter(
        "todo_items_processed",
        description="Number of todo items processed"
    )

    def observable_cap_gauge_func(options: CallbackOptions) -> Iterable[Observation]:
        for key, value in cap_counts.items():
            yield Observation(value, {"type": f"cap_{key}_count"})

    cap_gauge = meter.create_observable_gauge(
        name="cap_gauge",
        callbacks=[observable_cap_gauge_func],
        description="gauge related with cap",
        unit="1"
    )

    # 返回指标对象
    return {
        "meter": meter,
        "search_time_histogram": search_time_histogram,
        "tool_execute_time_histogram": tool_execute_time_histogram,
        "cap_gauge": cap_gauge,
        "cap_counts": cap_counts,  # 外部可以修改此字典来更新 gauge
        "task_enqueue_counter": task_enqueue_counter,
        "task_dequeue_counter": task_dequeue_counter,
        "processed_counter": processed_counter,
    }

"""
OpenTelemetry 初始化模块
提供统一的入口来获取 Tracer、Meter 和预定义的指标对象。
真实遥测初始化请调用 init_telemetry()。
"""
import logging
from opentelemetry import trace, metrics

# ---------- 获取全局代理 Tracer / Meter ----------
# 此时尚未设置真实的 Provider，获取到的是 No-op 代理对象
tracer = trace.get_tracer(__name__)
meter = metrics.get_meter(__name__)

# ---------- 预创建常用指标对象（基于代理 Meter，安全且始终可用）----------
search_time_histogram = meter.create_histogram(
    name="search_time",
    description="Time taken for search operations",
    unit="ms"
)
tool_execute_time_histogram = meter.create_histogram(
    name="tool_execute_time",
    description="Time taken for tool execution",
    unit="ms"
)
cap_gauge = meter.create_gauge(
    name="cap_gauge",
    description="Current capacity gauge"
)
cap_counts = meter.create_counter(
    name="cap_counts",
    description="Count of capacity changes"
)

# 向后兼容：导出一个类似 metrics_result 的字典
metrics_result = {
    "meter": meter,
    "search_time_histogram": search_time_histogram,
    "tool_execute_time_histogram": tool_execute_time_histogram,
    "cap_gauge": cap_gauge,
    "cap_counts": cap_counts,
}

# ---------- 显式初始化函数 ----------
def init_telemetry():
    """
    初始化真实的 OpenTelemetry 提供者和导出器。
    应在应用启动时调用（例如在 main 函数、Django AppConfig.ready 或环境变量控制下调用）。
    测试环境中无需调用此函数。
    """
    from scl.otel.traces import setup_traces
    from scl.otel.metrics import setup_metrics
    from scl.otel.init import setup_logs

    # 注意：setup_traces 和 setup_metrics 内部应调用 trace.set_tracer_provider / metrics.set_meter_provider
    # 如果它们原来返回了一些对象，我们可以忽略或仅用于内部配置
    setup_traces()
    setup_metrics()   # 假设该函数内部只负责设置 MeterProvider，不再重复创建指标
    setup_logs()      # 你现有的日志初始化函数

    # 可选：如果 setup_metrics 返回了新创建的指标对象，可以更新全局变量以保证完全一致
    # 但由于全局变量已基于代理创建，Provider 切换后它们会自动生效，通常无需额外操作。

    logging.getLogger(__name__).info("OpenTelemetry initialized (real providers set).")
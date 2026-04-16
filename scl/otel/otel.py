"""
OpenTelemetry 初始化模块
提供统一的入口来初始化 Traces, Metrics, 和 Logs
"""

from scl.otel.traces import setup_traces
from scl.otel.metrics import setup_metrics
from scl.otel.init import setup_logs


def setup_telemetry():
    """
    初始化 OpenTelemetry (Traces, Metrics, Logs)
    
    Returns:
        tuple: (tracer, meter, metrics_dict)
            - tracer: OpenTelemetry Tracer 对象
            - meter: OpenTelemetry Meter 对象
            - metrics_dict: 包含所有自定义指标的字典
    """
    # 初始化 Traces
    tracer = setup_traces()
    
    # 初始化 Metrics
    metrics_result = setup_metrics()
    meter = metrics_result["meter"]
    
    # 初始化 Logs
    setup_logs()
    
    return tracer, meter, metrics_result


# 为了向后兼容，提供便捷的访问方式
tracer, meter, metrics_data = setup_telemetry()

# 向后兼容：从 metrics_data 中提取所有指标对象
search_time_histogram = metrics_data["search_time_histogram"]
tool_execute_time_histogram = metrics_data["tool_execute_time_histogram"]
cap_gauge = metrics_data["cap_gauge"]
cap_counts = metrics_data["cap_counts"]
task_enqueue_counter = metrics_data["task_enqueue_counter"]
task_dequeue_counter = metrics_data["task_dequeue_counter"]
processed_counter = metrics_data["processed_counter"]

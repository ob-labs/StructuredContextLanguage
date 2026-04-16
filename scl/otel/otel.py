"""
OpenTelemetry 初始化模块
提供统一的入口来初始化 Traces, Metrics, 和 Logs
"""

from scl.otel.traces import setup_traces
from scl.otel.metrics import setup_metrics
from scl.otel.init import setup_logs

# 初始化 Traces
tracer = setup_traces()
    
# 初始化 Metrics
metrics_result = setup_metrics()
meter = metrics_result["meter"]
    
# 初始化 Logs
setup_logs()

# 向后兼容：从 metrics_data 中提取所有指标对象
search_time_histogram = metrics_result["search_time_histogram"]
tool_execute_time_histogram = metrics_result["tool_execute_time_histogram"]
cap_gauge = metrics_result["cap_gauge"]
cap_counts = metrics_result["cap_counts"]

import time
from typing import Iterable

from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter,
)
from opentelemetry.metrics import (
    CallbackOptions,
    Observation,
    get_meter_provider,
    set_meter_provider,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

exporter = OTLPMetricExporter()
reader = PeriodicExportingMetricReader(exporter)
provider = MeterProvider(metric_readers=[reader])
set_meter_provider(provider)

test_value = 9

def observable_counter_func(options: CallbackOptions) -> Iterable[Observation]:
    yield Observation(1, {})


def observable_up_down_counter_func(
    options: CallbackOptions,
) -> Iterable[Observation]:
    yield Observation(-10, {})


def observable_gauge_func(options: CallbackOptions) -> Iterable[Observation]:
    yield Observation(test_value, {})


meter = get_meter_provider().get_meter("getting-started", "0.1.2")

# Counter
counter = meter.create_counter("counter")
counter.add(1)

# Async Counter
observable_counter = meter.create_observable_counter(
    "observable_counter",
    [observable_counter_func],
)

# UpDownCounter
updown_counter = meter.create_up_down_counter("updown_counter")
updown_counter.add(1)
updown_counter.add(-5)

# Async UpDownCounter
observable_updown_counter = meter.create_observable_up_down_counter(
    "observable_updown_counter", [observable_up_down_counter_func]
)

# Histogram
histogram = meter.create_histogram("histogram")
histogram.record(99.9)


# Histogram with explicit bucket boundaries advisory
histogram = meter.create_histogram(
    "histogram_with_advisory",
    explicit_bucket_boundaries_advisory=[0.0, 1.0, 2.0],
)
histogram.record(99.9)
histogram.record(99)
histogram.record(98)

# Async Gauge
gauge = meter.create_observable_gauge("gauge", [observable_gauge_func])

test_value = 100

time.sleep(30)


import functools
from typing import Callable, Any
import random

def record_latency(histogram_name: str = "request_duration_seconds"):
    """记录函数执行时间的装饰器"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            # 获取直方图（假设在闭包或全局可访问）
            # 或者可以传递histogram对象
            start_time = time.perf_counter()
            
            try:
                result = func(*args, **kwargs)
                print(f"Function {func.__name__} returned {result}")
                return result
            finally:
                end_time = time.perf_counter()
                duration = end_time - start_time
                
                # 从meter获取或使用全局histogram
                hist = meter.create_histogram(
                    histogram_name,
                    explicit_bucket_boundaries_advisory=[1.0, 5.0, 10.0]
                )
                hist.record(duration, {"function": func.__name__})
        
        return wrapper
    return decorator

# 使用装饰器
@record_latency("api_call_duration")
def call_external_api(api_name: str):
    time.sleep(random.uniform(0.5, 8.0))
    return f"Response from {api_name}"

# 调用
result = call_external_api("user_service")
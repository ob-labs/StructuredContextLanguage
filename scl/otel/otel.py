import os
import logging
from typing import Iterable

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)

from opentelemetry import metrics
from opentelemetry.sdk.metrics import (
    MeterProvider,
)
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from scl.config import config

# Try to import OTLP exporters, fallback to console if not available
try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    OTLP_AVAILABLE = True
except ImportError:
    OTLP_AVAILABLE = False
    logging.info("OTLP exporter not available, falling back to console exporter")

# 创建TracerProvider
provider = TracerProvider()

# Use OTLP exporter if available, otherwise use console exporter
if OTLP_AVAILABLE:
    OTLP_ENDPOINT = config.otlp_endpoint
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

# Use OTLP exporter for metrics if available, otherwise use console exporter
if OTLP_AVAILABLE:
    metric_exporter = OTLPMetricExporter()
else:
    metric_exporter = ConsoleMetricExporter()

metric_reader = PeriodicExportingMetricReader(metric_exporter)
provider = MeterProvider(metric_readers=[metric_reader])

# Sets the global default meter provider
metrics.set_meter_provider(provider)

# Creates a meter from the global meter provider
meter = metrics.get_meter("scl")

## a key value map
## map
### key as str in name,desc format
### value as object
### value can be a number for gauge
### value can be a histogram

# Define metrics
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

# Dictionary to store counts
cap_counts = {
    "search": 0,
    "total": 0,
    "duplicate": 0,
    "hit": 0,
}

def observable_cap_gauge_func(options: CallbackOptions) -> Iterable[Observation]:
    # Loop through the dictionary and yield observations
    for key, value in cap_counts.items():
        yield Observation(value, {"type": " cap " + key + " count "})

cap_gauge = meter.create_observable_gauge(
    name="cap_gauge",
    callbacks=[observable_cap_gauge_func],
    description="gauge related with cap",
    unit="1"
)

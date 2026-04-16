import os
import logging
import time
import random
from typing import Iterable

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, ConsoleMetricExporter

# 配置基础日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

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
    if HAS_CONFIG and hasattr(config, 'otlp_metrics_endpoint') and config.otlp_metrics_endpoint:
        return config.otlp_metrics_endpoint
    return os.getenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "http://localhost:4318")

def setup_metrics():
    """初始化 Metrics 并创建自定义指标"""
    logging.info("init metrics for SCL")
    resource = Resource(attributes={SERVICE_NAME: SERVICE_NAME_VALUE})
    otlp_endpoint = get_otlp_endpoint()
    # 初始化 Metrics
    if OTLP_HTTP_AVAILABLE:
        metric_exporter = OTLPMetricExporter(
            endpoint=f"{otlp_endpoint}",
            #endpoint="http://localhost:9090/api/v1/otlp/v1/metrics",
            headers={}
        )
            #endpoint=f"{otlp_endpoint}/v1/metrics")
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
    }


# ==================== 调试主函数 ====================
def main():
    """
    调试入口：初始化 metrics，模拟业务操作并持续运行一段时间，
    以便观察指标是否正确导出（控制台输出或 OTLP 接收）。
    """
    logging.info("正在初始化 Metrics...")
    metrics_dict = setup_metrics()
    
    # 解包指标对象
    search_hist = metrics_dict["search_time_histogram"]
    execute_hist = metrics_dict["tool_execute_time_histogram"]
    cap_counts = metrics_dict["cap_counts"]
    
    logging.info("Metrics 初始化完成，开始模拟数据...")
    logging.info(f"OTLP 端点: {get_otlp_endpoint()}")
    logging.info("提示：程序将运行 60 秒，期间会持续更新指标。按 Ctrl+C 可提前终止。")

    # 模拟循环：不断更新指标
    start_time = time.time()
    iteration = 0
    try:
        while time.time() - start_time < 60:
            iteration += 1
            
            # 1. 模拟搜索耗时 (直方图)
            search_duration = random.uniform(0.5, 8.0)
            search_hist.record(search_duration)
            
            # 2. 模拟执行耗时
            execute_duration = random.uniform(0.2, 6.0)
            execute_hist.record(execute_duration)
            
            # 3. 更新 Gauge 字典 (cap_counts)
            cap_counts["search"] = random.randint(0, 100)
            cap_counts["total"] = cap_counts["search"] + random.randint(0, 50)
            cap_counts["duplicate"] = random.randint(0, 20)
            cap_counts["hit"] = random.randint(0, cap_counts["search"])
            
            # 4. 计数器增量
            processed_ctr.add(random.randint(1, 10))
            
            if iteration % 10 == 0:
                logging.info(
                    f"迭代 {iteration}: "
                    f"search={cap_counts['search']}, total={cap_counts['total']}, "
                    f"duplicate={cap_counts['duplicate']}, hit={cap_counts['hit']}"
                )
            
            # 模拟业务间隔
            time.sleep(2)
            
    except KeyboardInterrupt:
        logging.info("调试被用户中断。")
    
    logging.info("调试结束，Metrics 导出周期为 60 秒，请等待最后一个周期输出。")
    # 给导出器一点时间完成最后一次导出
    time.sleep(5)


if __name__ == "__main__":
    main()
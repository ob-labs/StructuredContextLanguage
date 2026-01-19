import functools
from typing import Callable, Any

def record_latency(histogram, count):
    """记录函数执行时间的装饰器"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            # 获取直方图（假设在闭包或全局可访问）
            # 或者可以传递histogram对象
            start_time = time.perf_counter()
            
            try:
                result = func(*args, **kwargs)
                #print(f"Function {func.__name__} returned {result}")
                # todo
                if count is not None:
                    count = len(result)
                return result
            finally:
                end_time = time.perf_counter()
                duration = end_time - start_time
                histogram.record(duration, {"function": func.__name__})
        
        return wrapper
    return decorator
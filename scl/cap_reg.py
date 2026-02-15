import sys
import os
import logging
from typing import List, Dict
from scl.meta.capability import Capability
from scl.otel.metric_decorator import record_latency
from scl.otel.otel import search_time_histogram, tool_execute_time_histogram

# Add the StructuredContextLanguage directory to the path
scl_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(scl_root)
from scl.otel.otel import tracer
from scl.storage.base import StoreBase
from scl.meta.msg import Msg

class CapRegistry:
    def __init__(self, StoreBase: StoreBase):
        """
        Initialize the CapRegistry with any StoreBase implementation
        
        Args:
            StoreBase: An instance of any StoreBase implementation
        """
        self.cap_store = StoreBase
    
    ## RAG search between context and function description after embedding
    ## Return function in openAI tool format
    ## the only diff between this class and basestore are getCapsByNames and call_cap_safe?
    @tracer.start_as_current_span("getCapsByNames")
    def getCapsByNames(self, ToolNames: List[str]) -> Dict[str,Capability]:
        functions = {}
        if self.cap_store is None:
            logging.info("Database not initialized. Cannot perform similarity search.")
            return {}
        for tool_name in ToolNames:
            logging.info(f"Searching for function: {tool_name}")
            function = self.get_cap_by_name(tool_name)
            logging.info(f"Function: {function}")
            if function:
                functions[tool_name]=function
        return functions
    
    ## make this class fits basestore interface
    @tracer.start_as_current_span("getCapsByName")
    def get_cap_by_name(self, name)-> Capability:
        return self.cap_store.get_cap_by_name(name)

    ## RAG search between context and function description after embedding
    ## Return function in openAI tool format
    @tracer.start_as_current_span("getCapsBySimilarity")
    @record_latency(search_time_histogram, "search")
    def getCapsBySimilarity(self, msg: Msg, limit=5, min_similarity=0.5) -> Dict[str, Capability]:
        return self.cap_store.search_by_similarity(msg, limit, min_similarity)
    
    @tracer.start_as_current_span("invoke_cap_safe")
    @record_latency(tool_execute_time_histogram)
    def call_cap_safe(self, cap: Capability, args_dict=None):
        ## todo replace by https://github.com/langchain-ai/langchain-sandbox?
        ## todo replace by e2b?
        """动态创建函数并执行"""
        func_code = cap.function_impl
        func_lines = [f"def dynamic_func({', '.join(args_dict.keys())}):"]
        func_lines.extend([f"    {line}" for line in func_code.split('\n')])
        func_def = '\n'.join(func_lines)
        local_vars = {}
        ## todo debug/trace
        logging.info(f"args_dict: {args_dict}")
        logging.info(f"func_def: {func_def}")
        exec(func_def, globals(), local_vars)
        func = local_vars['dynamic_func']
        return func(**args_dict)

    @tracer.start_as_current_span("record_cap_history_safe")
    def record(self, msg: Msg, cap: Capability):
        return self.cap_store.record(msg, cap)

    @tracer.start_as_current_span("getCapsByHistory")
    @record_latency(search_time_histogram, "search")
    def getCapsByHistory(self, msg: Msg, limit=5, min_similarity=0.5) -> Dict[str, Capability]:
        return self.cap_store.getCapsByHistory(msg, limit, min_similarity)

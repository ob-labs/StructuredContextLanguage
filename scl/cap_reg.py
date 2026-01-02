import sys
import os
import logging
from typing import List

# Add the StructuredContextLanguage directory to the path
scl_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(scl_root)
from scl.embeddings.impl import embed
from scl.trace import tracer
from scl.storage.base import StoreBase

class CapRegistry:
    def __init__(self, StoreBase: StoreBase):
        """
        Initialize the FunctionRegistry with any StoreBase implementation
        
        Args:
            StoreBase: An instance of any StoreBase implementation
        """
        self.cap_store = StoreBase
    
    ## RAG search between context and function description after embedding
    ## Return function in openAI tool format
    @tracer.start_as_current_span("getCapsByNames")
    def getCapsByNames(self, ToolNames: List[str]):
        functions = []
        if self.cap_store is None:
            logging.info("Database not initialized. Cannot perform similarity search.")
            return []
        for tool_name in ToolNames:
            logging.info(f"Searching for function: {tool_name}")
            function = self.cap_store.get_cap_by_name(tool_name)
            logging.info(f"Function: {function}")
            if function:
                functions.append(function[0])
        return functions
    
    ## RAG search between context and function description after embedding
    ## Return function in openAI tool format
    @tracer.start_as_current_span("getCapsBySimilarity")
    def getCapsBySimilarity(self, context: str, limit=5, min_similarity=0.5):
        if self.cap_store is None:
            logging.info("Database not initialized. Cannot perform similarity search.")
            return []
        embedding = embed(context)
        return self.cap_store.search_by_similarity(embedding, limit, min_similarity)
    
    @tracer.start_as_current_span("call_function_safe")
    def call_cap_safe(self, func_name: str, args_dict=None):
        ## todo replace by https://github.com/langchain-ai/langchain-sandbox?
        """动态创建函数并执行"""
        # 定义函数
        cap = self.getCapsByNames([func_name])[0]
        logging.info(f"Cap: {cap}")
        func_code = cap["function_impl"]
        #func_def = f"""
        #def dynamic_func({', '.join(args_dict.keys())}):
        #    {func_code}
        #"""
        # 执行函数定义
        #local_vars = {}
        #logging.info(f"args_dict: {args_dict}")
        #logging.info(f"func_def: {func_def}")
        #exec(func_def, globals(), local_vars)
        # 或者使用更简单的版本：
        func_lines = [f"def dynamic_func({', '.join(args_dict.keys())}):"]
        func_lines.extend([f"    {line}" for line in func_code.split('\n')])
        func_def = '\n'.join(func_lines)

        # 执行函数定义
        local_vars = {}
        logging.info(f"args_dict: {args_dict}")
        logging.info(f"func_def: {func_def}")
        exec(func_def, globals(), local_vars)   
        # 获取函数并执行
        func = local_vars['dynamic_func']
        return func(**args_dict)

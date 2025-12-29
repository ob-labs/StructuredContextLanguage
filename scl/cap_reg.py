import sys
import os
import logging
from typing import List

# Add the StructuredContextLanguage directory to the path
scl_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(scl_root)
from scl.embeddings.impl import embed
from scl.trace import tracer
from scl.utils import *
from scl.storage.base import StoreBase

class CapRegistry:
    def __init__(self, StoreBase: StoreBase):
        """
        Initialize the FunctionRegistry with any StoreBase implementation
        
        Args:
            StoreBase: An instance of any StoreBase implementation
        """
        self.cap_store = StoreBase
        ## todo remove this
        # Function registry
        self.FUNCTION_REGISTRY = {
            'add': add,
            'mul': mul, 
            'compare': compare,
            'count_letter': count_letter
        }
    
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
        ## todo replace by https://github.com/langchain-ai/langchain-sandbox
        ### get function body from reg
        ### get function name
        ### get function var
        ### invoke langchain-sandbox
        """
        Safely call a function through the registry
        """
        func = self.FUNCTION_REGISTRY.get(func_name)
        
        if func is None:
            raise ValueError(f"Function '{func_name}' is not registered or does not exist")
        
        # Call function
        return func(**args_dict)

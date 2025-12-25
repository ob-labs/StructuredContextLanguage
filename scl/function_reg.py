import sys
import os
import logging
from typing import List, Optional

# Add the StructuredContextLanguage directory to the path
scl_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(scl_root)
from scl.embeddings.impl import OpenAIEmbedding
from scl.trace import tracer
from scl.utils import *
from scl.storage.base import FunctionStoreBase

# Import storage classes conditionally to avoid import errors
PgVectorFunctionStore = None
try:
    from scl.storage.pg import PgVectorFunctionStore
except ImportError:
    logging.info("Warning: PgVectorFunctionStore could not be imported. Database functionality will be disabled.")

SkillStore = None
try:
    from scl.storage.skillstore import SkillStore
except ImportError:
    logging.info("Warning: SkillStore could not be imported. File-based storage will be disabled.")


class FunctionRegistry:
    def __init__(self, function_store: FunctionStoreBase):
        """
        Initialize the FunctionRegistry with any FunctionStoreBase implementation
        
        Args:
            function_store: An instance of any FunctionStoreBase implementation
            init_db: Optional parameter for initialization (deprecated for generic interface)
        """
        # Verify that the provided store implements the base interface
        if not isinstance(function_store, FunctionStoreBase):
            raise TypeError(f"function_store must be an instance of FunctionStoreBase, got {type(function_store)}")
        
        self.function_store = function_store
        
        # For storage implementations that require initialization, they should handle it in their own __init__
        # This approach makes the registry storage-agnostic
        #if init_db is not None:
        #    logging.info("init_db parameter is deprecated for generic storage interface. Initialization should be handled by storage implementation.")
        
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
    @tracer.start_as_current_span("getToolsByNames")
    def getToolsByNames(self, ToolNames: List[str]):
        functions = []
        if self.function_store is None:
            logging.info("Database not initialized. Cannot perform similarity search.")
            return []
        for tool_name in ToolNames:
            logging.info(f"Searching for function: {tool_name}")
            function = self.function_store.get_function_by_name(tool_name)
            if function:
                functions.append(function[0])
        return functions
    
    ## RAG search between context and function description after embedding
    ## Return function in openAI tool format
    @tracer.start_as_current_span("getTools")
    def getTools(self, context: str, limit=5):
        if self.function_store is None:
            logging.info("Database not initialized. Cannot perform similarity search.")
            return []
        return self.function_store.search_by_similarity(context, limit)
    
    @tracer.start_as_current_span("call_function_safe")
    def call_function_safe(self, func_name: str, args_dict=None):
        ## todo replace by https://github.com/langchain-ai/langchain-sandbox
        """
        Safely call a function through the registry
        """
        func = self.FUNCTION_REGISTRY.get(func_name)
        
        if func is None:
            raise ValueError(f"Function '{func_name}' is not registered or does not exist")
        
        # Call function
        return func(**args_dict)
    
    def support_functionCall(self):
        return self.function_store.support_function_Call()

    @tracer.start_as_current_span("insert_function")
    ## interface
    def insert_function(self, function_name, function_body, llm_description, function_description):
        """
        Insert a new function into the store
        """
        if self.function_store is None:
            logging.info("Database not initialized. Cannot insert function.")
            return None
        return self.function_store.insert_function(function_name, function_body, llm_description, function_description)
    
def main():
    """
    Main function to test the FunctionRegistry class
    """
    print("Testing FunctionRegistry...")
    
    # Test calling functions through the registry
    try:
        # Example with database storage (if available)
        if PgVectorFunctionStore:
            print("Testing with database storage...")
            function_store = PgVectorFunctionStore(
                dbname="postgres",
                user="postgres",
                password="postgres",  # 请修改为您的密码
                host="localhost",
                port="5432",
                init=True,
                embedding_service=OpenAIEmbedding()
            )
        else:
            # Fallback to file-based storage
            print("Database storage not available, testing with file-based storage...")
            function_store = SkillStore(
                folder="./test_functions",
                embedding_service=OpenAIEmbedding()
            )
        
        registry = FunctionRegistry(function_store)
        result = registry.call_function_safe('add', {'a': 5, 'b': 3})
        print(f"Add function result: 5 + 3 = {result}")
        
        # Test multiply function
        result = registry.call_function_safe('mul', {'a': 4, 'b': 7})
        print(f"Multiply function result: 4 * 7 = {result}")
        
        # Test compare function
        result = registry.call_function_safe('compare', {'a': 10, 'b': 5})
        print(f"Compare function result: {result}")
        
        # Test count_letter_in_string function
        result = registry.call_function_safe('count_letter', {'a': 'Hello World', 'b': 'l'})
        print(f"Count letter function result: {result}")

        print(registry.getTools("1 + 2 =?"))
        
        print("Basic function registry tests passed!")
        print("Note: Storage functionality depends on available implementations.")
            
    except Exception as e:
        print(f"Error during testing: {e}")
    
    # Clean up test files if using file-based storage
    try:
        import shutil
        if os.path.exists('./test_functions'):
            shutil.rmtree('./test_functions')
    except:
        pass


if __name__ == "__main__":
    main()

import sys
import os
import logging
from typing import List

# Add the StructuredContextLanguage directory to the path
scl_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(scl_root)
from scl.embeddings.impl import OpenAIEmbedding
from scl.trace import tracer
from scl.utils import *

# Import PgVectorFunctionStore conditionally to avoid import errors
PgVectorFunctionStore = None
try:
    from scl.storage.pg import PgVectorFunctionStore
except ImportError:
    logging.info("Warning: PgVectorFunctionStore could not be imported. Database functionality will be disabled.")


class FunctionRegistry:
    def __init__(self, function_store, init_db=True):
        """
        Initialize the FunctionRegistry with a PgVectorFunctionStore instance
        """
        self.function_store = function_store
        if init_db is not None:
            try:
                # Setup database
                self.function_store.create_database()
                self.function_store.enable_vector_extension()
                self.function_store.create_table()
            except Exception as e:
                logging.info(f"Warning: Could not initialize database connection: {e}")
                logging.info("Database functionality will be disabled.")
                self.function_store = None

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
        """
        Safely call a function through the registry
        """
        func = self.FUNCTION_REGISTRY.get(func_name)
        
        if func is None:
            raise ValueError(f"Function '{func_name}' is not registered or does not exist")
        
        # Call function
        return func(**args_dict)
    
    @tracer.start_as_current_span("insert_function")
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
        # Test add function
        function_store = PgVectorFunctionStore(
            dbname="postgres",
            user="postgres",
            password="postgres",  # 请修改为您的密码
            host="localhost",
            port="5432",
            embedding_service=OpenAIEmbedding()
        )
        registry = FunctionRegistry(function_store, True)
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
        print("Note: Database functionality requires PostgreSQL to be installed and running.")
            
    except Exception as e:
        print(f"Error during testing: {e}")


if __name__ == "__main__":
    main()

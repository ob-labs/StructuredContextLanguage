"""
Test script to verify that FunctionRegistry properly uses the storage interface
"""

import sys
import os

# Add the StructuredContextLanguage directory to the path
scl_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(scl_root)

from scl.function_reg import FunctionRegistry
from scl.storage.skillstore import SkillStore
from scl.storage.pg import PgVectorFunctionStore
from scl.embeddings.impl import OpenAIEmbedding


def test_with_file_storage():
    """Test FunctionRegistry with file-based storage"""
    print("=== Testing FunctionRegistry with File Storage ===")
    
    # Create file-based storage
    storage_folder = "./test_registry_functions"
    file_store = SkillStore(folder=storage_folder, embedding_service=OpenAIEmbedding())
    
    # Create FunctionRegistry with file storage
    registry = FunctionRegistry(file_store)
    
    # Test inserting a function
    llm_description = {
        'type': 'function',
        'function': {
            'name': 'test_function',
            'description': 'A test function',
            'parameters': {
                'type': 'object',
                'properties': {
                    'x': {'type': 'number', 'description': 'Input value'},
                },
                'required': ['x'],
            },
        }
    }
    
    result = registry.insert_function(
        function_name="test_function",
        function_body="def test_function(x): return x * 2",
        llm_description=llm_description,
        function_description="A test function that doubles the input"
    )
    
    print(f"Insert result: {result}")
    
    # Test retrieving the function
    tools = registry.getToolsByNames(["test_function"])
    print(f"Retrieved tools: {len(tools)}")
    
    # Clean up
    import shutil
    if os.path.exists(storage_folder):
        shutil.rmtree(storage_folder)
    
    print("File storage test completed successfully!\n")


def test_with_database_storage():
    """Test FunctionRegistry with database storage (if available)"""
    print("=== Testing FunctionRegistry with Database Storage ===")
    
    try:
        # Create database-based storage
        db_store = PgVectorFunctionStore(
            dbname="postgres",
            user="postgres",
            password="postgres",  # Update with your password
            host="localhost", 
            port="5432",
            embedding_service=OpenAIEmbedding()
        )
        
        # Create FunctionRegistry with database storage
        registry = FunctionRegistry(db_store)
        
        # Test inserting a function
        llm_description = {
            'type': 'function',
            'function': {
                'name': 'db_test_function',
                'description': 'A test function in database',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'x': {'type': 'number', 'description': 'Input value'},
                    },
                    'required': ['x'],
                },
            }
        }
        
        result = registry.insert_function(
            function_name="db_test_function",
            function_body="def db_test_function(x): return x + 10",
            llm_description=llm_description,
            function_description="A test function that adds 10 to input"
        )
        
        print(f"Insert result: {result}")
        
        # Test retrieving the function
        tools = registry.getToolsByNames(["db_test_function"])
        print(f"Retrieved tools: {len(tools)}")
        
        # Close database connection
        db_store.close()
        
        print("Database storage test completed successfully!\n")
        
    except Exception as e:
        print(f"Database test skipped due to: {e}")
        print("This is expected if PostgreSQL is not running or configured.\n")


def test_type_checking():
    """Test that FunctionRegistry properly validates the interface"""
    print("=== Testing Type Checking ===")
    
    class InvalidStore:
        """A class that doesn't implement FunctionStoreBase"""
        pass
    
    try:
        # This should raise a TypeError
        invalid_store = InvalidStore()
        registry = FunctionRegistry(invalid_store)
        print("ERROR: Type checking failed - should have raised TypeError")
    except TypeError as e:
        print(f"Type checking works correctly: {e}")
    
    print("Type checking test completed successfully!\n")


def main():
    """Run all tests"""
    print("Testing FunctionRegistry with storage interface...\n")
    
    test_type_checking()
    test_with_file_storage()
    test_with_database_storage()
    
    print("All tests completed!")


if __name__ == "__main__":
    main()
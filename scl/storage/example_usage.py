"""
Example usage of the storage interface with both implementations.
"""

import os
import sys
import json

# Add the StructuredContextLanguage directory to the path
scl_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(scl_root)

from scl.embeddings.impl import OpenAIEmbedding
from scl.storage.skillstore import SkillStore
from scl.storage.pg import PgVectorFunctionStore


def example_file_storage():
    """Example using file-based storage (SkillStore)"""
    print("=== File-based Storage Example ===")
    
    # Create a folder for storing functions
    storage_folder = "./example_functions"
    
    # Initialize the skill store
    skill_store = SkillStore(folder=storage_folder, embedding_service=OpenAIEmbedding())
    
    # Define a sample function
    function_name = "calculate_sum"
    function_body = """
def calculate_sum(numbers):
    \"\"\"Calculate the sum of a list of numbers\"\"\"
    return sum(numbers)
    """
    
    llm_description = {
        'type': 'function',
        'function': {
            'name': 'calculate_sum',
            'description': 'Calculate the sum of numbers in a list',
            'parameters': {
                'type': 'object',
                'properties': {
                    'numbers': {
                        'type': 'array',
                        'items': {'type': 'number'},
                        'description': 'List of numbers to sum'
                    },
                },
                'required': ['numbers'],
            },
        }
    }
    
    function_description = "Calculate the sum of numbers in a list"
    
    # Insert the function
    result = skill_store.insert_function(
        function_name=function_name,
        function_body=function_body,
        llm_description=llm_description,
        function_description=function_description
    )
    
    print(f"Insert result: {result}")
    
    # Retrieve the function by name
    functions = skill_store.get_function_by_name("calculate_sum")
    print(f"Retrieved functions: {len(functions)} found")
    if functions:
        print(f"Function description: {functions[0]}")
    
    # Search by similarity
    similar_functions = skill_store.search_by_similarity("add numbers together", limit=3)
    print(f"Similar functions: {len(similar_functions)} found")
    
    # List all functions
    all_functions = skill_store.list_all_functions(limit=10)
    print(f"All functions: {len(all_functions)} found")
    
    # Clean up example files
    import shutil
    if os.path.exists(storage_folder):
        shutil.rmtree(storage_folder)


def example_database_storage():
    """Example using database storage (PgVectorFunctionStore)"""
    print("\n=== Database Storage Example ===")
    
    try:
        # Initialize the database store
        # Note: This requires PostgreSQL with pgvector extension to be running
        db_store = PgVectorFunctionStore(
            dbname="postgres",
            user="postgres", 
            password="postgres",  # Update with your password
            host="localhost",
            port="5432",
            embedding_service=OpenAIEmbedding()
        )
        
        # Example operations would go here
        # For now, just show that the connection works
        print("Database connection established")
        
        # Close the connection
        db_store.close()
        
    except Exception as e:
        print(f"Database example skipped due to: {e}")
        print("This is expected if PostgreSQL is not running or configured.")


def main():
    """Run both examples"""
    example_file_storage()
    example_database_storage()
    
    print("\n=== Interface Usage Notes ===")
    print("Both SkillStore and PgVectorFunctionStore implement the same interface")
    print("This allows you to switch between storage backends seamlessly")
    print("Both classes inherit from FunctionStoreBase which defines the common interface")


if __name__ == "__main__":
    main()
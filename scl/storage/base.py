"""
Abstract base class interface for function storage implementations.
This defines the standard interface that all storage implementations should follow.
"""

import abc
import sys
import os
from typing import Optional, List, Dict, Any
# Add the StructuredContextLanguage directory to the path
scl_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(scl_root)

from scl.trace import tracer


class FunctionStoreBase(abc.ABC):
    """
    Abstract base class that defines the interface for function/skill storage implementations.
    All storage implementations should inherit from this class and implement all abstract methods.
    """
    
    @abc.abstractmethod
    def __init__(self, **kwargs):
        """
        Initialize the storage implementation.
        Specific parameters depend on the implementation (e.g., database connection params, folder path, etc.)
        """
        pass

    @tracer.start_as_current_span("generate_embedding")
    @abc.abstractmethod
    def generate_embedding(self, text: str) -> Any:
        """
        Generate embedding vector for the given text.
        
        Args:
            text: Input text to generate embedding for
            
        Returns:
            Embedding vector (type may vary depending on implementation)
        """
        pass

    @tracer.start_as_current_span("insert_function")
    @abc.abstractmethod
    def insert_function(self, 
                       function_name: str, 
                       function_body: str, 
                       llm_description: Dict[str, Any], 
                       function_description: str) -> Optional[Any]:
        """
        Insert a new function into storage.
        
        Args:
            function_name: Unique name for the function
            function_body: The actual function code/body
            llm_description: OpenAI function call format description dictionary
                example: {'type': 'function', 'function': {'name': 'add', ...}}
            function_description: Text description for generating embeddings
            
        Returns:
            Identifier of the inserted function, or None if insertion failed
        """
        pass

    @tracer.start_as_current_span("update_function")
    @abc.abstractmethod
    def update_function(self, 
                       function_id: Optional[Any] = None, 
                       function_name: Optional[str] = None, 
                       function_body: Optional[str] = None, 
                       llm_description: Optional[Dict[str, Any]] = None, 
                       function_description: Optional[str] = None) -> bool:
        """
        Update an existing function in storage.
        
        Args:
            function_id: Function identifier (implementation-specific)
            function_name: Function name to identify the function to update
            function_body: New function body code
            llm_description: New OpenAI function call format description
            function_description: New description text
            
        Returns:
            True if update was successful, False otherwise
        """
        pass

    @tracer.start_as_current_span("get_function_by_name")
    @abc.abstractmethod
    def get_function_by_name(self, function_name: str) -> List[Dict[str, Any]]:
        """
        Retrieve function(s) by name.
        
        Args:
            function_name: Name of the function to retrieve
            
        Returns:
            List of function descriptions (llm_description format)
        """
        pass

    @tracer.start_as_current_span("search_by_similarity")
    @abc.abstractmethod
    def search_by_similarity(self, 
                            query_text: str, 
                            limit: int = 5, 
                            min_similarity: float = 0.5) -> List[Dict[str, Any]]:
        """
        Search for functions by semantic similarity to the query text.
        
        Args:
            query_text: Text to search for similar functions
            limit: Maximum number of results to return
            min_similarity: Minimum similarity threshold (0.0 to 1.0)
            
        Returns:
            List of similar function descriptions (llm_description format)
        """
        pass

    @tracer.start_as_current_span("delete_function")
    @abc.abstractmethod
    def delete_function(self, 
                       function_id: Optional[Any] = None, 
                       function_name: Optional[str] = None) -> bool:
        """
        Delete a function from storage.
        
        Args:
            function_id: Function identifier (implementation-specific)
            function_name: Function name to identify the function to delete
            
        Returns:
            True if deletion was successful, False otherwise
        """
        pass

    @tracer.start_as_current_span("list_all_functions")
    @abc.abstractmethod
    def list_all_functions(self, limit: int = 10) -> List[tuple]:
        """
        List all functions in storage (for debugging purposes).
        
        Args:
            limit: Maximum number of functions to return
            
        Returns:
            List of tuples containing (id, name, body, llm_description, description)
        """
        pass

    @abc.abstractmethod
    def support_function_Call(self) -> bool:
        """
        Check if the storage implementation supports function calls.
        
        Returns:
            True if the storage implementation supports function calls, False otherwise
        """
        pass

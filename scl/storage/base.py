from abc import ABC, abstractmethod

class StoreBase(ABC):
    
    @abstractmethod
    def get_cap_by_name(self, name):
        """
        Retrieve a capability by its name.
        
        Args:
            name (str): The name of the capability to retrieve
            
        Returns:
            The capability object or None if not found
        """
        pass
    
    @abstractmethod
    def search_by_similarity(self, query_embedding, limit=5, min_similarity=0.5):
        """
        Search for similar items based on embedding similarity.
        
        Args:
            query_embedding: The embedding vector to search with
            limit (int): Maximum number of results to return (default 5)
            min_similarity (float): Minimum similarity threshold (default 0.5)
            
        Returns:
            List of similar items with their similarity scores
        """
        pass
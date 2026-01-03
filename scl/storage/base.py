from abc import ABC, abstractmethod
from scl.meta.msg import Msg
from scl.meta.capability import Capability
from typing import List

class StoreBase(ABC):
    
    @abstractmethod
    def get_cap_by_name(self, name) -> Capability:
        """
        Retrieve a capability by its name.
        
        Args:
            name (str): The name of the capability to retrieve
            
        Returns:
            The capability object or None if not found
        """
        pass
    
    @abstractmethod
    def search_by_similarity(self, msg:Msg, limit=5, min_similarity=0.5) -> List[Capability]:
        """
        Search for similar items based on embedding similarity.
        
        Args:
            msg (Msg): The message object containing the embedding vector to search with
            limit (int): Maximum number of results to return (default 5)
            min_similarity (float): Minimum similarity threshold (default 0.5)
            
        Returns:
            List of similar items with their similarity scores
        """
        pass

    @abstractmethod
    def record(self, msg:Msg, cap_name:str):
        """
        Record a query embedding and its associated capability name.
        
        Args:
            msg (Msg): The message object containing the embedding vector to search with
            cap_name (str): The name of the capability associated with the embedding
            
        Returns:
            None
        """
        pass
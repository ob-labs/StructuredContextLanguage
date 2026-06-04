"""
File path: scl/storage/base.py

Features and design goals
- Abstract base for all storage backends (filesystem, database, in‑memory, etc.).
- Define a uniform interface for capability retrieval and similarity search.
- Enable history tracking (record/query) to support context‑aware recommendations
  (implementations may choose to stub or fully implement based on constraints).
- Allow insertion of new capabilities into the store.
- Interface designed for OpenTelemetry‑aware implementations:
  concrete stores should add tracing, metrics, and structured logging as needed.
- Explicit Python ABC with abstract methods ensures that every backend
  implements the required methods; missing methods are caught early.
"""

from abc import ABC, abstractmethod

from scl.meta.capability import Capability
from scl.meta.msg import Msg


class StoreBase(ABC):
    """
    Abstract base class for capability storage.

    Concrete implementations must provide:

    - get_cap_by_name(name) -> Capability
    - search_by_similarity(msg, limit, min_similarity) -> Dict[str, Capability]
    - record(msg, cap)
    - getCapsByHistory(msg, limit, min_similarity) -> Dict[str, Capability]
    - insert_capability(cap)
    """

    @abstractmethod
    def get_cap_by_name(self, name: str) -> Capability:
        """
        Retrieve a capability by its exact name.

        Args:
            name: Name of the capability.

        Returns:
            Capability object if found, otherwise None.
        """
        pass

    @abstractmethod
    def search_by_similarity(
        self, msg: Msg, limit: int = 5, min_similarity: float = 0.5
    ) -> dict[str, Capability]:
        """
        Semantic search for capabilities similar to the query embedding.

        Args:
            msg: Message containing the embedding vector (msg.embed).
            limit: Maximum number of results.
            min_similarity: Cosine similarity threshold.

        Returns:
            Mapping of capability name -> Capability for the top‑k matches
            above the threshold.
        """
        pass

    @abstractmethod
    def record(self, msg: Msg, cap: Capability) -> None:
        """
        Record that a given capability was selected for a particular message.

        Args:
            msg: The original query message.
            cap: The capability that was used.
        """
        pass

    @abstractmethod
    def getCapsByHistory(
        self, msg: Msg, limit: int = 5, min_similarity: float = 0.5
    ) -> dict[str, Capability]:
        """
        Retrieve capabilities based on usage history.

        Args:
            msg: Current message (may be used for additional filtering).
            limit: Maximum number of results.
            min_similarity: Similarity threshold.

        Returns:
            Mapping of capability name -> Capability.
        """
        pass

    @abstractmethod
    def insert_capability(self, cap: Capability) -> None:
        """
        Insert a new capability into the store.

        Args:
            cap: The capability to store.
        """
        pass


"""
Example usage:
    from scl.storage.fsstore import fsstore
    from scl.meta.msg import Msg

    # Any concrete implementation can be used transparently
    store: StoreBase = fsstore(path="/skills", init=True)

    # Retrieve a known capability
    cap = store.get_cap_by_name("image_classifier")
    if cap:
        print(cap.name, cap.type, cap.description)

    # Insert a new capability
    from scl.meta.capability import Capability
    new_cap = Capability(name="new_model", type="ai", description="New AI model")
    store.insert_capability(new_cap)

    # Search by similarity
    query = Msg(...)   # msg with an embedding
    similar = store.search_by_similarity(query, limit=3, min_similarity=0.7)
    for name, cap in similar.items():
        print(name, cap.description)

    # History‑based recommendations (implementation dependent)
    hist_results = store.getCapsByHistory(query, limit=2)
"""

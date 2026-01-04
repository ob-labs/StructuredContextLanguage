"""
Storage interface for function/skill storage implementations.
"""

from .base import StoreBase

__all__ = ['StoreBase']

# Import PgVectorStore (PostgreSQL with pgvector)
try:
    from .pgstore import PgVectorStore
    __all__.append('PgVectorStore')
except ImportError:
    pass

# Import fsstore (File system storage)
try:
    from .fsstore import fsstore
    __all__.append('fsstore')
except ImportError:
    pass

# Import OceanBaseStore (OceanBase with pyobvector)
try:
    from .oceanbasestore import OceanBaseStore
    __all__.append('OceanBaseStore')
except ImportError:
    pass

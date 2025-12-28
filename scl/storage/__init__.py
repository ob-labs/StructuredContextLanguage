"""
Storage interface for function/skill storage implementations.
"""

from .base import FunctionStoreBase
from .skillstore import SkillStore

__all__ = ['FunctionStoreBase', 'SkillStore']
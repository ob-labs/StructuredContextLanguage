from abc import ABC, abstractmethod
from typing import Optional, Dict
from scl.embeddings.impl import embed


class Capability(ABC):
    """
    Abstract base class for Skill and FunctionCall classes.
    Provides a common interface for both skill-based and function call-based implementations.
    """
    
    def __init__(self,
                 name: str,
                 description: str,
                 original_body: str,
                 llm_description: Optional[str] = None,
                 function_impl: Optional[str] = None):
        self._name = name
        self._description = description
        self._embedding_description = embed(self._description)
        self._original_body = original_body
        
        self._llm_description = llm_description
        self._function_impl = function_impl

    @property
    def name(self) -> str:
        """skill/function call名称"""
        return self._name

    @property
    def description(self) -> str:
        """函数描述 用于渐进式加载"""
        return self._description

    @property
    def original_body(self) -> str:
        """原始描述"""
        return self._original_body

    @property
    def embedding_description(self):
        """函数描述 实际用于RAG渐进式加载"""
        return self._embedding_description

    @property
    def llm_description(self) -> Optional[str]:
        """LLM生成的描述 用于tool字段"""
        return self._llm_description

    @property
    def function_impl(self) -> Optional[str]:
        """函数实现 用于sandbox执行"""
        return self._function_impl

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}', description='{self.description[:50]}...')"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Capability):
            return False
        return (self._name == other._name and
                self._description == other._description and
                self._original_body == other._original_body)
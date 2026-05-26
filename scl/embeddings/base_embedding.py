"""
File path:
scl/embedding/base_embedding.py

Common base class for all embedding clients.
Provides singleton pattern, OpenTelemetry logging and metrics,
and an abstract embed() method.
"""

import logging
from abc import ABC, abstractmethod

from scl.otel.otel import meter
from opentelemetry import trace

class BaseEmbeddingClient(ABC):
    _instances = {}

    def __new__(cls):
        if cls not in cls._instances:
            instance = super().__new__(cls)
            cls._instances[cls] = instance
            instance._initialized = False
        return cls._instances[cls]

    def __init__(self):
        if self._initialized:
            return
        self.logger = logging.getLogger(self.__class__.__name__)
        self._meter = meter
        self._initialized = True
        self._init_subclass()

    def _init_subclass(self):
        pass

    @abstractmethod
    def embed(self, text, **kwargs):
        pass
# Package initialization for scl.meta

# This package contains metadata and task-related classes

__version__ = "0.1.0"

# Import main classes for convenience
from .task import Task
from .captask import CapTask
from .capability import Capability
from .functioncall import FunctionCall
from .msg import Msg as Message
from .skill import Skill

from typing import Optional
from scl.embeddings.impl import embed
from scl.meta.capability import Capability

class FunctionCall(Capability):
    def __init__(self, 
                 name: str, 
                 description: str, 
                 original_body: str, 
                 llm_description: Optional[str] = None,
                 function_impl: Optional[str] = None):
        super().__init__(name, description, original_body, "function_call", llm_description, function_impl)
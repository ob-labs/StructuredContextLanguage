## for meta data folder we need to
### support load from init def as skill
### support interface to LLM as function call
### support tool call loop
import json
from typing import Dict, Optional
from scl.embeddings.impl import embed
from scl.meta.skills_ref.parser import SkillProperties
from scl.meta.base import Capability

class Skill(Capability):
    def __init__(self, 
                 SkillProperties: SkillProperties):
        if SkillProperties.metadata:
            self._original_body_dict = SkillProperties.metadata.copy()
        else:
            self._original_body_dict = {}
        original_body = json.dumps(self._original_body_dict, ensure_ascii=False, indent=2)
        
        super().__init__(
            name=SkillProperties.name,
            description=SkillProperties.description,
            original_body=original_body,
            llm_description=None,  # tbd
            function_impl=None    # tbd
        )

    @property
    def original_body_dict(self) -> Dict[str, str]:
        """原始描述获取字典表示"""
        return self._original_body_dict

    def __repr__(self) -> str:
        return f"Skill(name='{self.name}', description='{self.description[:50]}...')"

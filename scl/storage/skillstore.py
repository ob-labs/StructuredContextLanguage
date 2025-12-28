import sys
import os
import json
import logging
import hashlib
import time
from pathlib import Path
import numpy as np
import pickle

from typing import Optional, List, Dict, Any
# Add the StructuredContextLanguage directory to the path
scl_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(scl_root)

from scl.trace import tracer
from scl.embeddings.impl import OpenAIEmbedding
from scl.storage.base import FunctionStoreBase

# Import from the local skills_ref module
from scl.storage.skills_ref.parser import read_properties
from scl.storage.skills_ref.models import SkillProperties


class SkillStore(FunctionStoreBase):
    def __init__(self, folder, init=True, embedding_service=None):
        super().__init__()
        self.folder = folder
        self.embedding_service = embedding_service
        # Initialize cache for skill description embeddings
        self._skill_embedding_cache = {}
        self.cache_file = Path(self.folder) / ".skill_cache.pkl"  # Cache file path
        
        # Load existing cache from disk if it exists
        if init:
            dir_path = Path(self.folder).resolve()
            for item in dir_path.iterdir():
                if item.is_dir():
                    try:
                        skill_props = read_properties(item)
                        # Pre-populate the cache with skill description embeddings
                        print("init skill " + skill_props.name)
                        skill_embedding = self.generate_embedding(skill_props.description)
                        time.sleep(10) ## workaround for rate limiting
                        self._skill_embedding_cache[str(item)] = {
                            "skill_props": skill_props,
                            "embedding": skill_embedding
                        }
                    except Exception as e:
                        logging.error(f"Error reading properties for {item}: {e}")
            print("save chache to disk")
            self._save_cache_to_disk()
        else:
            self._load_cache_from_disk()

    def cosine_similarity(self, vec1, vec2):
        """
        计算两个向量的余弦相似度
        """
        vec1 = np.array(vec1)
        vec2 = np.array(vec2)
        
        dot_product = np.dot(vec1, vec2)
        norm_vec1 = np.linalg.norm(vec1)
        norm_vec2 = np.linalg.norm(vec2)
        
        if norm_vec1 == 0 or norm_vec2 == 0:
            return 0.0
        
        similarity = dot_product / (norm_vec1 * norm_vec2)
        return float(similarity) 

    @tracer.start_as_current_span("generate_embedding")
    def generate_embedding(self, text):
        """生成文本的嵌入向量"""
        embedding = self.embedding_service.embed(text)
            # Convert to Vector type if available
        return embedding

    @tracer.start_as_current_span("insert_function")
    def insert_function(self, function_name, function_body, llm_description, function_description):
        pass

    @tracer.start_as_current_span("update_function")
    def update_function(self, function_id=None, function_name=None, function_body=None, llm_description=None, function_description=None):
        pass

    @tracer.start_as_current_span("get_function_by_name")
    def get_function_by_name(self, function_name):
        """根据函数名查询"""
        # todo
        pass

    @tracer.start_as_current_span("search_by_similarity")
    def search_by_similarity(self, query_text, limit=5, min_similarity=0.5):
        """根据描述相似度查询函数"""
        result = {}
        query_embedding = self.generate_embedding(query_text)
        
        # Use cached embeddings instead of recalculating
        for skill_path, skill_data in self._skill_embedding_cache.items():
            skill_embedding = skill_data["embedding"]
            similarity = self.cosine_similarity(query_embedding, skill_embedding)
            
            if similarity >= min_similarity:
                # Use the skill_props directly from the cache
                skill_props = skill_data["skill_props"]
                logging.info(f"skill found {skill_props.name} with similarity {similarity}")
                result[skill_path] = skill_props
            
            if len(result) >= limit:
                break
        
        return result    


    @tracer.start_as_current_span("delete_function")
    def delete_function(self, function_id=None, function_name=None):
        # no implementation
        pass
    
    @tracer.start_as_current_span("list_all_functions")
    def list_all_functions(self, limit=10):
        # todo
        pass

    def _save_cache_to_disk(self):
        """Save the current cache to disk"""
        try:
            # Convert numpy arrays to lists for serialization
            serializable_cache = {}
            for path, data in self._skill_embedding_cache.items():
                serializable_cache[path] = {
                    "skill_props": data["skill_props"],
                    "embedding": data["embedding"].tolist() if isinstance(data["embedding"], np.ndarray) else data["embedding"]
                }
            
            with open(self.cache_file, "wb") as f:
                pickle.dump(serializable_cache, f)
            logging.info(f"Cache saved to {self.cache_file}")
        except Exception as e:
            logging.error(f"Error saving cache to disk: {e}")
    
    def _load_cache_from_disk(self):
        """Load cache from disk if it exists"""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "rb") as f:
                    serializable_cache = pickle.load(f)
                
                # Convert lists back to numpy arrays
                for path, data in serializable_cache.items():
                    self._skill_embedding_cache[path] = {
                        "skill_props": data["skill_props"],
                        "embedding": np.array(data["embedding"]) if isinstance(data["embedding"], list) else data["embedding"]
                    }
                
                logging.info(f"Cache loaded from {self.cache_file}")
            except Exception as e:
                logging.error(f"Error loading cache from disk: {e}")
    
    def clear_cache(self):
        """Clear the in-memory cache and remove the cache file"""
        self._skill_embedding_cache = {}
        if self.cache_file.exists():
            self.cache_file.unlink()  # Remove the cache file
        
    def refresh_cache(self):
        """Refresh the cache by clearing it and repopulating from the skill folders"""
        self.clear_cache()
        self._load_cache_from_disk()  # Try to load existing cache first
        
        # Repopulate cache with skills from folder
        dir_path = Path(self.folder).resolve()
        for item in dir_path.iterdir():
            if item.is_dir():
                try:
                    skill_props = read_properties(item)
                    # Pre-populate the cache with skill description embeddings
                    
                    skill_embedding = self.generate_embedding(skill_props.description)
                    time.sleep(10) ## workaround for rate limiting
                    self._skill_embedding_cache[str(item)] = {
                        "skill_props": skill_props,
                        "embedding": skill_embedding
                    }
                except Exception as e:
                    logging.error(f"Error reading properties for {item}: {e}")
        
        # Save the refreshed cache to disk
        self._save_cache_to_disk()
    
    def support_function_Call(self) -> bool:
        return False

def main():
    skill_store = SkillStore(folder="./skills/skills",init=False,embedding_service=OpenAIEmbedding())
    print(skill_store.search_by_similarity("Creating algorithmic art using p5.js with seeded randomness and interactive parameter exploration."))

if __name__ == "__main__":
    main()
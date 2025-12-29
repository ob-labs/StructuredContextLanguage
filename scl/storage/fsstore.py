from pathlib import Path
import logging
import pickle
from scl.meta.skills_ref.parser import read_properties
from scl.storage.base import StoreBase
from scl.meta.skill import Skill
import numpy as np
from scl.trace import tracer

class fsstore(StoreBase):
    def __init__(self, path, init):
        self.path = path
        self.cache_file = Path(self.path) / ".Capability_cache.pkl"  # Cache file path
        self._skill_embedding_cache = {}
        if init:
            self.refresh_cache()
        else:
            self._load_cache_from_disk()
            
    def cache(self):
        return self._skill_embedding_cache

    def load_skill(self,item):
        try:
            skill_props = read_properties(item)
            logging.info(skill_props)
            capability = Skill(skill_props)
            self._skill_embedding_cache[str(item)] = {
                    "Capability": capability
                }
        except Exception as e:
            logging.error(f"Error reading properties for {item}: {e}")

    def _save_cache_to_disk(self):
        """Save the current cache to disk"""
        try:
            # Convert numpy arrays to lists for serialization
            serializable_cache = {}
            for path, data in self._skill_embedding_cache.items():
                serializable_cache[path] = {
                    "Capability": data["Capability"],
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
                        "Capability": data["Capability"],
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
        dir_path = Path(self.path).resolve()
        for item in dir_path.iterdir():
            if item.is_dir():
               self.load_skill(item)
        # Save the refreshed cache to disk
        self._save_cache_to_disk()

    @tracer.start_as_current_span("get_cap_by_name")
    def get_cap_by_name(self, name):
        for path, data in self._skill_embedding_cache.items():
            if data["Capability"].name == name:
                return data["Capability"]
        return None

    @tracer.start_as_current_span("search_by_similarity")
    def search_by_similarity(self, query_embedding, limit=5, min_similarity=0.5):
        result = {}
        for path, data in self._skill_embedding_cache.items():
            skill_embedding = data["Capability"].embedding_description
            similarity = self.cosine_similarity(query_embedding, skill_embedding)
            if similarity >= min_similarity:
                result[path] = data
            if len(result) >= limit:
                break
        return result
    
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
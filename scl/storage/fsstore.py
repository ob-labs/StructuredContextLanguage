"""
File path: scl/storage/fsstore.py

Features and design goals
- It init by a directory path containing property files and embedding service.
- Persistent file‑based storage for capabilities (Capability objects).
- Data structures:
    - It's a file based table for capabilities (Capability objects), containing name, description, and full field as json obj for Capability Object store.
    - It has some scoring fields for similarity search.(For example BM25 and embedding).
    - BM25 impls by from rank_bm25 import BM25Okapi
- Load capabilities from a directory containing property files; cache embeddings and metadata in memory.
- Cache persistence via pickle to speed up subsequent startups.

- Supports insert_capability as adding a new capability.
    - By default, it will check if new capability's name and description are unique or not.
    - If not unique, it will raise an error, and return none.
    - If unique, it will check if insert model in force or not.
        - If in force, it will add the new capability to the store.
        - If not in force, it bases on embedding calculate similarity.
        - If similarity is below a threshold, it will raise an error, and return the existing capability.
        - Otherwise, add the new capability to the store.
    - For any new capability added to the store, recalculate description based BM25 for all existing capabilities.
    - For any new capability added to the store, return insert item as success.

- Retrieve a capability by its exact name.
- Semantic similarity search over cached skill embeddings:
    - By default, according to BM25 score after min-max normalization return top k.
    - If embedding service is on, according to embedding similarity return top k.
    - Provides options for linear combining BM25 and embedding scores.
        - option one: min-max BM25 with factor a and 1-a for embedding.
        - option two: sigmod BM25 with factor a and 1-a for embedding.
        - option three: tanh BM25 with factor a and 1-a for embedding.
        - option four: min-max BM25 with factor a and 1-a for sigmod BM25.
        - option five: min-max BM25 with factor a and 1-a for tanh BM25.

- History recording stubbed (to avoid unbounded disk growth); future enhancement may add in‑memory fixed‑size history.

- Observability:
  - OpenTelemetry tracing spans for key methods.
  - Metrics: counters for `search_by_similarity`, `get_cap_by_name`, and `insert_capability` invocations.
  - Structured logging at INFO and DEBUG levels for operational visibility.

Missing features (not in current scope but noted for future):
- JSON serialization of capability store for interoperability.
- Ability to remove or update existing capabilities.
- In-memory history ring buffer for capability usage tracking.
- Support for fuzzy name matching in get_cap_by_name.
"""

import logging
import pickle
from pathlib import Path

import numpy as np
from opentelemetry import trace
from rank_bm25 import BM25Okapi

# Import the embedding service (singleton) and its global embed function
from scl.embeddings.embedding import embed as generate_embedding
from scl.meta.capability import Capability
from scl.meta.functioncall import FunctionCall
from scl.meta.msg import Msg
from scl.meta.skill import Skill
from scl.meta.skills_ref.parser import read_properties
from scl.otel.otel import meter, tracer
from scl.storage.base import StoreBase


class fsstore(StoreBase):
    def __init__(self, path: str, init: bool, embedding_service_on: bool = False):
        super().__init__()
        self.path = path
        self.cache_file = Path(self.path) / ".Capability_cache.pkl"
        self._skill_embedding_cache: dict[str, dict] = {}
        self.embedding_service_on = embedding_service_on
        self.bm25: BM25Okapi | None = None

        # Instance logger (following the convention)
        self.logger = logging.getLogger(__name__)

        # Metrics
        self.search_counter = meter.create_counter(
            "fsstore.search_by_similarity.calls",
            description="Number of calls to search_by_similarity",
        )
        self.get_name_counter = meter.create_counter(
            "fsstore.get_cap_by_name.calls",
            description="Number of calls to get_cap_by_name",
        )
        self.insert_counter = meter.create_counter(
            "fsstore.insert_capability.calls",
            description="Number of calls to insert_capability",
        )

        if init:
            self.refresh_cache()
        else:
            self._load_cache_from_disk()
            self._rebuild_bm25()

    def cache(self) -> dict[str, dict]:
        """Return the in‑memory skill embedding cache."""
        return self._skill_embedding_cache

    def load_skill(self, item: Path) -> None:
        """Load a single skill from a property file, generate embedding if needed, and cache it."""
        try:
            skill_props = read_properties(item)
            self.logger.debug("Loaded skill properties for %s: %s", item, skill_props)
            capability = Skill(skill_props)
            # Generate embedding if service is on and description exists
            if self.embedding_service_on and capability.description:
                capability.embedding_description = generate_embedding(capability.description)
            else:
                capability.embedding_description = None
            self._skill_embedding_cache[str(item)] = {"Capability": capability}
        except Exception as e:
            self.logger.error("Error reading properties for %s: %s", item, e)

    def _save_cache_to_disk(self) -> None:
        """Persist the current in‑memory cache to disk using pickle."""
        try:
            with open(self.cache_file, "wb") as f:
                pickle.dump(self._skill_embedding_cache, f)
            self.logger.info(
                "Cache saved to %s (entries: %d)", self.cache_file, len(self._skill_embedding_cache)
            )
        except Exception as e:
            self.logger.error("Error saving cache to disk: %s", e)

    def _load_cache_from_disk(self) -> None:
        """Load the cache from disk if the pickle file exists."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "rb") as f:
                    self._skill_embedding_cache = pickle.load(f)
                self.logger.info(
                    "Cache loaded from %s (entries: %d)",
                    self.cache_file,
                    len(self._skill_embedding_cache),
                )
            except Exception as e:
                self.logger.error("Error loading cache from disk: %s", e)

    def clear_cache(self) -> None:
        """Clear the in‑memory cache and remove the disk cache file."""
        self._skill_embedding_cache = {}
        if self.cache_file.exists():
            self.cache_file.unlink()
            self.logger.info("Cache file %s removed", self.cache_file)
        self.bm25 = None

    def _rebuild_bm25(self) -> None:
        """Rebuild BM25 index from current cache capabilities.
        Each document is built from the capability's name and description,
        lowercased and whitespace-tokenized for case-insensitive matching.
        """
        self.logger.info("Rebuilding BM25 index")
        corpus = []
        for data in self._skill_embedding_cache.values():
            cap = data["Capability"]
            name = cap.name if cap.name else ""
            desc = cap.description if cap.description else ""
            # Combine name and description for richer matching
            doc_text = f"{name}. {desc}"
            corpus.append(doc_text.lower().split())
        if corpus:
            self.bm25 = BM25Okapi(corpus)
        else:
            self.bm25 = None
        self.logger.info("BM25 index rebuilt with %d documents", len(corpus) if corpus else 0)

    def refresh_cache(self) -> None:
        """Clear the cache, reload from disk, repopulate from the skill directory, rebuild BM25, and save."""
        self.clear_cache()
        self._load_cache_from_disk()

        dir_path = Path(self.path).resolve()
        for item in dir_path.iterdir():
            if item.is_dir():
                self.load_skill(item)
        self.logger.info(
            "Cache refresh completed, total skills: %d", len(self._skill_embedding_cache)
        )
        self._rebuild_bm25()
        self._save_cache_to_disk()

    @tracer.start_as_current_span("insert_capability")
    def insert_capability(
        self, capability: Capability, force: bool = False, similarity_threshold: float = 0.8
    ) -> Capability:
        """
        Add a new capability to the store.
        Always checks uniqueness of name and description; raises ValueError if a duplicate exists.
        If `force` is False and embedding_service_on is True, a similarity check is performed
        against existing capabilities using embeddings. If the maximum cosine similarity
        with any existing capability equals or exceeds `similarity_threshold`, a ValueError is raised
        with the existing capability attached as `existing_capability` attribute.
        Otherwise, the capability is inserted and returned.
        Recalculates BM25 and persists cache.

        Args:
            capability: Capability to insert.
            force: If True, skip the embedding similarity check.
            similarity_threshold: Cosine similarity threshold for duplicate detection (only when force=False).

        Returns:
            The inserted Capability on success.

        Raises:
            ValueError: If a capability with the same name or description already exists,
                        or if similarity check detects a too similar capability.
        """
        self.insert_counter.add(1)
        current_span = trace.get_current_span()
        current_span.set_attribute("capability.name", capability.name)
        current_span.set_attribute(
            "capability.description", capability.description[:100] if capability.description else ""
        )
        current_span.set_attribute("force", force)

        # Uniqueness check on exact name and description
        for data in self._skill_embedding_cache.values():
            cur = data["Capability"]
            if cur.name == capability.name:
                msg = f"Capability with name '{capability.name}' already exists"
                self.logger.error(msg)
                current_span.set_attribute("error", True)
                exc = ValueError(msg)
                exc.existing_capability = cur
                raise exc
            if cur.description == capability.description:
                msg = "Capability with the same description already exists"
                self.logger.error(msg)
                current_span.set_attribute("error", True)
                exc = ValueError(msg)
                exc.existing_capability = cur
                raise exc

        # Similarity check when not forced and embedding service is available
        if not force and self.embedding_service_on:
            # Ensure the new capability has an embedding for comparison
            if capability.description and capability.embedding_description is None:
                capability.embedding_description = generate_embedding(capability.description)
                self.logger.debug("Generated embedding for new capability '%s'", capability.name)
            if capability.embedding_description is not None:
                max_sim = 0.0
                most_similar_cap = None
                for data in self._skill_embedding_cache.values():
                    cur = data["Capability"]
                    if cur.embedding_description is not None:
                        sim = self.cosine_similarity(
                            capability.embedding_description, cur.embedding_description
                        )
                        if sim > max_sim:
                            max_sim = sim
                            most_similar_cap = cur
                if max_sim >= similarity_threshold:
                    msg = (
                        f"Similar capability already exists (max cosine similarity {max_sim:.4f} >= threshold "
                        f"{similarity_threshold}). Use force=True to insert anyway."
                    )
                    self.logger.error(msg)
                    current_span.set_attribute("error", True)
                    exc = ValueError(msg)
                    exc.existing_capability = most_similar_cap
                    raise exc
                else:
                    self.logger.debug("Similarity check passed, max similarity = %.4f", max_sim)
            else:
                self.logger.debug("No embedding for new capability, skipping similarity check")
        elif not force and not self.embedding_service_on:
            self.logger.debug("Embedding service is off, similarity check skipped")

        # Insert the capability
        key = f"_inserted_{capability.name}"
        self._skill_embedding_cache[key] = {"Capability": capability}
        self.logger.info("Inserted capability '%s'", capability.name)

        # Update BM25 and persist
        self._rebuild_bm25()
        self._save_cache_to_disk()

        current_span.set_attribute("insert.success", True)
        return capability

    @tracer.start_as_current_span("get_cap_by_name")
    def get_cap_by_name(self, name: str) -> Capability | None:
        """Return a Capability by its exact name, or None if not found."""
        self.get_name_counter.add(1)
        current_span = trace.get_current_span()
        current_span.set_attribute("skill.name", name)

        for data in self._skill_embedding_cache.values():
            cur = data["Capability"]
            if cur.name == name:
                self.logger.debug("Found capability with name '%s'", name)
                return Capability(name=cur.name, type=cur.type, description=cur.description)

        self.logger.debug("Capability '%s' not found", name)
        return None

    @staticmethod
    def _minmax_normalize(scores: np.ndarray) -> np.ndarray:
        """Apply min‑max normalization to a score array."""
        if len(scores) == 0:
            return np.array([])
        s_min = np.min(scores)
        s_max = np.max(scores)
        if s_max - s_min == 0:
            return np.zeros_like(scores)
        return (scores - s_min) / (s_max - s_min)

    @staticmethod
    def _sigmoid(scores: np.ndarray) -> np.ndarray:
        """Sigmoid transformation (clipped for numerical stability)."""
        # Clip to avoid overflow
        clipped = np.clip(scores, -500, 500)
        return 1.0 / (1.0 + np.exp(-clipped))

    @staticmethod
    def _tanh(scores: np.ndarray) -> np.ndarray:
        """Hyperbolic tangent transformation."""
        return np.tanh(scores)

    @tracer.start_as_current_span("search_by_similarity")
    def search_by_similarity(
        self,
        msg: Msg,
        limit: int = 5,
        min_similarity: float = 0.5,
        combine_method: str | None = None,
        alpha: float = 0.5,
    ) -> dict[str, Capability]:
        """
        Semantically search for capabilities.

        If `combine_method` is None:
            - Uses embedding similarity if `self.embedding_service_on` and `msg.embed` is available,
              otherwise falls back to BM25 scoring (with proper min‑max normalization).
        If `combine_method` is set, linear combination of BM25 and embedding scores is performed
        according to the chosen method (see below). The final score is `alpha * bm25_score + (1-alpha) * embedding_score`
        for options 1‑3, or `alpha * bm25_1 + (1-alpha) * bm25_2` for options 4‑5 (no embedding required).

        Supported combine_method values:
            - "1" / "option1" / "minmax"          : min‑max normalized BM25 + embedding
            - "2" / "option2" / "sigmoid"         : sigmoid BM25 + embedding
            - "3" / "option3" / "tanh"            : tanh BM25 + embedding
            - "4" / "option4" / "minmax_sigmoid"  : min‑max BM25 + sigmoid BM25
            - "5" / "option5" / "minmax_tanh"     : min‑max BM25 + tanh BM25

        Returns a dict mapping capability name -> Capability (here FunctionCall) containing the top‑k
        matches sorted by descending similarity score.
        """
        self.search_counter.add(1)
        current_span = trace.get_current_span()
        current_span.set_attribute("search.limit", limit)
        current_span.set_attribute("search.min_similarity", min_similarity)
        if combine_method:
            current_span.set_attribute("search.combine_method", combine_method)
            current_span.set_attribute("search.alpha", alpha)

        # Collect all (path, Capability) pairs
        items: list[tuple[str, Capability]] = [
            (path, data["Capability"]) for path, data in self._skill_embedding_cache.items()
        ]

        # Determine if embedding can be used
        use_embedding = (
            self.embedding_service_on and hasattr(msg, "embed") and msg.embed is not None
        )

        # Precompute BM25 scores if they might be needed
        bm25_raw = None
        if combine_method is not None or not use_embedding:
            query_text = msg.messages
            if not self.bm25:
                self.logger.warning("BM25 model not available, returning empty results")
                return {}
            query_tokens = query_text.lower().split()
            bm25_raw = np.array(self.bm25.get_scores(query_tokens))

        # Precompute embedding similarities if needed for combination (options 1‑3)
        emb_sims = None
        if combine_method is not None and use_embedding:
            query_embedding = msg.embed
            emb_sims = np.array(
                [
                    self.cosine_similarity(query_embedding, cap.embedding_description)
                    for _, cap in items
                ]
            )

        scored = []
        if combine_method is None:
            # Original behavior: embedding if available, else BM25 with min‑max
            if use_embedding:
                query_embedding = msg.embed
                for _idx, (path, cap) in enumerate(items):
                    sim = (
                        self.cosine_similarity(query_embedding, cap.embedding_description)
                        if cap.embedding_description is not None
                        else 0.0
                    )
                    self.logger.debug("Embedding similarity with '%s': %.4f", path, sim)
                    if sim >= min_similarity:
                        scored.append((sim, path, cap))
                scored.sort(key=lambda x: x[0], reverse=True)
            else:
                if bm25_raw is None or len(bm25_raw) == 0:
                    self.logger.warning(
                        "BM25 scores could not be computed, returning empty results"
                    )
                    return {}
                norm_scores = self._minmax_normalize(bm25_raw)
                for idx, (path, cap) in enumerate(items):
                    sim = float(norm_scores[idx])
                    self.logger.debug("BM25 (min‑max) similarity with '%s': %.4f", path, sim)
                    if sim >= min_similarity:
                        scored.append((sim, path, cap))
                scored.sort(key=lambda x: x[0], reverse=True)
        else:
            # Linear combination branch
            method = combine_method.lower().strip()
            # Map common aliases
            if method in ("1", "option1", "minmax"):
                norm_bm25 = self._minmax_normalize(bm25_raw)
                if use_embedding and emb_sims is not None:
                    for idx, (path, cap) in enumerate(items):
                        combined = alpha * norm_bm25[idx] + (1 - alpha) * emb_sims[idx]
                        if combined >= min_similarity:
                            scored.append((combined, path, cap))
                else:
                    self.logger.warning(
                        "Embedding required for method %s but unavailable, falling back to min‑max BM25",
                        combine_method,
                    )
                    for idx, (path, cap) in enumerate(items):
                        sim = float(norm_bm25[idx])
                        if sim >= min_similarity:
                            scored.append((sim, path, cap))
            elif method in ("2", "option2", "sigmoid"):
                sig_bm25 = self._sigmoid(bm25_raw)
                if use_embedding and emb_sims is not None:
                    for idx, (path, cap) in enumerate(items):
                        combined = alpha * sig_bm25[idx] + (1 - alpha) * emb_sims[idx]
                        if combined >= min_similarity:
                            scored.append((combined, path, cap))
                else:
                    self.logger.warning(
                        "Embedding required for method %s but unavailable, falling back to sigmoid BM25",
                        combine_method,
                    )
                    for idx, (path, cap) in enumerate(items):
                        sim = float(sig_bm25[idx])
                        if sim >= min_similarity:
                            scored.append((sim, path, cap))
            elif method in ("3", "option3", "tanh"):
                tanh_bm25 = self._tanh(bm25_raw)
                if use_embedding and emb_sims is not None:
                    for idx, (path, cap) in enumerate(items):
                        combined = alpha * tanh_bm25[idx] + (1 - alpha) * emb_sims[idx]
                        if combined >= min_similarity:
                            scored.append((combined, path, cap))
                else:
                    self.logger.warning(
                        "Embedding required for method %s but unavailable, falling back to tanh BM25",
                        combine_method,
                    )
                    for idx, (path, cap) in enumerate(items):
                        sim = float(tanh_bm25[idx])
                        if sim >= min_similarity:
                            scored.append((sim, path, cap))
            elif method in ("4", "option4", "minmax_sigmoid"):
                norm_bm25 = self._minmax_normalize(bm25_raw)
                sig_bm25 = self._sigmoid(bm25_raw)
                for idx, (path, cap) in enumerate(items):
                    combined = alpha * norm_bm25[idx] + (1 - alpha) * sig_bm25[idx]
                    if combined >= min_similarity:
                        scored.append((combined, path, cap))
            elif method in ("5", "option5", "minmax_tanh"):
                norm_bm25 = self._minmax_normalize(bm25_raw)
                tanh_bm25 = self._tanh(bm25_raw)
                for idx, (path, cap) in enumerate(items):
                    combined = alpha * norm_bm25[idx] + (1 - alpha) * tanh_bm25[idx]
                    if combined >= min_similarity:
                        scored.append((combined, path, cap))
            else:
                self.logger.error(
                    "Unknown combine method '%s', falling back to min‑max BM25", combine_method
                )
                norm_bm25 = self._minmax_normalize(bm25_raw)
                for idx, (path, cap) in enumerate(items):
                    sim = float(norm_bm25[idx])
                    if sim >= min_similarity:
                        scored.append((sim, path, cap))

            scored.sort(key=lambda x: x[0], reverse=True)

        top = scored[:limit]
        current_span.set_attribute("search.total_matches", len(top))

        result = {}
        for _sim, _path, cap in top:
            result[cap.name] = FunctionCall(name=cap.name, description=cap.description)
        return result

    @tracer.start_as_current_span("record_cap_history")
    def record(self, msg: Msg, cap: Capability) -> None:
        """
        Record usage of a capability.
        NOTE: Not implemented to avoid large on‑disk history.
        Future version may implement an in‑memory ring buffer.
        """
        self.logger.debug("record() called but not implemented (msg=%s, cap=%s)", msg, cap)

    @tracer.start_as_current_span("getCapsByHistory")
    def getCapsByHistory(
        self, msg: Msg, limit: int = 5, min_similarity: float = 0.5
    ) -> dict[str, Capability]:
        """
        Retrieve capabilities based on usage history.
        NOTE: Not implemented to avoid unbounded on‑disk storage.
        Returns an empty dict to satisfy the interface.
        """
        self.logger.debug("getCapsByHistory() called but not implemented, returning empty dict")
        return {}

    @staticmethod
    def cosine_similarity(vec1, vec2) -> float:
        """Compute cosine similarity between two vectors. Returns 0 if either is None."""
        if vec1 is None or vec2 is None:
            return 0.0
        vec1 = np.array(vec1)
        vec2 = np.array(vec2)
        dot_product = np.dot(vec1, vec2)
        norm_vec1 = np.linalg.norm(vec1)
        norm_vec2 = np.linalg.norm(vec2)
        if norm_vec1 == 0 or norm_vec2 == 0:
            return 0.0
        return float(dot_product / (norm_vec1 * norm_vec2))


"""
Example usage:
    from scl.storage.fsstore import fsstore
    from scl.meta.msg import Msg
    from scl.meta.capability import Capability

    # Initialize the store (embedding service off for BM25 mode)
    store = fsstore(path="/path/to/capabilities", init=True, embedding_service_on=False)

    # Insert a new capability (name & description must be unique)
    new_cap = Capability(name="code_generator", type="tool", description="Generates python code from specs")
    inserted = store.insert_capability(new_cap)  # returns the inserted Capability

    # Insert with force (bypass similarity check, useful when embedding service is off)
    store.insert_capability(another_cap, force=True)

    # Find a capability by name
    cap = store.get_cap_by_name("text_classifier")
    if cap:
        print(cap.name, cap.description)

    # Search using BM25 with min‑max normalization
    query = Msg(messages="generate code from specification")  # Msg uses 'messages' attribute
    results = store.search_by_similarity(query, limit=3, min_similarity=0.2)
    for name, capability in results.items():
        print(f"{name}: {capability.description}")

    # Initialize with embedding service on (embedding similarity)
    store_emb = fsstore(path="/path/to/skills", init=True, embedding_service_on=True)
    # Msg must contain an embedding vector (list of floats) in 'embed' attribute
    query_emb = Msg(embed=[0.1, 0.2, ...])  # messages can be empty if only embedding used
    results_emb = store_emb.search_by_similarity(query_emb, limit=3)
    for name, capability in results_emb.items():
        print(f"{name}: {capability.description}")

    # Combined search: min‑max BM25 + embedding with alpha = 0.7
    results_comb = store_emb.search_by_similarity(query_emb, limit=5, min_similarity=0.3, combine_method="option1", alpha=0.7)
    for name, func_call in results_comb.items():
        print(f"{name}: combined score")

    # Combined BM25 variants only (no embedding required): min‑max + sigmoid
    results_hybrid = store_emb.search_by_similarity(Msg(messages="some query"), limit=3, combine_method="option4", alpha=0.6)

    # Refresh cache after adding/removing skill folders
    store.refresh_cache()
"""

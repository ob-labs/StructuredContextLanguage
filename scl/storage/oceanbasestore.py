import sys
import os
import json
import logging
from typing import Optional, List

# Add the StructuredContextLanguage directory to the path
scl_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(scl_root)

from scl.trace import tracer
from scl.storage.base import StoreBase
from scl.meta.capability import Capability
from scl.meta.msg import Msg
from typing import List

try:
    from pyobvector import (
        VECTOR,
        ObVecClient,
        cosine_distance,
        inner_product,
        l2_distance,
    )
    from pyobvector.schema import ReplaceStmt
    from sqlalchemy import JSON, Column, String, Table, BigInteger, text
    from sqlalchemy.dialects.mysql import LONGTEXT
    logging.info("pyobvector imported successfully")
except ImportError as e:
    logging.error(f"Required dependencies not found: {e}. Please install pyobvector and sqlalchemy.")
    raise


class OceanBaseStore(StoreBase):
    def __init__(self, 
                 host="127.0.0.1", 
                 port="2881", 
                 user="root@test", 
                 password="", 
                 db_name="test",
                 table_name="capabilities",
                 embedding_model_dims=None,
                 init=False):
        """
        Initialize OceanBase database connection
        
        Args:
            host: OceanBase server address
            port: OceanBase server port
            user: Username
            password: Password
            db_name: Database name
            table_name: Table name
            embedding_model_dims: Embedding vector dimensions
            init: Whether to initialize database and table
        """
        self.connection_args = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "db_name": db_name,
        }
        self.table_name = table_name
        self.embedding_model_dims = embedding_model_dims or int(os.getenv("EMBEDDING_MODEL_DIMS", 1024))
        
        # Initialize client
        self._create_client()
        
        if init:
            self.create_table()
    
    def _create_client(self):
        """Create and initialize OceanBase vector client"""
        try:
            password = self.connection_args['password']
            if password is None or password == "":
                logging.warning("Password is empty. If authentication fails, please set OCEANBASE_PASSWORD environment variable.")
            
            self.obvector = ObVecClient(
                uri=f"{self.connection_args['host']}:{self.connection_args['port']}",
                user=self.connection_args['user'],
                password=password or "",  # Ensure password is at least an empty string
                db_name=self.connection_args['db_name'],
            )
            logging.info("OceanBase connection successful!")
        except Exception as e:
            error_msg = str(e)
            logging.error(f"Connection failed: {e}")
            if "Access denied" in error_msg or "password" in error_msg.lower():
                logging.error("Authentication failed. Please check:")
                logging.error(f"  1. Username format: {self.connection_args['user']} (should be 'user@tenant')")
                logging.error(f"  2. Password is set: {'Yes' if self.connection_args.get('password') else 'No (empty or not set)'}")
                logging.error("  3. Set OCEANBASE_PASSWORD environment variable if needed")
                logging.error("  4. For OceanBase Docker, you may need to set a password during initialization")
            else:
                logging.error("Please ensure OceanBase is installed and running")
                logging.error(f"  Host: {self.connection_args['host']}")
                logging.error(f"  Port: {self.connection_args['port']}")
            raise
    
    def close(self):
        """Close database connection"""
        if hasattr(self, 'obvector') and self.obvector:
            # ObVecClient may not have an explicit close method
            # but we can close the connection by disposing the engine
            if hasattr(self.obvector, 'engine'):
                self.obvector.engine.dispose()
            logging.info("Database connection closed")
    
    def create_table(self):
        """Create capability storage table"""
        try:
            # Check if table already exists
            if self.obvector.check_table_exists(self.table_name):
                logging.info(f"Table '{self.table_name}' already exists")
                return
            
            # Define table structure
            cols = [
                # Primary key - use BIGINT
                Column("id", BigInteger, primary_key=True, autoincrement=True),
                # Capability name - unique
                Column("name", String(255), nullable=False, unique=True),
                # Description - LONGTEXT cannot have UNIQUE constraint in OceanBase
                Column("description", LONGTEXT, nullable=False),
                # Type
                Column("type", String(255), nullable=False),
                # Vector field
                Column("embedding_description", VECTOR(self.embedding_model_dims)),
                # Original body
                Column("original_body", LONGTEXT, nullable=False),
                # LLM description - JSON
                Column("llm_description", JSON, nullable=False),
                # Function implementation
                Column("function_impl", LONGTEXT, nullable=False),
            ]
            
            # Create vector index parameters
            vidx_params = self.obvector.prepare_index_params()
            vidx_params.add_index(
                field_name="embedding_description",
                index_type="HNSW",  # Use HNSW index type
                index_name="idx_embedding_description",
                metric_type="l2",  # Use L2 distance
                params={"M": 16, "efConstruction": 200},  # HNSW parameters
            )
            
            # Create table with vector index
            self.obvector.create_table_with_index_params(
                table_name=self.table_name,
                columns=cols,
                indexes=None,
                vidxs=vidx_params,
                partitions=None,
            )
            
            # Create index on name field
            with self.obvector.engine.connect() as conn:
                conn.execute(text(f"CREATE INDEX idx_name ON {self.table_name}(name)"))
                conn.commit()
            
            # Refresh metadata
            self.obvector.refresh_metadata([self.table_name])
            
            logging.info(f"Table '{self.table_name}' created successfully with indexes")
            
        except Exception as e:
            logging.error(f"Failed to create table: {e}")
            raise
    
    @tracer.start_as_current_span("insert_capability")
    def insert_capability(self, cap: Capability):
        """
        Insert a new capability
        
        Args:
            cap: Capability object
            
        Returns:
            ID of the inserted record, or None if failed
        """
        try:
            # Prepare data for insertion
            # Note: llm_description may be a string or dict, need to convert to JSON format
            llm_desc = cap.llm_description
            if isinstance(llm_desc, str):
                try:
                    llm_desc = json.loads(llm_desc)
                except json.JSONDecodeError:
                    llm_desc = {"description": llm_desc}
            elif llm_desc is None:
                llm_desc = {}
            
            # Ensure embedding_description is in list format
            embedding = cap.embedding_description
            if not isinstance(embedding, list):
                # If it's a numpy array or other format, convert to list
                try:
                    embedding = list(embedding)
                except (TypeError, ValueError):
                    logging.error(f"Cannot convert embedding_description to list format: {type(embedding)}")
                    return None
            
            # Build insert record
            record = {
                "name": cap.name,
                "description": cap.description,
                "type": cap.type,
                "embedding_description": embedding,  # Use list directly, pyobvector will handle it
                "original_body": cap.original_body,
                "llm_description": llm_desc,  # JSON type will serialize automatically
                "function_impl": cap.function_impl or "",
            }
            
            # Use ReplaceStmt to implement upsert (update if name already exists)
            table = Table(self.table_name, self.obvector.metadata_obj, autoload_with=self.obvector.engine)
            
            with self.obvector.engine.connect() as conn:
                with conn.begin():
                    # Check if record with same name already exists
                    select_stmt = text(f"SELECT id FROM {self.table_name} WHERE name = :name")
                    result = conn.execute(select_stmt, {"name": cap.name})
                    existing = result.fetchone()
                    
                    if existing:
                        # If exists, update record (REPLACE INTO will replace entire record)
                        record["id"] = existing[0]
                        upsert_stmt = ReplaceStmt(table).values([record])
                        conn.execute(upsert_stmt)
                        cap_id = existing[0]
                        logging.info(f"Capability '{cap.name}' updated successfully, ID: {cap_id}")
                    else:
                        # Insert new record
                        upsert_stmt = ReplaceStmt(table).values([record])
                        conn.execute(upsert_stmt)
                        # Get inserted ID
                        select_id_stmt = text(f"SELECT id FROM {self.table_name} WHERE name = :name")
                        id_result = conn.execute(select_id_stmt, {"name": cap.name})
                        cap_id = id_result.fetchone()[0]
                        logging.info(f"Capability '{cap.name}' inserted successfully, ID: {cap_id}")
            
            return cap_id
            
        except Exception as e:
            logging.error(f"Failed to insert capability: {e}", exc_info=True)
            return None
    
    @tracer.start_as_current_span("get_cap_by_name")
    def get_cap_by_name(self, name) -> Capability:
        """Query capability by name"""
        try:
            with self.obvector.engine.connect() as conn:
                select_sql = text(f"""
                    SELECT 
                        name,
                        type,
                        llm_description,
                        function_impl
                    FROM {self.table_name}
                    WHERE name = :name
                """)
                
                result = conn.execute(select_sql, {"name": name})
                row = result.fetchone()
                
                if row:
                    name_val, type_val, llm_desc, function_impl = row
                    # Parse llm_description JSON
                    try:
                        if isinstance(llm_desc, str):
                            llm_desc = json.loads(llm_desc)
                        elif llm_desc is None:
                            llm_desc = {}
                    except (json.JSONDecodeError, TypeError):
                        llm_desc = llm_desc if llm_desc else {}
                    
                    logging.info(f"Found capability: {name_val}")
                    return Capability(name=name_val, type=type_val, llm_description=llm_desc, function_impl=function_impl)
                else:
                    logging.info(f"Capability with name '{name}' not found")
                    return None
                    
        except Exception as e:
            logging.error(f"Query failed: {e}")
            return None
    
    @tracer.start_as_current_span("search_by_similarity")
    def search_by_similarity(self, msg: Msg, limit=5, min_similarity=0.5) -> List[Capability]:
        """Query capabilities by description similarity"""
        try:
            # Get embedding from Msg object
            query_embedding = msg.embed
            
            # Ensure query_embedding is in list format
            if not isinstance(query_embedding, list):
                try:
                    query_embedding = list(query_embedding)
                except (TypeError, ValueError):
                    logging.error(f"Cannot convert query_embedding to list format: {type(query_embedding)}")
                    return []
            
            # Use pyobvector's ann_search for vector similarity search
            results = self.obvector.ann_search(
                table_name=self.table_name,
                vec_data=query_embedding,
                vec_column_name="embedding_description",
                distance_func=l2_distance,  # Use L2 distance
                with_dist=True,
                topk=limit * 2,  # Get more results for filtering
                output_column_names=[
                    "name",
                    "type",
                    "llm_description",
                ],
            )
            
            similar_functions = []
            for row in results.fetchall():
                # Parse result row
                # Row format depends on the number and order of returned columns
                # Usually: (name, type, llm_description, distance) or similar format
                name_val = row[0]
                type_val = row[1]
                llm_desc = row[2]
                # Distance is usually the last column
                distance = row[-1] if len(row) > 3 else None
                
                # Calculate similarity (smaller L2 distance means higher similarity)
                # Convert distance to similarity: similarity = 1 / (1 + distance)
                # Or use cosine similarity approximation: similarity = 1 - (distance / max_distance)
                if distance is not None:
                    # For normalized vectors, L2 distance range is usually [0, 2]
                    # Use 1 - (distance / 2) as similarity, but ensure it's in reasonable range
                    distance_float = float(distance)
                    # Use more conservative conversion: similarity = 1 / (1 + distance)
                    similarity = 1.0 / (1.0 + distance_float)
                else:
                    similarity = 0.0
                
                # Check similarity threshold
                if similarity < min_similarity:
                    logging.info(f"{name_val} similarity {similarity:.4f} below threshold {min_similarity}")
                    continue
                
                # Parse llm_description JSON
                try:
                    if isinstance(llm_desc, str):
                        llm_desc = json.loads(llm_desc)
                    elif llm_desc is None:
                        llm_desc = {}
                except (json.JSONDecodeError, TypeError):
                    llm_desc = llm_desc if llm_desc else {}
                
                similar_functions.append(Capability(name=name_val, type=type_val, llm_description=llm_desc))
                
                # If we've found enough results meeting the threshold, return early
                if len(similar_functions) >= limit:
                    break
            
            logging.info(f"Found {len(similar_functions)} similar capabilities")
            return similar_functions
            
        except Exception as e:
            logging.error(f"Similarity search failed: {e}", exc_info=True)
            return []
    
    @tracer.start_as_current_span("record_cap_history_safe")
    def record(self, msg: Msg, cap: Capability):
        """
        Record a query embedding and its associated capability.
        
        Args:
            msg (Msg): The message object containing the embedding vector
            cap (Capability): The capability associated with the embedding
            
        Returns:
            None
        """
        # OceanBase storage doesn't need to record history
        # This method is provided for interface compatibility
        return

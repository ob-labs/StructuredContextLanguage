import psycopg2
import sys
import os
import json
import logging
# Add the StructuredContextLanguage directory to the path
scl_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(scl_root)

from scl.trace import tracer
from scl.storage.base import StoreBase
from scl.meta.base import Capability
Vector = None
register_vector_info = None
try:
    from pgvector import Vector
    from pgvector.psycopg2 import register_vector_info
    logging.info("pgvector imported successfully")
except ImportError as e:
    logging.info(f"Warning: pgvector not installed or import failed: {e}")
    Vector = None
    register_vector_info = None


class PgVectorStore(StoreBase):
    def __init__(self, dbname="postgres", user="postgres", password="your_password", 
                 host="localhost", port="5432", init=False):
        """
        初始化数据库连接
        """
        self.db_params = {
            "dbname": dbname,
            "user": user,
            "password": password,
            "host": host,
            "port": port
        }
                
        self.conn = None
        self.connect()
        if init:
            self.create_database()
            self.enable_vector_extension()
            self.create_table()
            
    def connect(self):
        """连接到数据库"""
        try:
            self.conn = psycopg2.connect(**self.db_params)
            if Vector is not None:
                try:
                    # Try to register vector type
                    cursor = self.conn.cursor()
                    cursor.execute("SELECT typname, oid, typarray FROM pg_type WHERE typname = 'vector'")
                    result = cursor.fetchone()
                    cursor.close()
                    if result:
                        register_vector_info(result[1], result[2], self.conn)
                except Exception as e:
                    logging.info(f"警告: 无法注册vector类型: {e}")
            logging.info("数据库连接成功！")
        except psycopg2.OperationalError as e:
            logging.info(f"连接失败: {e}")
            logging.info("请确保PostgreSQL已安装并运行")
            sys.exit(1)

    
    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            logging.info("数据库连接已关闭")
    
    def create_database(self):
        """从零开始创建数据库（需要先连接到默认数据库）"""
        # 首先连接到默认的postgres数据库
        default_params = self.db_params.copy()
        default_params["dbname"] = "postgres"
        
        try:
            conn = psycopg2.connect(**default_params)
            conn.autocommit = True
            cursor = conn.cursor()
            
            # 检查数据库是否存在
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (self.db_params["dbname"],))
            exists = cursor.fetchone()
            
            if not exists:
                # 创建新数据库
                cursor.execute(f"CREATE DATABASE {self.db_params['dbname']}")
                logging.info(f"数据库 '{self.db_params['dbname']}' 创建成功！")
            else:
                logging.info(f"数据库 '{self.db_params['dbname']}' 已存在")
            
            cursor.close()
            conn.close()
            
            # 重新连接到新创建的数据库
            self.connect()
            
        except Exception as e:
            logging.info(f"创建数据库失败: {e}")
    
    def enable_vector_extension(self):
        """启用pgvector扩展"""
        try:
            cursor = self.conn.cursor()
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            self.conn.commit()
            cursor.close()
            logging.info("pgvector扩展已启用")
        except Exception as e:
            logging.info(f"启用扩展失败: {e}")
            self.conn.rollback()
    
    def create_table(self):
        """创建函数存储表"""
        try:
            cursor = self.conn.cursor()
            
            # 获取嵌入模型的维度
            embedding_dims = os.getenv("EMBEDDING_MODEL_DIMS", 1024)
            create_table_sql = f"""
            CREATE TABLE IF NOT EXISTS capabilities (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL UNIQUE,
                description TEXT NOT NULL UNIQUE,
                type VARCHAR(255) NOT NULL,
                embedding_description vector({embedding_dims}),
                original_body TEXT NOT NULL,
                llm_description JSONB NOT NULL,
                function_impl TEXT NOT NULL
            );
            """
            
            cursor.execute(create_table_sql)
            
            # 创建索引以提高查询性能
            # 为function_name创建索引
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_name ON capabilities(name);")
            
            # 为llm_description创建GIN索引以加速JSON查询
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_llm_description ON capabilities USING GIN (llm_description);")
            
            # 为vector字段创建IVFFLAT索引以加速相似性搜索
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_embedding_description
                ON capabilities 
                USING ivfflat (embedding_description vector_l2_ops)
                WITH (lists = 100);
            """)
            
            self.conn.commit()
            cursor.close()
            logging.info("表格创建成功，并已建立索引")
            
        except Exception as e:
            logging.info(f"创建表格失败: {e}")
            self.conn.rollback()
    
    @tracer.start_as_current_span("insert_capability")
    def insert_capability(self, cap:Capability):
        """
        插入新函数
        
        Args:
            cap: Capability
        """
        try:                                    
            # 生成description的嵌入向量
            #embedding = Vector(cap.embedding_description)
            cursor = self.conn.cursor()
            
            insert_sql = """
            INSERT INTO capabilities (name, description, type, embedding_description, original_body, llm_description, function_impl)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            RETURNING id;
            """
            logging.info(f"Inserting function: {cap.name}, {cap.description}, {cap.type}, {cap.original_body}, {cap.function_impl}")
            cursor.execute(insert_sql, (cap.name, cap.description, cap.type, cap.embedding_description, cap.original_body, cap.llm_description, cap.function_impl))
            cap_id = cursor.fetchone()[0]
            
            self.conn.commit()
            cursor.close()
            
            logging.info(f"函数 '{cap.name}' 插入成功，ID: {cap_id}")
            return cap_id
            
        except psycopg2.errors.UniqueViolation as e:
            logging.info(f"函数名 '{cap.name}' 已存在（唯一约束违规）{e}")
            self.conn.rollback()
            return None
        except Exception as e:
            logging.info(f"插入函数失败: {e}")
            self.conn.rollback()
            return None

    @tracer.start_as_current_span("get_cap_by_name")
    def get_cap_by_name(self, name):
        """根据函数名查询"""
        try:
            cursor = self.conn.cursor()
            
            select_sql = """
            SELECT 
                name,
                type,
                llm_description,
                function_impl
            FROM capabilities
            WHERE name = %s;
            """
            
            cursor.execute(select_sql, (name,))
            result = cursor.fetchall()
            
            cursor.close()

            if result:
                row = result[0]
                logging.info(row)
                try:
                    llm_desc = json.loads(row[2]) if row[2] else {}
                except:
                    llm_desc = row[2]
                return [{"name":row[0],"type":row[1],"desc":llm_desc,"function_impl":row[3]}]
            else:
                logging.info(f"未找到名为 '{name}' 的能力")
                return []
            
        except Exception as e:
            logging.info(f"查询失败: {e}")
            return []
    
    @tracer.start_as_current_span("search_by_similarity")
    def search_by_similarity(self, query_embedding, limit=5, min_similarity=0.5):
        """根据描述相似度查询函数"""
        try:
            # 为查询文本生成嵌入向量)
            cursor = self.conn.cursor()
            
            search_sql = """
            SELECT 
                name,
                type,
                llm_description,
                1 - (embedding_description <=> %s::vector) as similarity
            FROM capabilities
            ORDER BY embedding_description <=> %s::vector
            LIMIT %s;
            """
            
            cursor.execute(search_sql, (query_embedding, query_embedding, limit))
            results = cursor.fetchall()
            
            cursor.close()
            
            if results:
                similar_functions = []
                for row in results:
                    try:
                        llm_desc = json.loads(row[2]) if row[2] else {}
                    except:
                        llm_desc = row[2]
                    if float(row[3]) < min_similarity:
                        logging.info(f"{row[0]} 相似度 {row[3]} 低于阈值 {min_similarity}")
                        continue
                    # 解析llm_description JSON
                    similar_functions.append({"name":row[0],"type":row[1],"desc":llm_desc})
                
                logging.info(f"找到 {len(similar_functions)} 个相似函数")
                return similar_functions
            else:
                logging.info("未找到相似函数")
                return []
            
        except Exception as e:
            logging.info(f"相似性搜索失败: {e}")
            return []

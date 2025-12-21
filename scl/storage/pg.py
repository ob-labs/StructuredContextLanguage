import psycopg2
import sys
import os
import json
import logging
# Add the StructuredContextLanguage directory to the path
scl_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(scl_root)

from scl.embeddings.impl import OpenAIEmbedding
from scl.trace import tracer

# Import pgvector Vector class for proper vector handling
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

class PgVectorFunctionStore:
    def __init__(self, dbname="postgres", user="postgres", password="your_password", 
                 host="localhost", port="5432", embedding_service=None):
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
        
        # 初始化文本嵌入模型
        self.embedding_service = embedding_service
        
        self.conn = None
        self.connect()
    
    def connect(self):
        """连接到数据库"""
        try:
            self.conn = psycopg2.connect(**self.db_params)
            # Register vector type if available
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
            CREATE TABLE IF NOT EXISTS functions (
                id SERIAL PRIMARY KEY,
                function_name VARCHAR(255) NOT NULL UNIQUE,
                function_body TEXT NOT NULL,
                llm_description JSONB NOT NULL,
                function_description TEXT NOT NULL,
                function_metadata vector({embedding_dims})
            );
            """
            
            cursor.execute(create_table_sql)
            
            # 创建索引以提高查询性能
            # 为function_name创建索引
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_function_name ON functions(function_name);")
            
            # 为llm_description创建GIN索引以加速JSON查询
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_llm_description ON functions USING GIN (llm_description);")
            
            # 为vector字段创建IVFFLAT索引以加速相似性搜索
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_function_metadata 
                ON functions 
                USING ivfflat (function_metadata vector_l2_ops)
                WITH (lists = 100);
            """)
            
            self.conn.commit()
            cursor.close()
            logging.info("表格创建成功，并已建立索引")
            
        except Exception as e:
            logging.info(f"创建表格失败: {e}")
            self.conn.rollback()
    
    @tracer.start_as_current_span("generate_embedding")
    def generate_embedding(self, text):
        """生成文本的嵌入向量"""
        embedding = self.embedding_service.embed(text)
        # Convert to Vector type if available
        if Vector is not None:
            return Vector(embedding)
        return embedding
    
    @tracer.start_as_current_span("check_function_exists")
    def check_function_exists(self, function_name):
        """检查函数名是否已存在"""
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM functions WHERE function_name = %s;", (function_name,))
            count = cursor.fetchone()[0]
            cursor.close()
            return count > 0
        except Exception as e:
            logging.info(f"检查函数名失败: {e}")
            return True  # 如果检查失败，保守地认为存在
    
    @tracer.start_as_current_span("insert_function")
    def insert_function(self, function_name, function_body, llm_description, function_description):
        """
        插入新函数
        
        Args:
            function_name: 函数名（必须唯一）
            function_body: 函数体代码
            llm_description: OpenAI函数调用格式的描述字典
                example: {'type': 'function', 'function': {'name': 'add', ...}}
            function_description: 用于生成嵌入向量的文本描述
        """
        try:
            # 检查函数名是否已存在
            if self.check_function_exists(function_name):
                logging.info(f"函数名 '{function_name}' 已存在，插入失败")
                return None
            
            # 验证llm_description格式
            if not isinstance(llm_description, dict):
                logging.info(f"llm_description必须是字典类型，当前类型: {type(llm_description)}")
                return None
            
            # 将llm_description转换为JSON字符串（PostgreSQL的JSONB类型可以接受）
            try:
                llm_description_json = json.dumps(llm_description)
            except Exception as e:
                logging.info(f"llm_description JSON序列化失败: {e}")
                return None
            
            # 生成description的嵌入向量
            embedding = self.generate_embedding(function_description)
            
            cursor = self.conn.cursor()
            
            insert_sql = """
            INSERT INTO functions (function_name, function_body, llm_description, function_description, function_metadata)
            VALUES (%s, %s, %s::jsonb, %s, %s)
            RETURNING id;
            """
            
            cursor.execute(insert_sql, (function_name, function_body, llm_description_json, function_description, embedding))
            function_id = cursor.fetchone()[0]
            
            self.conn.commit()
            cursor.close()
            
            logging.info(f"函数 '{function_name}' 插入成功，ID: {function_id}")
            return function_id
            
        except psycopg2.errors.UniqueViolation as e:
            logging.info(f"函数名 '{function_name}' 已存在（唯一约束违规）")
            self.conn.rollback()
            return None
        except Exception as e:
            logging.info(f"插入函数失败: {e}")
            self.conn.rollback()
            return None
    
    @tracer.start_as_current_span("update_function")
    def update_function(self, function_id=None, function_name=None, function_body=None, llm_description=None, function_description=None):
        """
        更新函数信息
        
        Args:
            function_id: 函数ID（优先使用）
            function_name: 函数名（如果没有提供function_id）
            llm_description: OpenAI函数调用格式的描述字典
        """
        try:
            cursor = self.conn.cursor()
            
            # 确定要更新的函数ID
            target_id = function_id
            if not target_id and function_name:
                cursor.execute("SELECT id FROM functions WHERE function_name = %s;", (function_name,))
                result = cursor.fetchone()
                if result:
                    target_id = result[0]
                else:
                    logging.info(f"未找到函数名 '{function_name}'")
                    cursor.close()
                    return False
            elif not target_id:
                logging.info("请提供function_id或function_name")
                cursor.close()
                return False
            
            update_fields = []
            params = []
            
            if function_body:
                update_fields.append("function_body = %s")
                params.append(function_body)
            
            if llm_description:
                # 验证并转换llm_description
                if not isinstance(llm_description, dict):
                    logging.info(f"llm_description必须是字典类型，当前类型: {type(llm_description)}")
                    cursor.close()
                    return False
                
                try:
                    llm_description_json = json.dumps(llm_description)
                    update_fields.append("llm_description = %s::jsonb")
                    params.append(llm_description_json)
                except Exception as e:
                    logging.info(f"llm_description JSON序列化失败: {e}")
                    cursor.close()
                    return False
            
            if function_description:
                update_fields.append("function_description = %s")
                params.append(function_description)
                # 如果description更新了，也需要更新嵌入向量
                embedding = self.generate_embedding(function_description)
                update_fields.append("function_metadata = %s")
                params.append(embedding)
            
            if not update_fields:
                logging.info("没有提供更新字段")
                cursor.close()
                return False
            
            params.append(target_id)  # 添加ID作为最后一个参数
            
            update_sql = f"""
            UPDATE functions
            SET {', '.join(update_fields)}
            WHERE id = %s;
            """
            
            cursor.execute(update_sql, params)
            
            if cursor.rowcount > 0:
                self.conn.commit()
                logging.info(f"函数 ID {target_id} 更新成功")
                cursor.close()
                return True
            else:
                logging.info(f"未找到函数 ID {target_id}")
                cursor.close()
                return False
            
        except Exception as e:
            logging.info(f"更新函数失败: {e}")
            self.conn.rollback()
            if cursor:
                cursor.close()
            return False
    
    @tracer.start_as_current_span("get_function_by_name")
    def get_function_by_name(self, function_name):
        """根据函数名查询"""
        try:
            cursor = self.conn.cursor()
            
            select_sql = """
            SELECT id, function_name, function_body, llm_description, function_description
            FROM functions
            WHERE function_name = %s;
            """
            
            cursor.execute(select_sql, (function_name,))
            result = cursor.fetchall()
            
            cursor.close()
            
            if result:
                functions = []
                for row in result:
                    # 解析llm_description JSON
                    try:
                        llm_desc = json.loads(row[3]) if row[3] else {}
                    except:
                        llm_desc = row[3]
                    functions.append(llm_desc)
                logging.info(f"找到 {len(functions)} 个名为 '{function_name}' 的函数")
                return functions
            else:
                logging.info(f"未找到名为 '{function_name}' 的函数")
                return []
            
        except Exception as e:
            logging.info(f"查询失败: {e}")
            return []
    
    @tracer.start_as_current_span("search_by_similarity")
    def search_by_similarity(self, query_text, limit=5, min_similarity=0.5):
        """根据描述相似度查询函数"""
        try:
            # 为查询文本生成嵌入向量
            query_embedding = self.generate_embedding(query_text)
            
            cursor = self.conn.cursor()
            
            search_sql = """
            SELECT 
                function_name,
                function_body,
                llm_description,
                function_description,
                1 - (function_metadata <=> %s::vector) as similarity
            FROM functions
            ORDER BY function_metadata <=> %s::vector
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
                    if float(row[4]) < min_similarity:
                        logging.info(f"{row[0]} 相似度 {row[4]} 低于阈值 {min_similarity}")
                        continue
                    # 解析llm_description JSON
                    similar_functions.append(llm_desc)
                
                logging.info(f"找到 {len(similar_functions)} 个相似函数")
                return similar_functions
            else:
                logging.info("未找到相似函数")
                return []
            
        except Exception as e:
            logging.info(f"相似性搜索失败: {e}")
            return []
    
    @tracer.start_as_current_span("delete_function")
    def delete_function(self, function_id=None, function_name=None):
        """删除函数（可按ID或名称删除）"""
        try:
            cursor = self.conn.cursor()
            
            if function_id:
                delete_sql = "DELETE FROM functions WHERE id = %s;"
                params = (function_id,)
                message = f"ID {function_id}"
            elif function_name:
                delete_sql = "DELETE FROM functions WHERE function_name = %s;"
                params = (function_name,)
                message = f"名称 '{function_name}'"
            else:
                logging.info("请提供function_id或function_name")
                return False
            
            cursor.execute(delete_sql, params)
            
            if cursor.rowcount > 0:
                self.conn.commit()
                logging.info(f"删除 {message} 成功，删除了 {cursor.rowcount} 行")
                cursor.close()
                return True
            else:
                logging.info(f"未找到 {message} 对应的函数")
                cursor.close()
                return False
            
        except Exception as e:
            logging.info(f"删除失败: {e}")
            self.conn.rollback()
            if cursor:
                cursor.close()
            return False
    
    @tracer.start_as_current_span("list_all_functions")
    def list_all_functions(self, limit=10):
        """列出所有函数（用于调试）"""
        try:
            cursor = self.conn.cursor()
            
            cursor.execute("""
            SELECT id, function_name, function_body, llm_description, function_description
            FROM functions
            LIMIT %s;
            """, (limit,))
            
            results = cursor.fetchall()
            cursor.close()
            
            # 解析JSON字段
            parsed_results = []
            for row in results:
                try:
                    llm_desc = json.loads(row[3]) if row[3] else {}
                except:
                    llm_desc = row[3]
                
                parsed_results.append((
                    row[0],  # id
                    row[1],  # function_name
                    row[2],  # function_body
                    llm_desc,  # llm_description (parsed)
                    row[4]   # function_description
                ))
            
            return parsed_results
            
        except Exception as e:
            logging.info(f"查询失败: {e}")
            return []


# 更新示例使用代码
def main():
    # 初始化函数存储
    function_store = PgVectorFunctionStore(
        dbname="postgres",
        user="postgres",
        password="postgres",  # 请修改为您的密码
        host="localhost",
        port="5432",
        embedding_service=OpenAIEmbedding()
    )
    
    try:
        # 1. 从零开始创建数据库和表格
        function_store.create_database()
        function_store.enable_vector_extension()
        function_store.create_table()
        
        # 2. 插入示例数据（使用新的llm_description格式）
        print("\n=== 插入示例数据 ===")
        
        # 第一个函数
        func1_id = function_store.insert_function(
            function_name="calculate_sum",
            function_body="""
def calculate_sum(numbers):
    \"\"\"计算列表中所有数字的和\"\"\"
    return sum(numbers)
            """,
            llm_description={
                'type': 'function',
                'function': {
                    'name': 'calculate_sum',
                    'description': '计算数字列表中所有元素的总和',
                    'parameters': {
                        'type': 'object',
                        'properties': {
                            'numbers': {
                                'type': 'array',
                                'items': {'type': 'number'},
                                'description': '要计算的数字列表',
                            },
                        },
                        'required': ['numbers'],
                    },
                }
            },
            function_description="计算列表中所有数字的总和"
        )
        
        # 第二个函数
        func2_id = function_store.insert_function(
            function_name="find_max",
            function_body="""
def find_max(numbers):
    \"\"\"找到列表中的最大值\"\"\"
    if not numbers:
        return None
    return max(numbers)
            """,
            llm_description={
                'type': 'function',
                'function': {
                    'name': 'find_max',
                    'description': '在数字列表中查找最大值',
                    'parameters': {
                        'type': 'object',
                        'properties': {
                            'numbers': {
                                'type': 'array',
                                'items': {'type': 'number'},
                                'description': '要查找最大值的数字列表',
                            },
                        },
                        'required': ['numbers'],
                    },
                }
            },
            function_description="在数字列表中查找最大值"
        )
        
        # 尝试插入重复的函数名（应该失败）
        print("\n=== 测试重复函数名插入 ===")
        duplicate_id = function_store.insert_function(
            function_name="calculate_sum",  # 重复的名称
            function_body="def test(): pass",
            llm_description={
                'type': 'function',
                'function': {'name': 'test', 'description': 'test'}
            },
            function_description="测试重复函数名"
        )
        
        if duplicate_id is None:
            print("✓ 成功阻止重复函数名插入")
        
        # 3. 根据函数名查询
        print("\n=== 根据函数名查询 ===")
        functions = function_store.get_function_by_name("calculate_sum")
        for func in functions:
            print(f"ID: {func['id']}, 名称: {func['name']}")
            print(f"LLM描述: {func['llm_description']}")
            print("-" * 50)
        
        # 4. 根据相似性查询
        print("\n=== 根据相似性查询 ===")
        similar_funcs = function_store.search_by_similarity(
            query_text="如何计算数字的总和？",
            limit=3
        )
        
        for func in similar_funcs:
            print(f"函数: {func}")
            print("-" * 50)
        
        # 5. 更新函数（使用新的llm_description格式）
        print("\n=== 修改数据 ===")
        if func1_id:
            function_store.update_function(
                function_id=func1_id,
                llm_description={
                    'type': 'function',
                    'function': {
                        'name': 'calculate_sum',
                        'description': '计算数字列表中所有元素的总和（优化版本）',
                        'parameters': {
                            'type': 'object',
                            'properties': {
                                'numbers': {
                                    'type': 'array',
                                    'items': {'type': 'number'},
                                    'description': '要计算的数字数组',
                                },
                                'ignore_none': {
                                    'type': 'boolean',
                                    'description': '是否忽略None值',
                                    'default': False
                                }
                            },
                            'required': ['numbers'],
                        },
                    }
                },
                function_description="计算数字列表中所有元素的总和（加法运算）"
            )
        
        # 验证修改
        print("\n=== 验证修改 ===")
        updated_func = function_store.get_function_by_name("calculate_sum")
        if updated_func:
            print(f"更新后的LLM描述: {updated_func[0]['llm_description']}")
        
        # 6. 列出所有函数
        print("\n=== 当前所有函数 ===")
        all_funcs = function_store.list_all_functions()
        for func in all_funcs:
            print(f"ID: {func[0]}, 名称: {func[1]}")
            print(f"LLM描述类型: {type(func[3])}")
        
    finally:
        # 关闭连接
        function_store.close()


if __name__ == "__main__":
    main()
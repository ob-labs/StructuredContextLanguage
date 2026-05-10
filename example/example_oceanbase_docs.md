# OceanBase Storage - Quick Start Guide

This guide provides a quick start for using OceanBase as the storage backend for Structured Context Language.

## Prerequisites

1. **Python 3.8+** installed
2. **Database** (choose one):
   - **OceanBase** (for OceanBase storage option - recommended for production)
3. **API Keys** for LLM and embedding services

### Step 1: Install Dependencies

```bash
cd /home/jingshun.tq/project/StructuredContextLanguage
pip install -r requirements.txt
```

### Step 2. Start OceanBase

#### Using Docker (Recommended for Testing)

```bash
docker run -d --name oceanbase-ce \
  -p 2881:2881 \
  -p 2882:2882 \
  -e MINI_MODE=1 \
  oceanbase/oceanbase-ce:latest
```

**Wait for OceanBase to start** (2-3 minutes on first run):
```bash
docker logs -f oceanbase-ce
# Look for "boot success" message
```

### Step 3: Obversbility —— otel.

```bash
docker run -p 8000:8000 -p 4317:4317 -p 4318:4318 ghcr.io/ctrlspice/otel-desktop-viewer:latest-amd64
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4317"
export OTEL_TRACES_EXPORTER="otlp"
export OTEL_EXPORTER_OTLP_PROTOCOL="grpc"

```

### Step 4: Set Up Environment Variables

Create a `.env` file in the project root or export the following environment variables:

### Step 5. Set Environment Variables

```bash
# OceanBase Connection
export OCEANBASE_HOST="127.0.0.1"
export OCEANBASE_PORT="2881"
export OCEANBASE_USER="root@test"
export OCEANBASE_PASSWORD=""
export OCEANBASE_DB_NAME="test"

# LLM Configuration
export API_KEY="your-openai-api-key"
export BASE_URL="https://api.openai.com/v1"
export MODEL="gpt-4"

# Embedding Configuration (SiliconFlow is used by default)
export EMBEDDING_API_KEY="your-siliconflow-api-key"
export EMBEDDING_BASE_URL="https://api.siliconflow.cn/v1"
export EMBEDDING_MODEL="BAAI/bge-large-zh-v1.5"
export EMBEDDING_MODEL_DIMS="1024"

# Optional: OpenTelemetry for observability
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4317"
export OTEL_TRACES_EXPORTER="otlp"
export OTEL_EXPORTER_OTLP_PROTOCOL="grpc"

OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4317"
OTEL_TRACES_EXPORTER="otlp"
OTEL_EXPORTER_OTLP_PROTOCOL="grpc"
```

### 6. Run the Example

```bash
cd /home/jingshun.tq/project/StructuredContextLanguage/exampleAPI
python example_oceanbase.py
```

## Code Example

```python
from scl.storage.oceanbasestore import OceanBaseStore
from scl.cap_reg import CapRegistry
from scl.llm_chat import function_call_playground
from openai import OpenAI
import os

# Initialize OpenAI client
client = OpenAI(
    api_key=os.getenv("API_KEY"),
    base_url=os.getenv("BASE_URL")
)

# Initialize OceanBase storage
caps = OceanBaseStore(
    host=os.getenv("OCEANBASE_HOST", "127.0.0.1"),
    port=os.getenv("OCEANBASE_PORT", "2881"),
    user=os.getenv("OCEANBASE_USER", "root@test"),
    password=os.getenv("OCEANBASE_PASSWORD", ""),
    db_name=os.getenv("OCEANBASE_DB_NAME", "test"),
    table_name="capabilities",
    embedding_model_dims=int(os.getenv("EMBEDDING_MODEL_DIMS", "1024")),
    init=True  # Creates table and indexes on first run
)

# Create capability registry
cap_registry = CapRegistry(caps)

# Insert capabilities
# ... (see example_oceanbase.py for details)

# Use in chat
messages = [{'role': 'user', 'content': 'Your question here'}]
result = function_call_playground(
    client, 
    os.getenv("MODEL"), 
    cap_registry, 
    [], 
    messages
)

# Close connection when done
caps.close()
```

## Configuration Options

### OceanBaseStore Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `host` | str | `"127.0.0.1"` | OceanBase server address |
| `port` | str | `"2881"` | OceanBase server port |
| `user` | str | `"root@test"` | Username (format: `user@tenant`) |
| `password` | str | `""` | Password |
| `db_name` | str | `"test"` | Database name |
| `table_name` | str | `"capabilities"` | Table name for storing capabilities |
| `embedding_model_dims` | int | `1024` | Embedding vector dimensions |
| `init` | bool | `False` | Create table and indexes on initialization |

### Table Structure

The `OceanBaseStore` creates a table with the following structure:

- `id` (BIGINT, PRIMARY KEY, AUTO_INCREMENT)
- `name` (VARCHAR(255), UNIQUE) - Capability name
- `description` (LONGTEXT) - Capability description (Note: LONGTEXT cannot have UNIQUE constraint in OceanBase)
- `type` (VARCHAR(255)) - Capability type
- `embedding_description` (VECTOR) - Vector embedding for similarity search
- `original_body` (LONGTEXT) - Original capability body
- `llm_description` (JSON) - LLM-formatted description
- `function_impl` (LONGTEXT) - Function implementation code

### Vector Index

The store automatically creates an HNSW vector index on `embedding_description`:
- Index type: HNSW
- Distance metric: L2
- Parameters: M=16, efConstruction=200

## Features

### 1. Automatic Table Creation
- Tables and indexes are created automatically when `init=True`
- Safe to run multiple times (checks if table exists)

### 2. Vector Similarity Search
- Efficient similarity search using HNSW index
- Configurable similarity threshold
- Returns top-k most similar capabilities

### 3. Upsert Support
- `insert_capability()` automatically handles updates
- If a capability with the same name exists, it will be updated

### 4. JSON Metadata Storage
- LLM descriptions stored as JSON
- Supports complex metadata structures

## Troubleshooting

### Connection Issues

**Error: Connection failed**
```bash
# Check if OceanBase is running
docker ps | grep oceanbase

# Check OceanBase logs
docker logs oceanbase-ce

# Verify connection parameters
# Make sure OceanBase is fully started (wait for "boot success")
```

**Error: Authentication failed**
- Verify username format: `user@tenant` (e.g., `root@test`)
- Check password is correct
- Ensure user has permissions on the database

### Table Creation Issues

**Error: Table already exists**
- This is normal if you've run the script before
- The code will skip table creation if table exists
- To recreate, drop the table manually or use a different `table_name`

**Error: Vector index creation failed**
- Ensure OceanBase version is 4.3.5.1 or above
- Check if vector extension is enabled
- Verify `embedding_model_dims` is set correctly

### Import Errors

**Error: No module named 'pyobvector'**
```bash
pip install pyobvector sqlalchemy
```

**Error: No module named 'sqlalchemy'**
```bash
pip install sqlalchemy
```

### Performance Issues

**Slow similarity search:**
- Ensure vector index is created (check with `init=True`)
- Verify `embedding_model_dims` matches your embedding model
- Consider adjusting HNSW parameters for your use case

**Connection timeout:**
- OceanBase may take time to start (especially first run)
- Wait for "boot success" in logs before connecting
- Check network connectivity

## Advanced Usage

### Custom Table Name

```python
caps = OceanBaseStore(
    host="127.0.0.1",
    port="2881",
    user="root@test",
    password="",
    db_name="test",
    table_name="my_custom_table",  # Custom table name
    embedding_model_dims=1024,
    init=True
)
```

### Manual Table Management

```python
# Create table manually
caps = OceanBaseStore(..., init=False)
caps.create_table()

# Close connection
caps.close()
```

### Query Capabilities

```python
# Get capability by name
result = caps.get_cap_by_name("add")
print(result)

# Search by similarity
query_embedding = [0.1, 0.2, ...]  # Your query embedding
similar = caps.search_by_similarity(
    query_embedding, 
    limit=5, 
    min_similarity=0.5
)
print(similar)
```

## Best Practices

1. **Always set `init=True` on first run** to create tables and indexes
2. **Use environment variables** for connection parameters
3. **Close connections** when done: `caps.close()`
4. **Match embedding dimensions** with your embedding model
5. **Use appropriate table names** for different use cases
6. **Monitor OceanBase logs** for connection issues

## Next Steps

- See `exampleAPI/example_oceanbase.py` for full example
- See `scl/storage/oceanbasestore.py` for implementation details


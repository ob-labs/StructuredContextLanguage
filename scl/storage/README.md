# Storage Interface

This module provides a standardized interface for storing and retrieving functions/skills with various backends.

## Architecture

The storage system follows an abstract interface pattern with the following components:

- `FunctionStoreBase`: Abstract base class defining the common interface
- `SkillStore`: File-based storage implementation
- `PgVectorFunctionStore`: PostgreSQL + pgvector database implementation

## Interface Methods

All storage implementations must provide these methods:

- `generate_embedding(text)`: Generate embedding vector for text
- `insert_function(function_name, function_body, llm_description, function_description)`: Insert a new function
- `update_function(...)`: Update an existing function
- `get_function_by_name(function_name)`: Retrieve function by name
- `search_by_similarity(query_text, limit, min_similarity)`: Semantic search for functions
- `delete_function(...)`: Delete a function
- `list_all_functions(limit)`: List all stored functions

## Usage

### File-based Storage (SkillStore)

```python
from scl.storage.skillstore import SkillStore
from scl.embeddings.impl import OpenAIEmbedding

# Initialize storage
store = SkillStore(folder="./functions", embedding_service=OpenAIEmbedding())

# Insert a function
store.insert_function(
    function_name="calculate_sum",
    function_body="def calculate_sum(numbers): return sum(numbers)",
    llm_description={
        'type': 'function',
        'function': {
            'name': 'calculate_sum',
            'description': 'Calculate the sum of numbers in a list',
            'parameters': {
                'type': 'object',
                'properties': {
                    'numbers': {
                        'type': 'array',
                        'items': {'type': 'number'},
                    },
                },
                'required': ['numbers'],
            },
        }
    },
    function_description="Calculate the sum of numbers in a list"
)
```

### Database Storage (PgVectorFunctionStore)

```python
from scl.storage.pg import PgVectorFunctionStore
from scl.embeddings.impl import OpenAIEmbedding

# Initialize storage
store = PgVectorFunctionStore(
    dbname="postgres",
    user="postgres",
    password="your_password",
    host="localhost",
    port="5432",
    embedding_service=OpenAIEmbedding()
)
```

## Benefits

- **Pluggable backends**: Switch between file-based and database storage without changing application code
- **Consistent API**: Same interface regardless of storage implementation
- **Extensible**: Easy to add new storage backends by implementing the base interface
- **Type safety**: Interface enforces consistent method signatures
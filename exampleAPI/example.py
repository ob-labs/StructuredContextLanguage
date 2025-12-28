## init for test  
import os
from openai import OpenAI
import sys
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
# Add the parent directory to the path to enable imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scl.function_reg import FunctionRegistry
from scl.embeddings.impl import OpenAIEmbedding
from scl.storage.pg import PgVectorFunctionStore
from scl.llm_chat import function_call_playground

client = OpenAI(
    api_key=os.getenv("API_KEY",""),
    base_url=os.getenv("BASE_URL","")
)
model = os.getenv("MODEL","")

function_store = PgVectorFunctionStore(
            dbname="postgres",
            user="postgres",
            password="postgres",  # 请修改为您的密码
            host="localhost",
            port="5432",
            init=True,
            embedding_service=OpenAIEmbedding()
        )
registry = FunctionRegistry(function_store)

## Function Reg
registry.insert_function(
    function_name="add",
    function_body="""
def add(a: float, b: float):
    return a + b
""",
    llm_description={'type': 'function',
    'function': {
        'name': 'add',
        'description': 'Compute the sum of two numbers',
        'parameters': {
            'type': 'object',
            'properties': {
                'a': {
                    'type': 'integer',
                    'description': 'A number',
                },
                'b': {
                    'type': 'integer',
                    'description': 'A number',
                },
            },
            'required': ['a', 'b'],
        },
    }
},
    function_description="计算两数之和"
)

registry.insert_function(
    function_name="mul",
    function_body="""
def mul(a: float, b: float):
    return a * b
""",
    llm_description={'type': 'function',
    'function': {
        'name': 'mul',
        'description': 'Calculate the product of two numbers',
        'parameters': {
            'type': 'object',
            'properties': {
                'a': {
                    'type': 'integer',
                    'description': 'A number',
                },
                'b': {
                    'type': 'integer',
                    'description': 'A number',
                },
            },
            'required': ['a', 'b'],
        },
    }
},
    function_description="计算两数之积"
)

registry.insert_function(
    function_name="count_letter",
    function_body="""
def count_letter(text, letter):
    return text.count(letter)
""",
    llm_description={'type': 'function',
    'function': {
        'name': 'count_letter',
        'description': 'Count the number of times a letter appears in a text',
        'parameters': {
            'type': 'object',
            'properties': {
                'text': {
                    'type': 'string',
                    'description': 'The text to search in',
                },
                'letter': {
                    'type': 'string',
                    'description': 'The letter to count',
                },
            },
            'required': ['text', 'letter'],
        },
    }
},
    function_description="计算字母在单词中出现的次数"
)


registry.insert_function(
    function_name="compare",
    function_body="""
def compare(a: float, b: float):
    if a > b:
        return f'{a} is greater than {b}'
    elif a < b:
        return f'{b} is greater than {a}'
    else:
        return f'{a} is equal to {b}'
""",
    llm_description={'type': 'function',
    'function': {
        'name': 'compare',
        'description': 'Compare two number, which one is bigger',
        'parameters': {
            'type': 'object',
            'properties': {
                'a': {
                    'type': 'number',
                    'description': 'A number',
                },
                'b': {
                    'type': 'number',
                    'description': 'A number',
                },
            },
            'required': ['a', 'b'],
        },
    }
},
    function_description="比较两个数字，哪个更大"
)

## Test with chat
### Function call Autonomy by RAG
# | Case number | File format | Context RAG | Memory | Function call | 
# | 0 | n/A | n/A | n/A | n/A |
messages = [{'role': 'user', 'content': "用中文回答：单词strawberry中有多少个字母r?"}]
print(function_call_playground(client, model, registry, messages, []))
## case ? test with function call with hit
# | ? | n/A | n/A | n/A | Autonomy |
messages = [{'role': 'user', 'content': "用中文回答：9.11和9.9，哪个小?"}]
print(function_call_playground(client, model, registry, messages, []))

### Function call Autonomy by RAG + User specific
## case 6 test with function call with hit
# | 6 | n/A | by config | n/A | by config |
messages = [{'role': 'user', 'content': "用中文回答：单词strawberry中有多少个字母r?"}]
print(function_call_playground(client, model, registry, messages, ["count_letter"]))
messages = [{'role': 'user', 'content': "用中文回答：9.11和9.9，哪个小?"}]
print(function_call_playground(client, model, registry, messages, ["compare"]))

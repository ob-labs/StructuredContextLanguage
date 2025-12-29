## todo: refactor, this file stores functions for now.
from scl.meta.functioncall import FunctionCall
import json
def add(a: float, b: float):
    return a + b

def mul(a: float, b: float):
    return a * b

def compare(a: float, b: float):
    if a > b:
        return f'{a} is greater than {b}'
    elif a < b:
        return f'{b} is greater than {a}'
    else:
        return f'{a} is equal to {b}'

def count_letter(text: str, letter: str):
    string = text.lower()
    letter = letter.lower()
    
    count = string.count(letter)
    return(f"The letter '{letter}' appears {count} times in the string.")

FunCallAdd = FunctionCall(
    name="add", 
    description="计算两数之和",
    original_body="""
def add(a: float, b: float):
    return a + b
""",
    llm_description=json.dumps({'type': 'function',
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
}),
    function_impl="""
def add(a: float, b: float):
    return a + b
""",
)
FunCalMul = FunctionCall(
    name="mul", 
    description="计算两数之积",
    original_body="""
def mul(a: float, b: float):
    return a * b
""",
    llm_description=json.dumps({'type': 'function',
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
}),
    function_impl="""
def mul(a: float, b: float):
    return a * b
""",
)
FunCalCountLetter = FunctionCall(
    name="count_letter", 
    description="计算字母在单词中出现的次数",
    original_body="""
def count_letter(text, letter):
    return text.count(letter)
""",
    llm_description=json.dumps({'type': 'function',
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
}),
    function_impl="""
def count_letter(text, letter):
    return text.count(letter)
""",
)
FunCalCompare = FunctionCall(
    name="compare", 
    description="比较两个数字，哪个更大",
    original_body="""
def compare(a: float, b: float):
    if a > b:
        return f'{a} is greater than {b}'
    elif a < b:
        return f'{b} is greater than {a}'
    else:
        return f'{a} is equal to {b}'
""",
    llm_description=json.dumps({'type': 'function',
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
}),
    function_impl="""
def compare(a: float, b: float):
    if a > b:
        return f'{a} is greater than {b}'
    elif a < b:
        return f'{b} is greater than {a}'
    else:
        return f'{a} is equal to {b}'
""",
)
## todo: refactor, this file stores functions for now.
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

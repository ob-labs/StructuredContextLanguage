## init for test  
import os
from openai import OpenAI
import sys
import logging
import random
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
# Add the parent directory to the path to enable imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scl.cap_reg import CapRegistry
from scl.meta.msg import Msg
from scl.storage.pgstore import PgVectorStore
from scl.llm_chat import function_call_playground
# Import utils functions - adding current directory to path for relative imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import *

def test():
    client = OpenAI(
        api_key=os.getenv("API_KEY",""),
        base_url=os.getenv("BASE_URL","")
    )
    model = os.getenv("MODEL","")

    caps = PgVectorStore(
                dbname="postgres",
                user="postgres",
                password="postgres",  # 请修改为您的密码
                host="localhost",
                port="5432",
                init=True
            )
    cap_registry = CapRegistry(caps)

    ## Function Reg
    caps.insert_capability(FunCallAdd)
    caps.insert_capability(FunCalMul)
    caps.insert_capability(FunCalCountLetter)
    caps.insert_capability(FunCalCompare)
    ## Test with chat
    ### Function call Autonomy by RAG
    # | Case number | File format | Context RAG | Memory | Function call | 
    # | 0 | n/A | n/A | n/A | n/A |
    ## case ? test with function call with hit
    # | ? | n/A | n/A | n/A | Autonomy |
    num1 = round(random.uniform(1, 20), 2)
    num2 = round(random.uniform(1, 20), 2)
    messages = [{'role': 'user', 'content': f"用中文回答：{num1}和{num2}，哪个小?"}]
    msg = Msg(messages)
    print(function_call_playground(client, model, cap_registry,[], msg))

    ### Function call Autonomy by RAG + User specific
    ## case 6 test with function call with hit
    # | 6 | n/A | by config | n/A | by config |
    num1 = round(random.uniform(1, 20), 2)
    num2 = round(random.uniform(1, 20), 2)
    messages = [{'role': 'user', 'content': f"用中文回答：{num1}和{num2}，哪个小?"}]
    msg = Msg(messages)
    print(function_call_playground(client, model, cap_registry,["compare"], msg))

    ### Function call Autonomy as learn from history(memory)
    num1 = round(random.uniform(1, 20), 2)
    num2 = round(random.uniform(1, 20), 2)
    messages = [{'role': 'user', 'content': f"用中文回答：{num1}和{num2}，哪个小?"}]
    msg = Msg(messages)
    print(function_call_playground(client, model, cap_registry,[], msg))

    ### scl.learn, behavior changes, as feedback loop effective
    ### metric and goals are hardcode as input parameter.
    ### num1 = round(random.uniform(1, 20), 2)
    ### num2 = round(random.uniform(1, 20), 2)
    ### messages = [{'role': 'user', 'content': f"用中文回答：{num1}和{num2}，哪个小?"}]
    ### msg = Msg(messages)
    ### print(function_call_playground(client, model, cap_registry,[], msg))

if __name__ == "__main__":
    test()
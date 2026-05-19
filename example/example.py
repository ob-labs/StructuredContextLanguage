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

from scl.cap_reg import CapRegistry
from scl.meta.msg import Msg
from scl.storage.pgstore import PgVectorStore
from scl.llm_chat import function_call_playground
# Import utils functions - adding current directory to path for relative imports
import os
import sys
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
    messages = [{'role': 'user', 'content': "用中文回答：单词strawberry中有多少个字母r?"}]
    msg = Msg(messages)
    print(function_call_playground(client, model, cap_registry,[], msg))
    ## case ? test with function call with hit
    # | ? | n/A | n/A | n/A | Autonomy |
    messages = [{'role': 'user', 'content': "用中文回答：9.11和9.9，哪个小?"}]
    msg = Msg(messages)
    print(function_call_playground(client, model, cap_registry,[], msg))

    ### Function call Autonomy by RAG + User specific
    ## case 6 test with function call with hit
    # | 6 | n/A | by config | n/A | by config |
    messages = [{'role': 'user', 'content': "用中文回答：单词strawberry中有多少个字母r?"}]
    msg = Msg(messages)
    print(function_call_playground(client, model, cap_registry,["count_letter"], msg))
    messages = [{'role': 'user', 'content': "用中文回答：9.11和9.9，哪个小?"}]
    msg = Msg(messages)
    print(function_call_playground(client, model, cap_registry,["compare"], msg))

    ### Function call Autonomy as learn from history(memory)
    messages = [{'role': 'user', 'content': "用中文回答：单词strawberry中有多少个字母r?"}]
    msg = Msg(messages)
    print(function_call_playground(client, model, cap_registry,[], msg))
    messages = [{'role': 'user', 'content': "用中文回答：9.11和9.9，哪个小?"}]
    msg = Msg(messages)
    print(function_call_playground(client, model, cap_registry,[], msg))

if __name__ == "__main__":
    test()
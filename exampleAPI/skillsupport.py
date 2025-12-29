## Ref https://github.com/agentskills/agentskills
### for a question
### go through github.com:anthropics/skills.git as example
### use an Autonomy way to load the skill
### it should be like golang chan style instead of load everything into DB
### function_reg should be an interface....
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
from scl.embeddings.impl import OpenAIEmbedding
from scl.storage.fsstore import fsstore
from scl.llm_chat import function_call_playground


def test():
    client = OpenAI(
        api_key=os.getenv("API_KEY",""),
        base_url=os.getenv("BASE_URL","")
    )
    model = os.getenv("MODEL","")

    caps = fsstore(
                path="./scl/storage/skills/skills",
                init=False, # for 1st run, please set to True to make you a cache.
            )
    registry = CapRegistry(caps)
    ## Test with chat
    ### Function call Autonomy by RAG
    # | Case number | File format | Context RAG | Memory | Function call | 
    # | 0 | n/A | n/A | n/A | n/A |
    messages = [{'role': 'user', 'content': "Creating algorithmic art using p5.js with seeded randomness and interactive parameter exploration."}]
    print(function_call_playground(client, model, registry, messages, []))
    ## case ? test with function call with hit
    # | ? | n/A | n/A | n/A | Autonomy |
    messages = [{'role': 'user', 'content': "用中文回答：9.11和9.9，哪个小?"}]
    print(function_call_playground(client, model, registry, messages, []))

if __name__ == "__main__":
    test()
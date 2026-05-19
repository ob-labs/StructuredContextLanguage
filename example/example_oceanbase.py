## OceanBase Storage Example
import os
import sys
import logging

# Load environment variables from .env file FIRST, before importing other modules
try:
    from dotenv import load_dotenv
    # Load .env from project root directory
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    if os.path.exists(env_path):
        load_dotenv(env_path, override=True)  # override=True to ensure .env values take precedence
        print(f"Loaded environment variables from {env_path}")
    else:
        # Fallback to default behavior (current directory or parent directories)
        load_dotenv(override=True)
        print("Loaded environment variables from default locations")
except ImportError:
    print("WARNING: python-dotenv not installed. Install it with: pip install python-dotenv")
    print("WARNING: Environment variables will only be read from system environment.")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Add the parent directory to the path to enable imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import OpenAI

from scl.cap_reg import CapRegistry
from scl.storage.oceanbasestore import OceanBaseStore
from scl.llm_chat import function_call_playground
from scl.meta.msg import Msg
# Import utils functions - adding current directory to path for relative imports
import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import *

def test():
    # Get base URL and convert to DashScope compatible mode if needed
    base_url = os.getenv("BASE_URL", "")
    
    client = OpenAI(
        api_key=os.getenv("API_KEY",""),
        base_url=base_url
    )
    model = os.getenv("MODEL","")

    # Initialize OceanBase storage
    # Make sure OceanBase is running and accessible
    oceanbase_password = os.getenv("OCEANBASE_PASSWORD")
    if oceanbase_password is None:
        print("=" * 50)
        print("WARNING: OCEANBASE_PASSWORD environment variable is not set!")
        print("Please set it using: export OCEANBASE_PASSWORD='your_password'")
        print("For default OceanBase Docker setup, you may need to set a password.")
        print("=" * 50)
        oceanbase_password = ""  # Use empty string as fallback
    
    # Support both OCEANBASE_DB_NAME and OCEANBASE_DATABASE for backward compatibility
    db_name = os.getenv("OCEANBASE_DB_NAME") or os.getenv("OCEANBASE_DATABASE", "test")
    
    caps = OceanBaseStore(
        host=os.getenv("OCEANBASE_HOST", "127.0.0.1"),
        port=os.getenv("OCEANBASE_PORT", "2881"),
        user=os.getenv("OCEANBASE_USER", "root@test"),
        password=oceanbase_password,
        db_name=db_name,
        table_name="capabilities",
        embedding_model_dims=int(os.getenv("EMBEDDING_MODEL_DIMS", "1024")),
        init=True  # Create table and indexes on first run
    )
    cap_registry = CapRegistry(caps)

    ## Function Registration
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
    print("=" * 50)
    print("Test 1: Autonomous function call by RAG")
    print("=" * 50)
    print(function_call_playground(client, model, cap_registry, [], msg))
    
    ## case ? test with function call with hit
    # | ? | n/A | n/A | n/A | Autonomy |
    messages = [{'role': 'user', 'content': "用中文回答：9.11和9.9，哪个小?"}]
    msg = Msg(messages)
    print("\n" + "=" * 50)
    print("Test 2: Autonomous function call by RAG")
    print("=" * 50)
    print(function_call_playground(client, model, cap_registry, [], msg))

    ### Function call Autonomy by RAG + User specific
    ## case 6 test with function call with hit
    # | 6 | n/A | by config | n/A | by config |
    messages = [{'role': 'user', 'content': "用中文回答：单词strawberry中有多少个字母r?"}]
    msg = Msg(messages)
    print("\n" + "=" * 50)
    print("Test 3: Function call with user-specified hint (count_letter)")
    print("=" * 50)
    print(function_call_playground(client, model, cap_registry, ["count_letter"], msg))
    
    messages = [{'role': 'user', 'content': "用中文回答：9.11和9.9，哪个小?"}]
    msg = Msg(messages)
    print("\n" + "=" * 50)
    print("Test 4: Function call with user-specified hint (compare)")
    print("=" * 50)
    print(function_call_playground(client, model, cap_registry, ["compare"], msg))
    
    # Close connection
    caps.close()
    print("\n" + "=" * 50)
    print("OceanBase connection closed")
    print("=" * 50)

if __name__ == "__main__":
    test()


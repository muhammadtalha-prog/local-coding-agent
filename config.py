import sys
import os
from dotenv import load_dotenv

# Ensure stdout and stderr support UTF-8 formatting on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Load local environment variables from .env file
load_dotenv()

# Workspace setup
WORKSPACE_DIR = os.getenv("AGENT_WORKSPACE", "./workspace")
if not os.path.exists(WORKSPACE_DIR):
    os.makedirs(WORKSPACE_DIR)

# LLM Selection Settings
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")  # "ollama" or "deepseek"
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder")

# Pre-populate DeepSeek credentials
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-f3884e1040304b97a7f36147df604e77")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

def get_llm():
    """
    Initializes and returns the selected Chat Model (Ollama or DeepSeek API).
    """
    if LLM_PROVIDER.lower() == "deepseek":
        from langchain_openai import ChatOpenAI
        if not DEEPSEEK_API_KEY:
            raise ValueError("DeepSeek API Key is missing. Please set DEEPSEEK_API_KEY in your .env file.")
        
        print(f"🤖 Initializing DeepSeek Chat API ({DEEPSEEK_MODEL})...")
        return ChatOpenAI(
            model=DEEPSEEK_MODEL,
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com/v1",
            temperature=0.2
        )
    else:
        from langchain_ollama import ChatOllama
        print(f"🤖 Initializing Local Ollama Inference ({OLLAMA_MODEL}) at {OLLAMA_HOST}...")
        return ChatOllama(
            model=OLLAMA_MODEL,
            base_url=OLLAMA_HOST,
            temperature=0.2
        )

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

def clean_env_var(value, default=""):
    if value is None:
        return default
    return str(value).strip().strip("'\"")

# Workspace setup
WORKSPACE_DIR = clean_env_var(os.getenv("AGENT_WORKSPACE"), "./workspace")
if not os.path.exists(WORKSPACE_DIR):
    os.makedirs(WORKSPACE_DIR)

# LLM Selection Settings
LLM_PROVIDER = clean_env_var(os.getenv("LLM_PROVIDER"), "ollama")  # "ollama", "deepseek", "grok", or "groq"
OLLAMA_HOST = clean_env_var(os.getenv("OLLAMA_HOST"), "http://localhost:11434")
OLLAMA_MODEL = clean_env_var(os.getenv("OLLAMA_MODEL"), "qwen2.5-coder")

# Pre-populate DeepSeek credentials
DEEPSEEK_API_KEY = clean_env_var(os.getenv("DEEPSEEK_API_KEY"), "")
DEEPSEEK_MODEL = clean_env_var(os.getenv("DEEPSEEK_MODEL"), "deepseek-chat")

# Pre-populate Grok credentials
GROK_API_KEY = clean_env_var(os.getenv("GROK_API_KEY"), "")
GROK_MODEL = clean_env_var(os.getenv("GROK_MODEL"), "grok-beta")

# Pre-populate Groq credentials
GROQ_API_KEY = clean_env_var(os.getenv("GROQ_API_KEY"), "")
GROQ_MODEL = clean_env_var(os.getenv("GROQ_MODEL"), "llama-3.3-70b-versatile")

# Standardized Timeouts (in seconds)
DEFAULT_HTTP_TIMEOUT = int(clean_env_var(os.getenv("DEFAULT_HTTP_TIMEOUT"), "5"))
DEFAULT_LLM_TIMEOUT = int(clean_env_var(os.getenv("DEFAULT_LLM_TIMEOUT"), "60"))
DEFAULT_COMMAND_TIMEOUT = int(clean_env_var(os.getenv("DEFAULT_COMMAND_TIMEOUT"), "60"))

def get_llm():
    """
    Initializes and returns the selected Chat Model (Ollama, DeepSeek, Grok, or Groq API).
    Automatically falls back to Groq or Grok if Ollama is unresponsive and keys are present.
    """
    provider = LLM_PROVIDER.lower()

    # Fallback to local Ollama if missing API keys for cloud providers
    if provider == "deepseek" and not DEEPSEEK_API_KEY:
        print("⚠️ Warning: LLM_PROVIDER is set to 'deepseek' but DEEPSEEK_API_KEY is empty.")
        print("👉 Falling back to local 'ollama' provider.")
        provider = "ollama"
        
    if provider == "grok" and not GROK_API_KEY:
        print("⚠️ Warning: LLM_PROVIDER is set to 'grok' but GROK_API_KEY is empty.")
        print("👉 Falling back to local 'ollama' provider.")
        provider = "ollama"

    if provider == "groq" and not GROQ_API_KEY:
        print("⚠️ Warning: LLM_PROVIDER is set to 'groq' but GROQ_API_KEY is empty.")
        print("👉 Falling back to local 'ollama' provider.")
        provider = "ollama"

    if provider == "deepseek":
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
    elif provider == "grok":
        from langchain_openai import ChatOpenAI
        if not GROK_API_KEY:
            raise ValueError("Grok API Key is missing. Please set GROK_API_KEY in your .env file.")
        
        print(f"🤖 Initializing Grok Chat API ({GROK_MODEL})...")
        return ChatOpenAI(
            model=GROK_MODEL,
            api_key=GROK_API_KEY,
            base_url="https://api.x.ai/v1",
            temperature=0.2
        )
    elif provider == "groq":
        from langchain_openai import ChatOpenAI
        if not GROQ_API_KEY:
            raise ValueError("Groq API Key is missing. Please set GROQ_API_KEY in your .env file.")
        
        print(f"🤖 Initializing Groq Chat API ({GROQ_MODEL})...")
        return ChatOpenAI(
            model=GROQ_MODEL,
            api_key=GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
            temperature=0.2
        )
    else:
        # Check if local Ollama is online/responsive and query installed models
        import urllib.request
        import json
        ollama_available = False
        installed_models = []
        try:
            with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=DEFAULT_HTTP_TIMEOUT) as response:
                if response.status == 200:
                    ollama_available = True
                    data = json.loads(response.read().decode('utf-8'))
                    if "models" in data:
                        installed_models = [m["name"] for m in data["models"]]
        except Exception:
            pass

        if not ollama_available:
            if GROQ_API_KEY:
                from langchain_openai import ChatOpenAI
                print(f"⚠️ Local Ollama at {OLLAMA_HOST} is offline/unresponsive. Falling back to Groq Chat API ({GROQ_MODEL})...")
                return ChatOpenAI(
                    model=GROQ_MODEL,
                    api_key=GROQ_API_KEY,
                    base_url="https://api.groq.com/openai/v1",
                    temperature=0.2
                )
            elif GROK_API_KEY:
                from langchain_openai import ChatOpenAI
                print(f"⚠️ Local Ollama at {OLLAMA_HOST} is offline/unresponsive. Falling back to Grok Chat API ({GROK_MODEL})...")
                return ChatOpenAI(
                    model=GROK_MODEL,
                    api_key=GROK_API_KEY,
                    base_url="https://api.x.ai/v1",
                    temperature=0.2
                )

        resolved_model = OLLAMA_MODEL
        if ollama_available and installed_models:
            installed_lower = [m.lower() for m in installed_models]
            if OLLAMA_MODEL.lower() not in installed_lower:
                # Target model is not exactly installed. Check if we have a tag variation of the same base model.
                base_target = OLLAMA_MODEL.split(':')[0].lower()
                matching_base_model = None
                for m in installed_models:
                    if m.split(':')[0].lower() == base_target:
                        matching_base_model = m
                        break
                
                if matching_base_model:
                    print(f"⚠️ Configured Ollama model '{OLLAMA_MODEL}' not found exactly, but base model is available.")
                    print(f"👉 Resolving to installed variant: '{matching_base_model}'")
                    resolved_model = matching_base_model
                else:
                    # No version of the target model is installed. Apply priority fallbacks.
                    fallback_model = None
                    for m in installed_models:
                        if "qwen2.5-coder" in m.lower():
                            fallback_model = m
                            break
                    if not fallback_model:
                        for m in installed_models:
                            if "qwen2.5" in m.lower():
                                fallback_model = m
                                break
                    if not fallback_model:
                        for m in installed_models:
                            if "deepseek-coder" in m.lower():
                                fallback_model = m
                                break
                    if not fallback_model:
                        for m in installed_models:
                            if "codellama" in m.lower():
                                fallback_model = m
                                break
                    if not fallback_model:
                        fallback_model = installed_models[0]
                        
                    print(f"⚠️ Configured Ollama model '{OLLAMA_MODEL}' is not installed in local registry.")
                    print(f"👉 Falling back to installed model: '{fallback_model}' (Available models: {installed_models})")
                    resolved_model = fallback_model

        from langchain_ollama import ChatOllama
        print(f"🤖 Initializing Local Ollama Inference ({resolved_model}) at {OLLAMA_HOST}...")
        return ChatOllama(
            model=resolved_model,
            base_url=OLLAMA_HOST,
            temperature=0.2,
            timeout=DEFAULT_LLM_TIMEOUT
        )

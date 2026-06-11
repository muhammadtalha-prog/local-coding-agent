from langchain_ollama import ChatOllama
import sys

try:
    print("Initializing ChatOllama...")
    llm = ChatOllama(model="qwen2.5-coder", base_url="http://localhost:11434", temperature=0.2)
    print("Invoking ChatOllama...")
    res = llm.invoke("Say hello")
    print("Response received:")
    print(res)
except Exception as e:
    print(f"Error occurred: {e}", file=sys.stderr)

# Local Coding Agent - Stateful Self-Testing Developer

**Local Coding Agent** is an autonomous developer assistant built using **Python, LangChain, and LangGraph**. 

Given a set of Python workspace files (which can be empty, skeletons, or fully coded with comments) and a project description, the agent executes an agentic loop: it designs, writes, and automatically executes code in a dedicated virtual environment sandbox, analyzing standard error logs/tracebacks to autonomously refactor and correct its own work until all unit runs and assert tests pass successfully.

---

## 📐 Architecture & Flowchart

The agent uses **LangGraph** to model state transitions, enabling structured loop-backs whenever a code compilation or logic assert fails.

```
                  ┌──────────────────────────────┐
                  │ Start: User Input (Py Files) │
                  └──────────────┬───────────────┘
                                 │
                                 ▼
                  ┌──────────────────────────────┐
                  │ Load Coder Model via Ollama  │
                  └──────────────┬───────────────┘
                                 │
                                 ▼
                  ┌──────────────────────────────┐
                  │ LangGraph Analyst: Plan Task │
                  └──────────────┬───────────────┘
                                 │
                                 ▼
                  ┌──────────────────────────────┐
  ┌──────────────►│    LangGraph Coder Node      │
  │               │ (Write/Edit Python Files)   │
  │               └──────────────┬───────────────┘
  │                              │
  │                              ▼
  │               ┌──────────────────────────────┐
  │               │   Reviewer Node (Test Venv)  │
  │               └──────────────┬───────────────┘
  │                              │
  │                              ▼
  │                     /─────────────────\
  │                    <   Tests Passed?   >
  │                     \─────────────────/
  │                       /            \
  │                    No/              \Yes
  │                     /                \
  ┌─  Self-Correction: /                  \
  │  Feed tracebacks  /                    ▼
  │  back to model   /             ┌────────────────┐
  │                 /              │Success: Deploy │
  └─────────────────               └────────────────┘
```

---

## 🛠️ Project Structure

* `config.py` — Configures workspace targets and initializes the inference engine (Ollama `qwen2.5-coder` or DeepSeek API).
* `tools.py` — LangChain tools mapping filesystem hooks (`read`, `write`, `list`) and subprocess test runners.
* `prompt.py` — Instructs the agent on refactoring loops, stack trace analysis, and coding guidelines.
* `agent.py` — Graph engine defining compilation nodes, routing edges, and retry limits.
* `main.py` — Interactive command-line launcher taking project scopes.
* `requirements.txt` — Package specifications.
* `.env` — Local configuration variables (Provider, hosts, keys).

---

## 🚀 How to Setup and Run

### Step 1: Initialize local inference (Ollama)
1. Install [Ollama](https://ollama.com/).
2. Pull the coder model:
   ```bash
   ollama pull qwen2.5-coder
   ```
3. Keep the Ollama service running.

### Step 2: Configure Environment
1. Open a terminal in `e:\Local coding agent`.
2. Create and activate a virtual environment for the orchestrator:
   ```bash
   python -m venv venv
   # Windows:
   .\venv\Scripts\activate
   # Mac/Linux:
   source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### Step 3: Run a Code Generation Task
1. Execute the launcher script:
   ```bash
   python main.py
   ```
2. Enter your project description (e.g. *"Create a python program that performs matrix multiplications and add assertions to test the calculations"*).
3. The agent will boot Qwen/DeepSeek, analyze your workspace (`./workspace`), write the python code, automatically invoke python to run and test the calculations, inspect tracebacks if it makes syntax or calculation mistakes, fix itself, and deploy the working project.

---

## 📌 Update for Sir Hamza (Presentation Summary)

Here is a summarized update you can present to your supervisor regarding the implementation details:

* **Engine Options**: Deployed with a double-layered backend:
  1. **Ollama Qwen 2.5 Coder** (100% free, runs locally, no API keys needed).
  2. **DeepSeek API** (cloud fallback using OpenAI-compatible headers).
* **Agentic Framework**: Built utilizing **LangGraph** (StateGraph nodes) for managing stateful memory, enabling a clean refactor loop when tests fail.
* **Reviewer Node (Beauty of the Model)**: We implemented a `test_python_file_tool` that runs code inside a subprocess. If the output contains syntax warnings or traceback errors, the script returns the compiler logs back to the model, triggering the self-correction node.
* **Workspace Sandbox**: The agent operates inside a separate `./workspace` directory and initializes a **nested local python venv** (`workspace/venv`) so that any test executions or package installations (`pip install`) are isolated and do not conflict with your main OS python libraries.

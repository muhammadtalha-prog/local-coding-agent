# 🤖 Local Coding Agent - Secure Avionics Multi-Agent System

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![vLLM](https://img.shields.io/badge/vLLM-Local%20Inference-orange.svg)](https://github.com/vllm-project/vllm)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An autonomous multi-agent developer assistant designed for secure local environments. The system plans, generates, lints, tests, and self-corrects code inside an isolated Virtual Machine (venv) sandbox, running entirely offline using **vLLM**.

---

## ⚡ Key Features

- 🖥️ **100% Local Inference & Execution** - Complete removal of external APIs (Gemini, Groq) to ensure security and zero cloud data leaks.
- ⚙️ **vLLM Integration** - High-performance local LLM execution via OpenAI-compatible API servers.
- 🛡️ **VM Sandbox Isolation** - Executes generated code and tests inside an isolated `venv` virtual machine workspace to protect the host device from unintended file modifications.
- 📉 **Lag & Hang Prevention** - Strict resource constraints and child process tree cleanup (`taskkill` on Windows) prevent infinite loops or compile hangs from lagging your PC.
- 🧩 **Specialized Agent Pipeline**:
  - **Planning Agent**: Designs system architecture and safety contracts.
  - **Coding Agent**: Translates plans into typed, production-ready code.
  - **Testing Agent**: Writes comprehensive `pytest` suites.
  - **Debugger Agent**: Corrects failures dynamically based on VM error trace logs.
  - **Review Agent**: Performs DO-178C avionics safety/compliance audits.
  - **Deploy Agent**: Rewrites code into user-interactive CLI prompts.

---

## 📋 Getting Started

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/local-coding-agent.git
cd local-coding-agent

# Create virtual environment
python -m venv venv
.\venv\Scripts\activate  # Linux/Mac: source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.template` to `.env` and set up the local vLLM settings:
```bash
copy .env.template .env
```
Ensure your `.env` contains:
```ini
LLM_PROVIDER=vllm
VLLM_API_BASE=http://localhost:8000/v1
VLLM_MODEL=Qwen/Qwen2.5-Coder-7B-Instruct
```

### 3. Start local vLLM Server

Run the startup helper script to launch the local OpenAI-compatible endpoint:
```bash
# On Windows
start_vllm.bat
```
Or start manually via CLI:
```bash
python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen2.5-Coder-7B-Instruct --host 127.0.0.1 --port 8000
```

### 4. Run the Agent

Run the main agent CLI:
```bash
python cli.py -d "Create a rental property ticket management system with user authentication, ticket creation, assignment, and reporting. Include safety contracts for input validation and data integrity."
```

---

## ⚠️ Important Notes

* **GPU Memory**: Ensure you have enough dedicated GPU memory for local model inference (e.g. ~8-10GB VRAM for Qwen2.5-Coder 7B).
* **vLLM Must Be Running**: The CLI validates that the local vLLM endpoint is active before initiating the coordinator.
* **VM Isolation**: All code checking (Ruff, Mypy) and execution checks (Pytest, python running) happen isolated inside `vm_sandbox/work/sandbox/` to prevent modifications to the parent workspace.
* **Windows Paths**: The system implements proper Windows path handling throughout commands and directory setups.

---

## 📁 Project Architecture

* `cli.py` - Core entry point; performs startup validations.
* `head.py` - Main orchestrator managing the multi-agent execution pipeline.
* `vm_manager.py` - Initializes and manages the virtual environment VM execution sandbox.
* `llm.py` - Communicates with the local vLLM model.
* `settings.py` - Safety parameters, system prompts, and folder configurations.
* `coder.py` - Specialized code-generation agent.
* `tester.py` - Specialized unit test suite generator.
* `debugger.py` - Specialized self-correction agent.
* `linter.py` - Runs Ruff and Mypy inside the sandbox.
* `executor.py` - Runs test suites and programs inside the sandbox.
* `review.py` - Safety reviewer and auditor.
* `deploy.py` - Prepares verified code for interactive user deployment.

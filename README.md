# 🧮 MATLAB Code Generation Agent

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![Ollama](https://img.shields.io/badge/Ollama-Local%20LLM-orange.svg)](https://ollama.com)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A lightweight local AI agent that **plans, generates, executes, and self-corrects MATLAB code** entirely on your machine using a small 1.5–3B language model via Ollama.

Designed for constrained hardware — works on **8GB RAM** with no GPU required.

---

## ⚡ Key Features

- 🧠 **4-agent pipeline**: Planner → Coder → Executor → Debugger
- 🔁 **Self-correcting**: Automatically fixes MATLAB errors (up to 3 retries)
- 🚫 **No toolbox dependencies**: All algorithms implemented with base MATLAB only
- 💾 **Low memory**: Only 3 Python packages required; model runs in ~2GB RAM
- ⚡ **Fast**: No LangChain, no LiteLLM, no Docker — direct Ollama API calls
- 📁 **Workspace output**: Verified `.m` files saved to `workspace/<name>/`

---

## 📋 Getting Started

### 1. Install Ollama

```bash
# Windows — download from https://ollama.com/download
# Or via winget:
winget install Ollama.Ollama
```

### 2. Pull a Model

```bash
# Recommended for 8GB RAM:
ollama pull qwen2.5-coder:3b       # ~2.2GB — best balance
ollama pull qwen2.5-coder:1.5b     # ~1.1GB — fastest, for very low RAM
```

### 3. Set Up the Project

```powershell
# Clone / navigate to project
cd "D:\Local coding agent"

# Fix PowerShell script execution policy (one-time, only if you see "running scripts is disabled")
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# Create virtual environment
python -m venv venv
.\venv\Scripts\activate

# Install dependencies (only 3 packages!)
pip install -r requirements.txt
```

> **Alternative (no activation needed):** Call the venv Python directly:
> ```powershell
> & "D:\Local coding agent\venv\Scripts\python.exe" main.py --task "your task here"
> ```

### 4. Configure (Optional)

```bash
copy .env.template .env
# Edit .env if your MATLAB is installed at a non-standard path
```

### 5. Run

```powershell
# Single task
python main.py --task "Generate a moving average filter function"

# Interactive mode
python main.py

# Use a smaller model for faster generation
python main.py --task "Create a sine wave generator" --model qwen2.5-coder:1.5b

# Override timeout and retries
python main.py --task "Simulate a DC motor" --timeout 150 --retries 3
```

---

## 📁 Project Structure

```
D:\Local coding agent\
├── main.py                  # Entry point -- orchestrates the pipeline
├── config.py                # All settings (LLM, MATLAB paths, timeouts)
├── agents/
│   ├── llm_client.py        # Thin Ollama API client
│   ├── planner.py           # JSON plan generator (in-memory only)
│   ├── coder.py             # MATLAB .m code generator
│   ├── debugger.py          # Error-fix agent
│   └── matlab_executor.py   # MATLAB subprocess runner
├── sandbox/                 # Temp workspace (code during pipeline, auto-cleaned)
├── workspace/               # Output: verified .m files saved here
├── .env                     # Your configuration (gitignored)
├── .env.template            # Configuration template
└── requirements.txt         # 3 packages only
```

---

## 🔧 Configuration

All settings live in `.env` (copy from `.env.template`):

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_MODEL` | `qwen2.5-coder:3b` | Model to use |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama server URL |
| `LLM_MAX_TOKENS` | `2048` | Max tokens per LLM call |
| `LLM_TEMPERATURE` | `0.15` | Randomness (lower = more deterministic) |
| `LLM_TIMEOUT_SEC` | `120.0` | Per-call LLM timeout |
| `MATLAB_PATH` | auto-detected | Path to `matlab.exe` |
| `MATLAB_EXEC_TIMEOUT_SEC` | `120.0` | MATLAB subprocess timeout |
| `MAX_DEBUG_RETRIES` | `3` | Max debug loop iterations |
| `AUTO_APPROVE` | `true` | Skip human approval before saving |

---

## 💻 Hardware Requirements

| Component | Minimum | Recommended |
|---|---|---|
| RAM | 6GB | 8GB |
| Storage | 5GB free | 10GB free |
| CPU | 4 cores | 8 cores |
| GPU | None needed | Optional (speeds up Ollama) |

### Model RAM Usage (Q4 quantization)

| Model | RAM | Notes |
|---|---|---|
| `qwen2.5-coder:1.5b` | ~1.1GB | Fastest, good for simple tasks |
| `qwen2.5-coder:3b` | ~2.2GB | **Recommended** — best quality/speed |
| `phi4-mini:3.8b` | ~2.5GB | Strong reasoning |
| `qwen2.5-coder:7b` | ~4.5GB | Highest quality, tight on 8GB |

---

## ⚠️ Important Notes

- **MATLAB is optional**: If not installed, the agent generates `.m` code and saves it — just without executing it
- **No toolbox functions**: The agent is instructed to avoid all licensed MATLAB toolboxes. If a toolbox function is unavoidable, the pipeline will halt with a clear explanation
- **Ollama must be running**: Start it with `ollama serve` before running the agent

---

## 📝 Example Output

```
Task: Generate a moving average filter function

OK  Ollama OK -- model 'qwen2.5-coder:3b' is available.
>> [1/3] Planner Agent: Designing MATLAB function...
OK  Plan ready: moving_average_filter.m
>> [2/3] Coder Agent: Generating MATLAB code...
OK  Code written to sandbox: moving_average_filter.m
>> [3/3] Executing & Verifying MATLAB code...
OK  Execution passed!
OK  Saved to workspace:
   >> D:\Local coding agent\workspace\moving_average_filter\moving_average_filter.m

DONE  in 34.7s
```

# 🚀 Local CPU-Optimized Ollama & VM Sandbox Optimization Guide

This guide details configuration settings, setup steps, and troubleshooting tips to run the coding agent using **Ollama** natively on Windows CPU without lags, hangs, or timeout crashes.

---

## 🛠️ Step 1: Install & Set Up Ollama

Ollama runs natively on Windows and automatically compiles models using CPU execution (llama.cpp) if no GPU is found.

1. Download the Windows installer: [Ollama-Installer.exe](https://ollama.com/download/Ollama-Installer.exe).
2. Double-click the installer and run through the setup wizard.
3. Once completed, start the Ollama desktop application or run `ollama serve` in a terminal window.

---

## 📦 Step 2: Pull the CPU-Optimized Model

To ensure fast local code generation on CPU without freezing your machine:
* **Avoid large 7B/13B models** on low-VRAM CPU hosts, as they cause massive disk swapping and CPU lag.
* **Use the 1.5B variant**: `qwen2.5-coder:1.5b` is highly capable, extremely fast, and has a tiny footprint.

In a terminal, run:
```powershell
ollama pull qwen2.5-coder:1.5b
```

---

## ⚙️ Step 3: Optimize Context Window & Threads (Anti-Lag Tuning)

By default, large context windows take a long time to evaluate on CPU, which triggers HTTP client timeout errors. We can lock the context size and thread count.

### 1. Create a custom Modelfile
Create a file named `Modelfile` in the root workspace directory with the following contents:
```dockerfile
# Modelfile
FROM qwen2.5-coder:1.5b

# Restrict context window and output token sizes to keep CPU prefill fast
PARAMETER num_ctx 2048
PARAMETER num_predict 1024
PARAMETER temperature 0.1
```

### 2. Build the optimized model
In a terminal, compile this custom model:
```powershell
ollama create cpu-integrity-coder -f ./Modelfile
```

### 3. Update your `.env` configuration
In your `.env` file, change `OLLAMA_MODEL` to target your newly built optimized model:
```ini
OLLAMA_MODEL=cpu-integrity-coder
```

### 4. CPU Thread Allocation Override (Optional)
If Ollama consumes 100% CPU and makes the Windows UI unresponsive:
1. Open Windows System Environment Variables.
2. Add a new User variable named **`OLLAMA_NUM_PARALLEL`** and set it to `1` (disables parallel request allocation to conserve threads).
3. (Optional) Set the environment variable **`OMP_NUM_THREADS`** to match only the physical cores of your CPU (e.g., `4` or `6`), leaving background threads free for Windows tasks.

---

## 🛡️ Step 4: Execution inside the VM Sandbox

The agent framework implements strict virtualenv sandbox isolation for maximum safety:
1. **Isolated Execution**: When running Python generation tasks, the code and test files are copied to `vm_sandbox/work/sandbox/` and executed using `vm_sandbox/venv` (which contains `pytest`, `ruff`, `mypy`).
2. **Workspace Protection**: The parent workspace directory remains completely read-only for running code, preventing generated modules from corrupting, overwriting, or deleting project files.
3. **Hang Prevention**: Subprocesses have strict timeouts. If a subprocess times out on Windows, a recursive `taskkill` immediately cleans up the process tree, keeping your device responsive.

---

## 🔧 Step 5: Troubleshooting

### A. HTTP Timeout Errors
* The agent has an increased HTTP client timeout of **`180.0` seconds** in `llm.py`. 
* If you still see timeout errors, verify that `num_ctx` is set to `2048` or lower, or run `ollama serve` in a foreground command window to monitor processing speeds.

### B. Verification
To verify that the Ollama server is active and find your local models, run:
```powershell
curl http://localhost:11434/api/tags
```

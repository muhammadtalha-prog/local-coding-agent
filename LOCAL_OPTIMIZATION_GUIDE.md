# 🚀 Local CPU & RAM Optimization Guide for MATLAB Code Agent

This guide details configuration settings and tuning strategies to run the MATLAB Code Agent smoothly on an **8GB RAM / 256GB SSD Windows CPU** environment using Ollama and native MATLAB.

---

## 🛠️ Step 1: Optimize MATLAB Startup Latency (Major Speedup)

By default, MATLAB takes 30–60 seconds to launch because it initializes the entire Java Virtual Machine (JVM) desktop environment. 

### 1. JVM Bypass (`MATLAB_NO_JVM=true`)
Since the agent works with numeric calculations, system simulations, and signal processing using base MATLAB functions, it **does not require the Java Virtual Machine**. 
* The system is configured by default to run MATLAB with the `-nojvm` flag.
* This drops execution startup latency from **30–60 seconds to less than 5 seconds**!

### 2. Configure in `.env`
You can control this behavior via your `.env` configuration file:
```ini
# Set to false if you are generating GUI-based MATLAB code (highly discouraged)
MATLAB_NO_JVM=true
```

---

## 📦 Step 2: CPU-Optimized Ollama Tuning

Running local models on 8GB RAM CPUs can freeze the system if the prompt evaluation threads consume 100% processing power.

### 1. Model Selection Recommendation
* **qwen2.5-coder:3b (Recommended default)**: Fits completely in ~2.2GB RAM. It is fast, highly intelligent, and generates extremely reliable MATLAB syntax.
* **qwen2.5-coder:1.5b (Fastest)**: Fits in ~1.1GB RAM. Use this if your system is lagging heavily or you want near-instant response times.

### 2. Configure Context Restraints (Anti-Timeout)
Create a file named `Modelfile` in the root of this project:
```dockerfile
FROM qwen2.5-coder:3b

# Restrict context windows to keep CPU prefill fast
PARAMETER num_ctx 2048
PARAMETER num_predict 1024
PARAMETER temperature 0.15
```
Then build the optimized model:
```powershell
ollama create cpu-matlab-coder -f ./Modelfile
```
Update your `.env` to target this model:
```ini
OLLAMA_MODEL=cpu-matlab-coder
```

### 3. CPU Thread Allocation
If Ollama makes your Windows UI lag or freeze:
1. Open Windows System Environment Variables.
2. Add a user variable named **`OLLAMA_NUM_PARALLEL`** and set it to `1` (prevents parallel requests from taking up CPU memory).
3. Set the environment variable **`OMP_NUM_THREADS`** to match the number of physical cores of your CPU (e.g., `4` or `6`), leaving background threads free for normal Windows operations.

---

## 🛡️ Step 3: Run the Agent

Always run the agent using the virtual environment to ensure the correct dependencies are used:

```powershell
# Direct activation alternative (bypasses script policies):
& "D:\Local coding agent\venv\Scripts\python.exe" main.py --task "Your MATLAB task description"
```

---

## 🔍 Step 4: Troubleshooting

### 1. MATLAB Timeout Errors
* If MATLAB startup is still timing out, increase the timeout limit in your `.env` file:
  ```ini
  MATLAB_EXEC_TIMEOUT_SEC=180.0
  ```
* Timeouts are automatically detected by the runner and retried directly without wasting LLM calls.

### 2. Missing Toolbox Warnings
* The agent is instructed to write manual implementations of common toolbox functions (like DSP/Control filters).
* If a missing toolbox function is called, the pipeline will identify it instantly, halt, and instruct you to rephrase the task to avoid it.

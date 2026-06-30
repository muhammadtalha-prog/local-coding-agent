@echo off
echo Starting local vLLM OpenAI-Compatible API Server...
echo Model: Qwen/Qwen2.5-Coder-7B-Instruct
echo Host: 127.0.0.1
echo Port: 8000
python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen2.5-Coder-7B-Instruct --host 127.0.0.1 --port 8000
pause

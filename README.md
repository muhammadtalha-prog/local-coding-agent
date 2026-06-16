# 🤖 Local Coding Agent

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![LangChain](https://img.shields.io/badge/LangChain-0.2+-green.svg)](https://langchain.com)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## 🚀 Overview

**Local Coding Agent** is an AI-powered autonomous developer assistant that generates, tests, and self-corrects Python and MATLAB code based on natural language descriptions. Built with LangChain and LangGraph, it runs entirely locally with Ollama or integrates with cloud providers like Groq and DeepSeek.

### ✨ Key Features

- 🤖 **AI-Powered Code Generation** - Natural language to Python/MATLAB code
- 🔄 **Self-Correction Loop** - Automatically fixes bugs from error logs
- ⚡ **Parallel Processing** - Run multiple tasks simultaneously
- 📁 **Human-Readable Output** - Clean folder names based on task descriptions
- 🧪 **MATLAB Integration** - Full support with mock executor for testing
- 🔌 **Multi-Provider Support** - Ollama, Groq, DeepSeek, Grok
- 📊 **Batch Processing** - JSON-based task queue

## 📋 Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/local-coding-agent.git
cd local-coding-agent

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: .\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.template .env
# Edit .env with your preferred provider
```

### Usage

```bash
# Python code generation
python main.py "Create a Python script that processes CSV files" --test-cmd "python processor.py" --language python

# MATLAB code generation
python main.py "Create a MATLAB script for FFT analysis" --test-cmd "matlab -batch fft_analysis" --language matlab

# Batch processing
python main.py --batch tasks.json --concurrency 3
```

## 📁 Project Structure

```text
local-coding-agent/
├── agent.py              # LangGraph agent implementation
├── config.py             # Configuration & API management
├── external_software.py  # MATLAB/Python execution engine
├── main.py               # CLI launcher
├── prompt.py             # System prompts for AI
├── service_agent.py      # Parallel task service
├── tools.py              # File & command execution tools
├── workspace/            # Generated project folders
├── .env.template         # Environment configuration template
├── requirements.txt      # Python dependencies
└── README.md             # This file
```

## 🔧 Requirements

- Python 3.10+
- Ollama (optional, for local inference)
- MATLAB (optional, for MATLAB code execution)
- Git

## 📄 License

MIT License - see [LICENSE](file:///e:/Local%20coding%20agent/LICENSE) file for details

## 🤝 Contributing

Contributions are welcome! Please read CONTRIBUTING.md for details.

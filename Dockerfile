FROM python:3.10-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install safety tools and requirements
RUN pip install --no-cache-dir \
    ruff \
    mypy \
    pytest \
    pydantic \
    requests

# Create app directory
WORKDIR /app

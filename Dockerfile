# ==========================================
# Dockerfile cho Mechanical RAG Chatbot
# ==========================================
FROM python:3.11-slim

WORKDIR /app

# Cai dat system dependencies (neu can thiet)
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt ./

# Cai dat Python packages
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/

# Set PYTHONPATH de Python nhan dien module src/
ENV PYTHONPATH=/app/src

# Port mac dinh (co the bi override boi docker-compose)
EXPOSE 8100

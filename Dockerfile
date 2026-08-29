FROM python:3.11-slim

WORKDIR /app

# Install ALL build dependencies pyquotex needs
RUN apt-get update && apt-get install -y \
    git \
    gcc \
    build-essential \
    python3-dev \
    libffi-dev \
    libssl-dev \
    libxml2-dev \
    libxslt1-dev \
    curl \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Install Rust (needed by some JSON/websocket libs)
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# Copy and install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD uvicorn api:app --host 0.0.0.0 --port ${PORT:-10000}


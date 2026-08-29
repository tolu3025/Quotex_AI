FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y git gcc && rm -rf /var/lib/apt/lists/*

# Install pyquotex from GitHub first (needs git)
RUN pip install --no-cache-dir git+https://github.com/cleitonleonel/pyquotex.git

# Then install everything else
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD uvicorn api:app --host 0.0.0.0 --port ${PORT:-10000}


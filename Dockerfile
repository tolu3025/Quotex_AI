FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y git gcc build-essential && rm -rf /var/lib/apt/lists/*

# Step 1: Install pinned deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Step 2: Install pyquotex without its conflicting deps
RUN pip install --no-cache-dir --no-deps git+https://github.com/cleitonleonel/pyquotex.git

# Step 3: Install pyquotex runtime deps we didn't already cover
RUN pip install --no-cache-dir aiohttp websockets requests beautifulsoup4 lxml Pillow

COPY . .

CMD uvicorn api:app --host 0.0.0.0 --port ${PORT:-10000}


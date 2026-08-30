FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y git gcc build-essential && rm -rf /var/lib/apt/lists/*

# Step 1: Install pinned deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Step 2: Install pyquotex without its conflicting dependency tree
RUN pip install --no-cache-dir --no-deps git+https://github.com/cleitonleonel/pyquotex.git

# Step 3: Manually install ALL pyquotex runtime deps
RUN pip install --no-cache-dir \
    aiohttp \
    websockets \
    requests \
    beautifulsoup4 \
    lxml \
    Pillow \
    fake-useragent \
    python-dateutil \
    pycryptodome

COPY . .

CMD uvicorn api:app --host 0.0.0.0 --port ${PORT:-10000}

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y git gcc build-essential && rm -rf /var/lib/apt/lists/*

# Step 1: Install all our pinned deps first
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Step 2: Install pyquotex from GitHub WITHOUT its dependencies
# (we already installed compatible versions above)
RUN pip install --no-cache-dir --no-deps git+https://github.com/cleitonleonel/pyquotex.git

# Step 3: Manually install pyquotex's actual runtime deps that we didn't already cover
RUN pip install --no-cache-dir aiohttp websockets requests beautifulsoup4 lxml Pillow

COPY . .

CMD uvicorn api:app --host 0.0.0.0 --port ${PORT:-10000}

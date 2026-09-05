FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv

COPY requirements.txt requirements-dev.txt ./
RUN uv pip install --system --no-cache -r requirements.txt -r requirements-dev.txt

COPY . .

# F2/bbc788da: seed the turn-gate blocker state dir owned by the runtime uid so the
# root-owned named volume (turngate-data) is initialised writable by the container
# (which runs as 1000:1000) yet stays outside any agent-writable path.
RUN mkdir -p /data/turngate && chown -R 1000:1000 /data/turngate

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]

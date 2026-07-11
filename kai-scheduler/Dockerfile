FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates &&     curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-26.1.4.tgz |     tar xz --strip-components=1 -C /usr/local/bin docker/docker &&     rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv

COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

COPY worker_auth.py .
COPY scheduler.py .
COPY watchdog.py .
COPY security_watchdog.py .
COPY execution_registry.py .
COPY triage.py .
COPY invariants.py .
COPY test_watchdog_dedup.py .

CMD ["python", "scheduler.py"]

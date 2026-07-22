FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl openssh-client patch git && rm -rf /var/lib/apt/lists/*

# uid-1000 runtime user (compose runs user "1000:1000"): ssh/ssh-keygen need a
# passwd entry + home dir, else openssh fails "No user exists for uid 1000".
RUN useradd -m -u 1000 -s /bin/bash orchestrator
ENV HOME=/home/orchestrator

# git safe.directory for self-modify mounts (root-in-container vs leo-on-host)
RUN git config --system --add safe.directory /kai-system && git config --system --add safe.directory /workspace

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt

COPY . .

EXPOSE 8003

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]

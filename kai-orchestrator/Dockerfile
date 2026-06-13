FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl openssh-client patch git && rm -rf /var/lib/apt/lists/*

# git safe.directory for self-modify mounts (root-in-container vs leo-on-host)
RUN git config --system --add safe.directory /kai-system && git config --system --add safe.directory /workspace

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8003

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]

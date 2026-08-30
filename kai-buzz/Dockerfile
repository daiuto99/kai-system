FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv

COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

COPY agents_bridge.py buzz_approve.py buzz_provision.py ember_bridge.py sky_dm.py kai_dm.py roads_dm.py coach_dm.py relay_roundtrip_client.py advisor_witness.py ./
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["./entrypoint.sh"]

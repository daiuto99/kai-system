FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates nmap &&     curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-26.1.4.tgz |     tar xz --strip-components=1 -C /usr/local/bin docker/docker &&     rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv

COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

COPY worker_auth.py .
COPY redact.py .
COPY test_token_redaction.py .
COPY scheduler.py .
COPY contract_tests.py .
COPY telegram_inbound_probe.py .
COPY watchdog.py .
COPY security_watchdog.py .
COPY execution_registry.py .
COPY triage.py .
COPY tg_alert.py .
COPY invariants.py .
# Watchdog-subsystem test suite baked into the image so ci.sh can exercise it in-place
# (KAI-48). The container runs non-root, so pytest must be installed at build time
# (added to requirements.txt). Only HERMETIC tests whose deps are already in the image
# are baked. Excluded: test_fleet_maint (needs shared/fleet_eval → host CI test_fleet_eval),
# test_invariant_engine (makes live Plane API calls → not hermetic, tracked separately).
COPY test_watchdog_dedup.py .
COPY test_fleet_watchdog.py .
COPY test_kai808_telegram_allowlist.py .

CMD ["python", "scheduler.py"]

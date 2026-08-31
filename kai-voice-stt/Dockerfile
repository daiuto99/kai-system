FROM python:3.11-slim

# ffmpeg: faster-whisper decodes audio through it (wav/mp3/m4a/ogg/flac).
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Model weights cache on a persistent volume so the ~75MB base model downloads once,
# not on every container recreate. Set before the model ever loads.
ENV HF_HOME=/models \
    XDG_CACHE_HOME=/models

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8005
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8005"]

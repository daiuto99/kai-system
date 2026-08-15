FROM python:3.12-slim
WORKDIR /app
COPY kai_openai_shim.py .
EXPOSE 4001
# stdlib-only shim; no pip deps. -u for unbuffered logs.
CMD ["python3", "-u", "kai_openai_shim.py"]

# Container image for the remote/cloud variant only. The local variant
# (server.py) is meant to run directly on your machine via Claude
# Desktop's config, not in a container.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run/App Runner/Container Apps all set PORT for you at runtime;
# 8080 is just the local-testing default.
ENV PORT=8080
EXPOSE 8080

CMD ["python3", "server_remote.py"]

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/codex-integration-smoke-monitor
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git nodejs npm \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g opencode-ai

COPY apps/sdk/python/requirements.txt ./sdk/python/
COPY apps/codex-integration-smoke-monitor/requirements.txt ./codex-integration-smoke-monitor/
RUN pip install --no-cache-dir \
    -r sdk/python/requirements.txt \
    -r codex-integration-smoke-monitor/requirements.txt

COPY apps/codex-integration-smoke-monitor/ codex-integration-smoke-monitor/
COPY apps/sdk/python/ codex-integration-smoke-monitor/sdk/python/
COPY apps/sdk/cityos.proto codex-integration-smoke-monitor/sdk/

RUN mkdir -p codex-integration-smoke-monitor/sdk/python/_autogen/grpc
RUN python -m grpc_tools.protoc \
    -I codex-integration-smoke-monitor/sdk/ \
    --python_out=codex-integration-smoke-monitor/sdk/python/_autogen/grpc \
    --grpc_python_out=codex-integration-smoke-monitor/sdk/python/_autogen/grpc \
    cityos.proto

CMD ["python3", "codex-integration-smoke-monitor/codex_integration_smoke_monitor.py"]

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/tellme-smoke-monitor
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git nodejs npm \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g opencode-ai

COPY apps/sdk/python/requirements.txt ./sdk/python/
COPY apps/tellme-smoke-monitor/requirements.txt ./tellme-smoke-monitor/
RUN pip install --no-cache-dir \
    -r sdk/python/requirements.txt \
    -r tellme-smoke-monitor/requirements.txt

COPY apps/tellme-smoke-monitor/ tellme-smoke-monitor/
COPY apps/sdk/python/ tellme-smoke-monitor/sdk/python/
COPY apps/sdk/cityos.proto tellme-smoke-monitor/sdk/

RUN mkdir -p tellme-smoke-monitor/sdk/python/_autogen/grpc
RUN python -m grpc_tools.protoc \
    -I tellme-smoke-monitor/sdk/ \
    --python_out=tellme-smoke-monitor/sdk/python/_autogen/grpc \
    --grpc_python_out=tellme-smoke-monitor/sdk/python/_autogen/grpc \
    cityos.proto

CMD ["python3", "tellme-smoke-monitor/tellme_smoke_monitor.py"]

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/tellme-smoke-developer-a
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git nodejs npm \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g opencode-ai

COPY apps/sdk/python/requirements.txt ./sdk/python/
COPY apps/tellme-smoke-developer-a/requirements.txt ./tellme-smoke-developer-a/
RUN pip install --no-cache-dir \
    -r sdk/python/requirements.txt \
    -r tellme-smoke-developer-a/requirements.txt

COPY apps/tellme-smoke-developer-a/ tellme-smoke-developer-a/
COPY apps/sdk/python/ tellme-smoke-developer-a/sdk/python/
COPY apps/sdk/cityos.proto tellme-smoke-developer-a/sdk/

RUN mkdir -p tellme-smoke-developer-a/sdk/python/_autogen/grpc
RUN python -m grpc_tools.protoc \
    -I tellme-smoke-developer-a/sdk/ \
    --python_out=tellme-smoke-developer-a/sdk/python/_autogen/grpc \
    --grpc_python_out=tellme-smoke-developer-a/sdk/python/_autogen/grpc \
    cityos.proto

CMD ["python3", "tellme-smoke-developer-a/tellme_smoke_developer_a.py"]

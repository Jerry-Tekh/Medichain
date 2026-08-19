FROM node:24-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}" \
    HOME=/home/medichain
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global genlayer@0.39.2 \
    && npm cache clean --force \
    && useradd --create-home --home-dir /home/medichain --shell /usr/sbin/nologin medichain

COPY medichain/requirements-production.txt ./requirements.txt
RUN python3 -m venv /app/.venv \
    && pip install --no-cache-dir -r requirements.txt

COPY medichain ./medichain
RUN chown -R medichain:medichain /app /home/medichain
WORKDIR /app/medichain/backend

ENV MEDICHAIN_ENV=production
ENV MEDICHAIN_BACKEND_MODE=genlayer
ENV GENLAYER_CLI_COMMAND=genlayer

USER medichain
EXPOSE 8000
CMD ["python", "start.py"]

FROM docker.io/library/node:26-slim@sha256:5adad64f9a1fca2d51972caa65983634c8a774eb300d72b20f7f2a0f60368072 AS frontend-build

WORKDIR /frontend

# pnpm-workspace.yaml carries the supply-chain settings (trustLockfile,
# minimumReleaseAgeExclude); without it pnpm 11+ applies its default
# minimumReleaseAge here and rejects lockfile entries from the last day.
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./

# Node 26 no longer bundles Corepack, so install the pnpm release pinned in
# package.json's `packageManager` field directly.
RUN npm install --global "pnpm@$(node -p "require('./package.json').packageManager.split('@')[1].split('+')[0]")"

# --ignore-scripts: this stage only needs the packages on disk to build, and
# has no git binary for any package's install script to call.
RUN pnpm install --frozen-lockfile --ignore-scripts

COPY frontend/ ./
RUN pnpm build

FROM docker.io/library/python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY --from=frontend-build /frontend/dist/ ./static/

RUN mkdir -p /app/audio /app/data && \
    useradd -r -u 1001 appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]

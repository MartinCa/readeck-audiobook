FROM docker.io/library/node:24-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205 AS frontend-build

WORKDIR /frontend

RUN corepack enable

# pnpm-workspace.yaml carries the supply-chain settings (trustLockfile,
# minimumReleaseAgeExclude); without it pnpm 11+ applies its default
# minimumReleaseAge here and rejects lockfile entries from the last day.
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
# --ignore-scripts: this stage only needs the packages on disk to build, and
# has no git binary for any package's install script to call.
RUN pnpm install --frozen-lockfile --ignore-scripts

COPY frontend/ ./
RUN pnpm build

FROM docker.io/library/python:3.15-slim@sha256:94d3426f1def7db4a106a232ac121bfd975393e5dfd40a619b955fae45fe7913

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

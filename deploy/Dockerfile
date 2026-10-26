# syntax=docker/dockerfile:1
# The orchestrator image (TRD §2.4): the factory code at the pinned tag, the docker CLI for
# the host socket, git for worktrees. Agents never run here; they run in task containers
# from the per-installation task image. Built by `docker compose` from the factory checkout.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/factory/.venv

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

# The docker CLI from the official image: no piped installer scripts.
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /opt/factory
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY orchestrator ./orchestrator
COPY cli ./cli
COPY prompts ./prompts
COPY migrations ./migrations
COPY deploy ./deploy
COPY fixtures/known_failing ./fixtures/known_failing
RUN uv sync --frozen --no-dev && chmod +x deploy/entrypoint.sh

ENV PATH=/opt/factory/.venv/bin:/usr/local/bin:/usr/bin:/bin
EXPOSE 8080
ENTRYPOINT ["tini", "--", "/opt/factory/deploy/entrypoint.sh"]

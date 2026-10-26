# syntax=docker/dockerfile:1
# The factory base task image (TRD §2.0 · Task container image). Built per installation by
# `factory image build` with the product repository as the build context and its install
# command baked in; the task's worktree is bind-mounted over /work at run time, so anything
# the install command needs to keep must live outside /work (uv: /opt/venv).
FROM python:3.12-slim-bookworm

ARG INSTALL_COMMAND=""
ARG CLAUDE_CODE_VERSION=latest
ARG CODEX_VERSION=latest

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    NPM_CONFIG_UPDATE_NOTIFIER=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates ripgrep tini \
    && rm -rf /var/lib/apt/lists/*

# Node and npm from the official image: no piped installer scripts.
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" "@openai/codex@${CODEX_VERSION}" \
    && npm cache clean --force

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN groupadd -g 10001 agent && useradd -m -u 10001 -g 10001 -s /bin/bash agent \
    && mkdir -p /opt/venv /work && chown -R 10001:10001 /opt/venv /work

WORKDIR /work
COPY --chown=10001:10001 . /work
USER 10001:10001
RUN if [ -n "$INSTALL_COMMAND" ]; then sh -c "$INSTALL_COMMAND"; fi

ENV HOME=/home/agent \
    PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin

ENTRYPOINT ["tini", "--"]
CMD ["sleep", "infinity"]

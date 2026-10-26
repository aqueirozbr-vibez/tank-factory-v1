#!/bin/sh
# The orchestrator's command line from /etc/factory/factory.env (compose passes it as env).
set -eu
: "${FACTORY_REPO_CLONE:?set FACTORY_REPO_CLONE in /etc/factory/factory.env}"
: "${FACTORY_ORG:?set FACTORY_ORG}"
: "${FACTORY_REPO:?set FACTORY_REPO}"
set -- serve \
  --repo-clone "$FACTORY_REPO_CLONE" \
  --org "$FACTORY_ORG" \
  --repo "$FACTORY_REPO" \
  --harness "${FACTORY_HARNESS:-roster}" \
  --bind 0.0.0.0 \
  --port 8080
if [ -n "${FACTORY_SCRIPTS_DIR:-}" ]; then
  set -- "$@" --scripts-dir "$FACTORY_SCRIPTS_DIR"
fi
exec factory "$@"

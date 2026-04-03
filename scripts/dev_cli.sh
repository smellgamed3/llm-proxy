#!/usr/bin/env bash

set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.dev.yml}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

require_cmd docker

run_compose() {
  (cd "$PROJECT_DIR" && docker compose -f "$COMPOSE_FILE" "$@")
}

usage() {
  cat <<'EOF'
Usage:
  bash scripts/dev_cli.sh <command> [args]

Commands:
  up                     Start dev services in background
  down                   Stop and remove dev services
  restart [svc...]       Restart all or specific services
  rebuild [svc...]       Rebuild images and recreate services
  update [svc...]        Pull base layers + rebuild + recreate services
  logs [svc...]          Show recent logs (tail=120)
  logsf [svc...]         Follow logs
  ps                     Show service status
  rerun                  Trigger analyzer full rerun via API
  help                   Show this help

Env:
  COMPOSE_FILE           Compose file path (default: docker-compose.dev.yml)
EOF
}

cmd="${1:-help}"
shift || true

case "$cmd" in
  up)
    run_compose up -d "$@"
    run_compose ps
    ;;
  down)
    run_compose down "$@"
    ;;
  restart)
    if [[ "$#" -gt 0 ]]; then
      run_compose restart "$@"
    else
      run_compose restart
    fi
    run_compose ps
    ;;
  rebuild)
    if [[ "$#" -gt 0 ]]; then
      run_compose up -d --build "$@"
    else
      run_compose up -d --build
    fi
    run_compose ps
    ;;
  update)
    if [[ "$#" -gt 0 ]]; then
      run_compose build --pull "$@"
      run_compose up -d "$@"
    else
      run_compose build --pull
      run_compose up -d
    fi
    run_compose ps
    ;;
  logs)
    run_compose logs --tail=120 "$@"
    ;;
  logsf)
    run_compose logs --tail=120 -f "$@"
    ;;
  ps)
    run_compose ps
    ;;
  rerun)
    curl -sf -X POST http://localhost:${API_PORT:-9091}/api/admin/analyzer/rerun \
      -H 'content-type: application/json' \
      -d '{"mode":"full"}'
    echo
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "Unknown command: $cmd" >&2
    usage
    exit 1
    ;;
esac

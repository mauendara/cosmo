#!/usr/bin/env bash
# Phase 0 exit criterion: lint, format, types, and tests in one command.
set -euo pipefail
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q
echo "all checks passed"

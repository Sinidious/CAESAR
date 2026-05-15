# CAESAR developer task runner.
# Install just: https://github.com/casey/just
# Usage: `just <recipe>`. Run `just` (no args) to list recipes.

set shell := ["bash", "-cu"]
set dotenv-load := true

default:
    @just --list --unsorted

# One-time setup: virtualenv, dev deps, pre-commit hooks.
setup:
    python -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -e ".[dev,docs]"
    .venv/bin/pre-commit install

# Format Python with ruff (writes).
fmt:
    .venv/bin/ruff format .
    .venv/bin/ruff check --fix .

# Lint without writing.
lint:
    .venv/bin/ruff check .
    .venv/bin/ruff format --check .

# Static types.
typecheck:
    .venv/bin/mypy

# Tests.
test *ARGS:
    .venv/bin/pytest {{ ARGS }}

# Full pre-push gate: lint + typecheck + test.
check: lint typecheck test

# Build the docs site locally.
docs-serve:
    .venv/bin/mkdocs serve

docs-build:
    .venv/bin/mkdocs build --strict

# Create a new ADR from the template.
adr-new title:
    #!/usr/bin/env bash
    set -euo pipefail
    dir="docs/adr"
    next=$(printf "%04d" $(( $(ls "$dir" | grep -E '^[0-9]{4}-' | sort | tail -1 | cut -c1-4 | sed 's/^0*//') + 1 )))
    slug=$(echo "{{ title }}" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-//;s/-$//')
    path="$dir/${next}-${slug}.md"
    cp "$dir/0000-template.md" "$path"
    sed -i "s/^# 0000 — .*/# ${next} — {{ title }}/" "$path"
    echo "Created $path"
    "${EDITOR:-nano}" "$path"

# Clean caches and build artifacts.
clean:
    rm -rf .venv .ruff_cache .mypy_cache .pytest_cache htmlcov dist build site
    find . -type d -name __pycache__ -exec rm -rf {} +

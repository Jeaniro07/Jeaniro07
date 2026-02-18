#!/bin/bash
set -euo pipefail

# Only run in remote (Claude Code on the web) environments
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

# Install Node.js dependencies if present
if [ -f "package.json" ]; then
  echo "Installing Node.js dependencies..."
  npm install
fi

# Install Python dependencies if present
if [ -f "requirements.txt" ]; then
  echo "Installing Python dependencies (requirements.txt)..."
  pip install -r requirements.txt --quiet
fi

if [ -f "pyproject.toml" ]; then
  echo "Installing Python dependencies (pyproject.toml)..."
  pip install -e . --quiet
fi

# Install Ruby dependencies if present
if [ -f "Gemfile" ]; then
  echo "Installing Ruby dependencies..."
  bundle install
fi

# Install Rust dependencies if present
if [ -f "Cargo.toml" ]; then
  echo "Building Rust dependencies..."
  cargo fetch
fi

# Install Go dependencies if present
if [ -f "go.mod" ]; then
  echo "Downloading Go dependencies..."
  go mod download
fi

echo "Session start hook complete."

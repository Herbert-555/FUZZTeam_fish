#!/usr/bin/env bash
# Install the repository pre-push hook locally (does not touch git config).
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
src="$repo_root/.githooks/pre-push"
dst="$repo_root/.git/hooks/pre-push"

cp "$src" "$dst"
chmod +x "$dst"
echo "[+] pre-push hook installed: $dst"

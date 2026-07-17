#!/usr/bin/env bash
# HARDEN-1: install the tracked pre-push gate into this checkout's .git/hooks.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
hook_dir="$(git -C "$repo_root" rev-parse --git-path hooks)"
mkdir -p "$hook_dir"
cp "$repo_root/scripts/hooks/pre-push" "$hook_dir/pre-push"
chmod 755 "$hook_dir/pre-push"
cmp -s "$repo_root/scripts/hooks/pre-push" "$hook_dir/pre-push"
test -x "$hook_dir/pre-push"
echo "Installed HARDEN-1 pre-push gate at $hook_dir/pre-push"

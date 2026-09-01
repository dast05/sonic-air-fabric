#!/usr/bin/env bash
# GitHub CLI securely prompts for the NGC secret; do not paste it into chat.
set -euo pipefail
cd "$(dirname "$0")/.."
repo="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
gh api --method PUT "repos/$repo/environments/air-lab" >/dev/null
gh secret set NGC_API_KEY --repo "$repo" --env air-lab
gh workflow run air-lab.yml --repo "$repo" -f operation=discover
echo 'Open Actions / NVIDIA Air lab, read images.json and set the three image UUID variables.'
echo 'Keep AIR_AUTO_DEPLOY unset until the first manual deploy succeeds.'

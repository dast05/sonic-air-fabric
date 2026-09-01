#!/usr/bin/env bash
# Run from the extracted repository. Creates a PRIVATE repo in your own account.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v gh >/dev/null || { echo 'Install GitHub CLI, then run gh auth login.'; exit 1; }
gh auth status
owner="$(gh api user --jq .login)"
repo="$owner/sonic-air-fabric"
if [[ "$owner" != dast05 ]]; then
  echo "Expected GitHub account dast05; current account is $owner. Switch account first."
  exit 1
fi
python3 -m fabriclab validate
python3 -m pytest -q
if [[ ! -d .git ]]; then git init -b main; fi
if git remote get-url origin >/dev/null 2>&1; then
  echo 'Origin already exists; inspect it and push explicitly. No remote was changed.'
  exit 1
fi
git add .
git commit -m 'Add SONiC Air fabric, inter-VNI router, TRex and CI/CD'
gh repo create "$repo" --private --source=. --remote=origin --push
gh api --method PUT "repos/$repo/environments/air-lab" >/dev/null
echo "Created private repo: $repo"
echo 'Next: configure NGC_API_KEY and image UUIDs as described in README.md.'

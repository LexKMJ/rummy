#!/usr/bin/env bash
# Requiere GitHub CLI (https://cli.github.com) y haber hecho: gh auth login
set -e
git init -q
git add .
git commit -qm "Rummi: primera versión"
git branch -M main
gh repo create rummi --public --source=. --remote=origin --push

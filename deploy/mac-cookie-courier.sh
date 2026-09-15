#!/usr/bin/env bash
# Obtenir un cookie depuis cette machine et le déposer sur le serveur.
#
# À utiliser quand l'adresse du serveur est refusée par le site alors que la
# connexion de cette machine passe : l'ordinateur ne sert qu'à ouvrir une
# session toutes les quelques heures, le serveur fait tout le reste.
#
#   bash deploy/mac-cookie-courier.sh ubuntu@91.134.243.26
#
# Installation en tâche de fond (macOS) : voir deploy/com.stockwatch.courier.plist
set -euo pipefail
cd "$(dirname "$0")/.."

TARGET="${1:-${STOCKWATCH_SSH_TARGET:-}}"
REMOTE_FILE="${STOCKWATCH_REMOTE_COOKIE:-/opt/stockwatch/cookie.txt}"
PY="${STOCKWATCH_PYTHON:-.venv/bin/python}"

[ -n "$TARGET" ] || { echo "Usage : bash deploy/mac-cookie-courier.sh utilisateur@serveur" >&2; exit 2; }
[ -x "$PY" ] || PY="python3"

TMP="$(mktemp -t stockwatch-cookie)"
trap 'rm -f "$TMP"' EXIT

# --print écrit le cookie sur la sortie standard sans toucher au .env local.
if ! "$PY" -m stockwatch cookie --auto --print > "$TMP"; then
  echo "✗ Aucun cookie obtenu depuis cette machine." >&2
  exit 1
fi
[ -s "$TMP" ] || { echo "✗ Cookie vide." >&2; exit 1; }

scp -q "$TMP" "$TARGET:/tmp/stockwatch-cookie.txt"
ssh "$TARGET" "sudo install -o stockwatch -g stockwatch -m 600 /tmp/stockwatch-cookie.txt '$REMOTE_FILE' && rm -f /tmp/stockwatch-cookie.txt"

echo "✓ Cookie déposé dans $REMOTE_FILE — le bot le prendra en compte dans la minute."

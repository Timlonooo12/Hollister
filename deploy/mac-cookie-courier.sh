#!/usr/bin/env bash
# Obtenir un cookie depuis cette machine et le déposer sur le serveur.
#
# À utiliser quand le site refuse toute nouvelle session venant de l'adresse du
# serveur alors que la connexion de cette machine passe : l'ordinateur ne sert
# qu'à ouvrir une session toutes les quelques heures, le serveur fait le reste.
#
#   bash deploy/mac-cookie-courier.sh ubuntu@91.134.243.26
#
# Préparation du serveur, une seule fois (voir README) :
#   sudo install -d -o ubuntu -g stockwatch -m 2770 /opt/stockwatch/incoming
#   echo STOCKWATCH_COOKIE_FILE=/opt/stockwatch/incoming/cookie.txt | sudo tee -a /opt/stockwatch/.env
#   sudo systemctl restart stockwatch
#
# Le dépôt se fait ensuite par simple copie : aucun sudo à distance, donc
# utilisable sans surveillance depuis une tâche planifiée.
set -euo pipefail
cd "$(dirname "$0")/.."

TARGET="${1:-${STOCKWATCH_SSH_TARGET:-}}"
REMOTE_FILE="${STOCKWATCH_REMOTE_COOKIE:-/opt/stockwatch/incoming/cookie.txt}"
PY="${STOCKWATCH_PYTHON:-.venv/bin/python}"

[ -n "$TARGET" ] || { echo "Usage : bash deploy/mac-cookie-courier.sh utilisateur@serveur" >&2; exit 2; }
[ -x "$PY" ] || PY="python3"

TMP="$(mktemp -t stockwatch-cookie)"
trap 'rm -f "$TMP"' EXIT

if ! "$PY" -m stockwatch cookie --auto --print > "$TMP"; then
  echo "✗ Aucun cookie obtenu depuis cette machine." >&2
  exit 1
fi
[ -s "$TMP" ] || { echo "✗ Cookie vide." >&2; exit 1; }

# Lisible par le groupe : le dossier distant est en setgid, le service le lira.
chmod 640 "$TMP"
scp -q -p "$TMP" "$TARGET:$REMOTE_FILE"

echo "✓ Cookie déposé dans $REMOTE_FILE — le bot le prendra en compte dans la minute."

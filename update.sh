#!/usr/bin/env bash
# Met à jour le code depuis GitHub sans toucher à .env ni à la mémoire du stock.
#
#   bash update.sh
#   GITHUB_TOKEN=ghp_xxx bash update.sh      # dépôt privé
set -euo pipefail
cd "$(dirname "$0")"

REPO="${STOCKWATCH_REPO:-Timlonooo12/Hollister}"
BRANCH="${STOCKWATCH_BRANCH:-main}"

say()  { printf '\033[1;36m%s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# Refuse to overwrite work in progress: this script replaces the code files
# wholesale, so uncommitted changes would be lost without warning.
if [ -d .git ] && command -v git >/dev/null 2>&1 && [ -n "$(git status --porcelain 2>/dev/null)" ]; then
  die "Modifications locales non commitées — elles seraient écrasées. Commite-les, ou utilise git pull."
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

say "Téléchargement de la dernière version ($REPO, branche $BRANCH)…"
CURL_ARGS=(-fsSL -o "$TMP/repo.zip")
if [ -n "${GITHUB_TOKEN:-}" ]; then
  CURL_ARGS+=(-H "Authorization: Bearer $GITHUB_TOKEN")
fi
if ! curl "${CURL_ARGS[@]}" "https://codeload.github.com/$REPO/zip/refs/heads/$BRANCH"; then
  echo >&2
  echo "Téléchargement impossible. Si le dépôt est privé, au choix :" >&2
  echo "  • rends-le public (GitHub → Settings → General → Change visibility) ;" >&2
  echo "  • ou crée un token (github.com/settings/tokens, portée « repo ») puis :" >&2
  echo "      GITHUB_TOKEN=ton_token bash update.sh" >&2
  echo "  • ou télécharge le ZIP depuis le navigateur (bouton vert Code → Download ZIP)." >&2
  exit 1
fi

unzip -oq "$TMP/repo.zip" -d "$TMP"
SOURCE="$(find "$TMP" -maxdepth 1 -mindepth 1 -type d | head -1)"
[ -n "$SOURCE" ] || die "Archive vide ou illisible."
[ -f "$SOURCE/stockwatch/__main__.py" ] || die "Archive inattendue (pas de dossier stockwatch/)."

# .env et stockwatch-state.json ne sont pas dans l'archive : ils restent en place.
cp -R "$SOURCE"/. .
ok "Code mis à jour."

PY="python3"
[ -x .venv/bin/python ] && PY=".venv/bin/python"
"$PY" -m pip install --quiet -r requirements.txt || die "Mise à jour des dépendances échouée."
ok "Dépendances à jour."

echo
say "Relance le bot :  $PY -m stockwatch     (ou : bash setup.sh)"

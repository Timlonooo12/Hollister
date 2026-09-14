#!/usr/bin/env bash
# Installation interactive : dépendances, token, .env, vérification, démarrage.
#
#   bash setup.sh                      # demande le token puis lance le bot
#   bash setup.sh --token 123:AA...    # sans question
#   bash setup.sh --no-start           # configure sans démarrer
set -euo pipefail

cd "$(dirname "$0")"

TOKEN=""
START=1
while [ $# -gt 0 ]; do
  case "$1" in
    --token) TOKEN="${2:-}"; shift 2 ;;
    --token=*) TOKEN="${1#*=}"; shift ;;
    --no-start) START=0; shift ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "Option inconnue : $1" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1;36m%s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# --- 1. Python -------------------------------------------------------------
PY=""
FOUND=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  VERSION="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
  [ -n "$VERSION" ] || continue          # macOS sans outils Xcode : le binaire existe mais n'exécute rien
  [ -n "$FOUND" ] || FOUND="$VERSION"
  if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$candidate"; break
  fi
done

if [ -z "$PY" ]; then
  echo
  if [ -n "$FOUND" ]; then
    printf '\033[1;31m✗ Python %s est trop ancien — il en faut 3.11 ou plus récent.\033[0m\n' "$FOUND" >&2
  else
    printf '\033[1;31m✗ Python 3.11+ est introuvable.\033[0m\n' >&2
  fi
  case "$(uname -s)" in
    Darwin)
      echo "  macOS : le Python livré par Apple est en 3.9, trop ancien." >&2
      echo "  1. Ouvre https://www.python.org/downloads/macos/" >&2
      echo "  2. Télécharge le .pkg « macOS 64-bit universal2 installer », double-clic, Suivant…" >&2
      echo "  3. Relance :  bash setup.sh" >&2
      ;;
    Linux)
      echo "  Debian / Ubuntu :  sudo apt update && sudo apt install -y python3 python3-venv python3-pip" >&2
      echo "  Fedora          :  sudo dnf install -y python3 python3-pip" >&2
      echo "  Puis relance    :  bash setup.sh" >&2
      ;;
    *)
      echo "  Installe Python 3.11+ depuis https://www.python.org/downloads/ puis relance : bash setup.sh" >&2
      ;;
  esac
  exit 1
fi
ok "Python trouvé : $($PY --version 2>&1)"

# --- 2. Environnement virtuel ---------------------------------------------
say "Installation des dépendances…"
if [ ! -d .venv ] && ! "$PY" -m venv .venv 2>/dev/null; then
  warn "Impossible de créer l'environnement virtuel (paquet python3-venv manquant ?) — installation directe."
fi
if [ -x .venv/bin/python ]; then
  PY=".venv/bin/python"
elif [ -x .venv/Scripts/python.exe ]; then
  PY=".venv/Scripts/python.exe"
fi
"$PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
"$PY" -m pip install --quiet -r requirements.txt || die "Installation des dépendances échouée."
ok "Dépendances installées."

# --- 3. Token --------------------------------------------------------------
[ -f .env ] || cp .env.example .env

if [ -z "$TOKEN" ]; then
  CURRENT="$("$PY" - <<'PY'
import pathlib, re


def token_of(path: str) -> str:
    file = pathlib.Path(path)
    if not file.exists():
        return ""
    match = re.search(r"^STOCKWATCH_BOT_TOKEN=(.*)$", file.read_text("utf-8"), re.M)
    return match.group(1).strip() if match else ""


current = token_of(".env")
# The example file ships a fake token: compare against it exactly, since real
# tokens look just like it.
print("" if current == token_of(".env.example") else current)
PY
)"
  if [ -n "$CURRENT" ]; then
    ok "Token déjà configuré (…${CURRENT: -6})."
  else
    echo
    say "Token Telegram"
    echo "  1. Ouvre https://t.me/BotFather  →  /newbot  →  choisis un nom"
    echo "  2. BotFather te répond un token du style 123456789:AAE-xxxxxxxxxxxxxxxxxxxx"
    echo
    printf 'Colle ton token ici puis Entrée : '
    read -r TOKEN
    [ -n "$TOKEN" ] || die "Aucun token saisi. Relance : bash setup.sh"
  fi
fi

if [ -n "$TOKEN" ]; then
  TOKEN="$(printf '%s' "$TOKEN" | tr -d '[:space:]')"
  case "$TOKEN" in
    *:*) ;;
    *) die "Ce token ne ressemble pas à un token Telegram (il doit contenir « : »)." ;;
  esac
  TOKEN="$TOKEN" "$PY" - <<'PY'
import os, pathlib, re

token = os.environ["TOKEN"]
path = pathlib.Path(".env")
text = path.read_text("utf-8")
line = f"STOCKWATCH_BOT_TOKEN={token}"
text, count = re.subn(r"^STOCKWATCH_BOT_TOKEN=.*$", line, text, count=1, flags=re.M)
if not count:
    text = text.rstrip("\n") + "\n" + line + "\n"
path.write_text(text, "utf-8")
PY
  chmod 600 .env 2>/dev/null || true
  ok "Token enregistré dans .env (…${TOKEN: -6})."
fi

# --- 4. Vérification de la lecture du stock -------------------------------
echo
say "Vérification de la page surveillée…"
if "$PY" -m stockwatch diagnose; then
  ok "Le stock est lisible."
else
  echo
  warn "Le stock n'a pas pu être lu (voir le message ci-dessus)."
  warn "Le bot démarrera quand même et te préviendra si ça ne se débloque pas."
fi

# --- 5. Démarrage ----------------------------------------------------------
echo
if [ "$START" -eq 0 ]; then
  say "Prêt. Pour démarrer :  $PY -m stockwatch"
  exit 0
fi
say "Démarrage du bot — envoie /start à ton bot sur Telegram."
say "(Ctrl+C pour arrêter ; pour tourner en continu, voir le README §7)"
echo
exec "$PY" -m stockwatch

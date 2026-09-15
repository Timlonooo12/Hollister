"""`python -m stockwatch cookie` — enregistrer un cookie de navigateur.

Copier un en-tête `Cookie` à la main est pénible et se prête mal au shell : il
contient des `;` que le terminal interprète, des guillemets, et il fait souvent
plus de mille caractères. Les navigateurs savent en revanche exporter une
requête complète en commande cURL d'un clic droit.

Cette commande accepte donc ce collage tel quel — ou un en-tête brut — en
extrait le cookie et l'écrit dans `.env`, sans jamais l'afficher en clair.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# -H 'Cookie: …' / -H "cookie: …" / --header 'Cookie: …' / -b '…'
_HEADER_RE = re.compile(
    r"""(?:-H|--header)\s+(?P<quote>['"])\s*cookie\s*:\s*(?P<value>.*?)(?P=quote)""",
    re.IGNORECASE | re.DOTALL,
)
_JAR_RE = re.compile(r"""(?:-b|--cookie)\s+(?P<quote>['"])(?P<value>.*?)(?P=quote)""", re.DOTALL)
# Un en-tête collé seul : « nom=valeur; nom2=valeur2 »
_RAW_RE = re.compile(r"^\s*(?:cookie\s*:\s*)?(?P<value>[^\s=;]+=[^;]*(?:;\s*[^\s=;]+=[^;]*)*)\s*$",
                     re.IGNORECASE | re.DOTALL)


def extract_cookie(text: str) -> str | None:
    """Le cookie contenu dans `text`, quelle que soit sa forme."""
    for pattern in (_HEADER_RE, _JAR_RE):
        match = pattern.search(text)
        if match:
            value = " ".join(match.group("value").split())
            if value:
                return value
    flat = " ".join(text.split())
    match = _RAW_RE.match(flat)
    if match and "=" in match.group("value"):
        return match.group("value")
    return None


def write_env(path: Path, key: str, value: str) -> None:
    """Remplace (ou ajoute) `key` dans le fichier .env, sans toucher au reste."""
    line = f"{key}={value}"
    text = path.read_text("utf-8") if path.exists() else ""
    updated, count = re.subn(rf"^{re.escape(key)}=.*$", lambda _: line, text, count=1, flags=re.M)
    if not count:
        updated = (text.rstrip("\n") + "\n" if text.strip() else "") + line + "\n"
    path.write_text(updated, "utf-8")
    path.chmod(0o600)


def mask(value: str) -> str:
    names = [chunk.split("=", 1)[0].strip() for chunk in value.split(";") if "=" in chunk]
    preview = ", ".join(names[:6]) + ("…" if len(names) > 6 else "")
    return f"{len(names)} cookies ({len(value)} caractères) : {preview}"


def run_cookie(env_path: Path) -> int:
    print("Colle ici la requête copiée depuis le navigateur (clic droit sur la ligne du")
    print("document → « Copier en tant que cURL »), ou l'en-tête Cookie seul.")
    print("Termine par Ctrl-D sur une ligne vide.")
    print()
    pasted = sys.stdin.read()

    cookie = extract_cookie(pasted)
    if not cookie:
        print()
        print("❌ Aucun cookie trouvé dans ce que tu as collé.")
        print("   Attendu : une commande cURL contenant -H 'Cookie: …', ou la ligne")
        print("   « nom=valeur; nom2=valeur2 » copiée depuis l'en-tête de la requête.")
        return 1

    write_env(env_path, "STOCKWATCH_COOKIE", cookie)
    print()
    print(f"✅ Cookie enregistré dans {env_path} — {mask(cookie)}")
    print("   Le fichier est en 600 et la valeur ne sera jamais affichée ni journalisée.")
    print()
    print("Redémarre le bot, puis vérifie :")
    print("   sudo systemctl restart stockwatch")
    print("   python -m stockwatch diagnose")
    return 0

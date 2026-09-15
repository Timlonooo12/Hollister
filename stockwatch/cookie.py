"""`python -m stockwatch cookie` — enregistrer un cookie de navigateur.

Copier un en-tête `Cookie` à la main est pénible et se prête mal au shell : il
contient des `;` que le terminal interprète, des guillemets, et il dépasse
souvent le millier de caractères. Les navigateurs savent en revanche exporter
une requête complète en commande cURL d'un clic droit.

Cette commande accepte donc ce collage tel quel, en extrait le cookie — ainsi
que le User-Agent et la langue, que les pare-feux applicatifs vérifient avec
lui — et les écrit dans `.env` sans jamais les afficher en clair.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Safari et Chrome exportent  -H $'Cookie: …'  dès que la valeur contient une
# apostrophe ou un accent : le dollar introduit une chaîne à échappements
# (antislash-apostrophe pour une apostrophe, antislash-x suivi de deux chiffres
# hexadécimaux pour un octet). Il faut donc accepter ce dollar, ne pas s'arrêter
# sur une apostrophe échappée — sinon le cookie est tronqué au premier « women's »
# venu — puis décoder.
_SINGLE = r"'(?P<single>(?:\\.|[^'\\])*)'"
_DOUBLE = r'"(?P<double>(?:\\.|[^"\\])*)"'
_HEADER_RE = re.compile(rf"(?:-H|--header)\s+(?P<dollar>\$?)(?:{_SINGLE}|{_DOUBLE})", re.DOTALL)
_JAR_RE = re.compile(rf"(?:-b|--cookie)\s+(?P<dollar>\$?)(?:{_SINGLE}|{_DOUBLE})", re.DOTALL)
# Un en-tête collé seul : « nom=valeur; nom2=valeur2 »
_RAW_RE = re.compile(
    r"^\s*(?:cookie\s*:\s*)?(?P<value>[^\s=;]+=[^;]*(?:;\s*[^\s=;]+=[^;]*)*)\s*$",
    re.IGNORECASE | re.DOTALL,
)

_ESCAPES = {"n": b"\n", "t": b"\t", "r": b"\r", "\\": b"\\", "'": b"'", '"': b'"'}


def unescape(value: str) -> str:
    """Décode une chaîne à échappements de shell.

    Les octets hexadécimaux sont réassemblés avant d'être décodés en UTF-8 :
    les décoder un par un couperait les accents en deux.
    """
    out = bytearray()
    index = 0
    while index < len(value):
        char = value[index]
        if char != "\\" or index + 1 >= len(value):
            out += char.encode("utf-8")
            index += 1
            continue
        following = value[index + 1]
        if following == "x" and index + 4 <= len(value):
            try:
                out.append(int(value[index + 2:index + 4], 16))
                index += 4
                continue
            except ValueError:
                pass
        out += _ESCAPES.get(following, ("\\" + following).encode("utf-8"))
        index += 2
    return out.decode("utf-8", "replace")


def _values(text: str, pattern: re.Pattern[str]) -> list[str]:
    found: list[str] = []
    for match in pattern.finditer(text):
        raw = match.group("single")
        if raw is None:
            raw = match.group("double") or ""
        found.append(unescape(raw) if match.group("dollar") or "\\" in raw else raw)
    return found


def extract_headers(text: str) -> dict[str, str]:
    """Les en-têtes d'une commande cURL copiée depuis un navigateur."""
    headers: dict[str, str] = {}
    for value in _values(text, _HEADER_RE):
        name, separator, content = value.partition(":")
        if separator:
            headers.setdefault(name.strip().lower(), content.strip())
    return headers


def extract_cookie(text: str) -> str | None:
    """Le cookie contenu dans `text`, quelle que soit sa forme."""
    header = extract_headers(text).get("cookie")
    if header:
        return " ".join(header.split())
    for value in _values(text, _JAR_RE):
        if "=" in value:
            return " ".join(value.split())
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
        print("   Attention à copier la ligne du DOCUMENT (la page), pas un fichier .js.")
        return 1

    write_env(env_path, "STOCKWATCH_COOKIE", cookie)
    print()
    print(f"✅ Cookie enregistré dans {env_path} — {mask(cookie)}")

    # Un cookie anti-bot est lié au navigateur qui l'a obtenu : réutilisé avec
    # un autre User-Agent, il est souvent refusé. On reprend donc les deux.
    headers = extract_headers(pasted)
    for header, key in (("user-agent", "STOCKWATCH_USER_AGENT"),
                        ("accept-language", "STOCKWATCH_ACCEPT_LANGUAGE")):
        value = headers.get(header)
        if value:
            write_env(env_path, key, value)
            print(f"✅ {key} repris du navigateur : {value[:60]}{'…' if len(value) > 60 else ''}")

    print("   Le fichier est en 600 ; le cookie n'apparaîtra ni dans les logs ni dans /status.")
    print()
    print("Redémarre le bot, puis vérifie :")
    print("   sudo systemctl restart stockwatch")
    print("   python -m stockwatch diagnose")
    return 0

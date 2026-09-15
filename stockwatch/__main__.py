"""Command line entry point.

    python -m stockwatch                 # run the bot + the watcher
    python -m stockwatch diagnose        # show what the parser reads from the page
    python -m stockwatch diagnose --file page.html --save dump.html
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from pydantic import ValidationError

from . import __version__
from .app import StartupError, configure_logging, run
from .config import load_settings
from .diagnose import run_diagnose

_TOKEN_HELP = (
    "❌ Configuration incomplète : STOCKWATCH_BOT_TOKEN est obligatoire.\n"
    "   1. Crée un bot avec @BotFather sur Telegram et copie le token.\n"
    "   2. Copie .env.example vers .env puis renseigne STOCKWATCH_BOT_TOKEN.\n"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m stockwatch",
        description="Bot Telegram qui alerte dès qu'une taille revient en stock.",
    )
    parser.add_argument("--version", action="version", version=f"stockwatch {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="Démarrer le bot et la surveillance (défaut)")
    diagnose = sub.add_parser("diagnose", help="Vérifier ce que le parseur lit sur la page")
    diagnose.add_argument("--url", help="URL à analyser (défaut : le produit configuré)")
    diagnose.add_argument("--file", help="Analyser un fichier HTML/JSON déjà téléchargé")
    diagnose.add_argument("--save", help="Enregistrer la réponse brute dans ce fichier")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "diagnose":
        settings = load_settings(allow_missing_token=True)
        configure_logging(settings.log_level)
        try:
            return run_diagnose(settings, url=args.url, file=args.file, save=args.save)
        except BrokenPipeError:
            # `... | head` ferme le tuyau : ce n'est pas une erreur du diagnostic.
            return 0

    try:
        settings = load_settings()
    except ValidationError as exc:
        print(_TOKEN_HELP, file=sys.stderr)
        print(exc, file=sys.stderr)
        return 2

    try:
        asyncio.run(run(settings))
    except StartupError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

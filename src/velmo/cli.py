"""REPL de conversation Velmo 2.0 (commandes, dispo, FAQ) — démarre après seed."""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

from .agent import build_default_agent
from .config import validate_startup


def main() -> None:
    load_dotenv()
    validate_startup()
    parser = argparse.ArgumentParser(description="Chat support Velmo 2.0")
    parser.add_argument("--user", default="C-marc-dubois", help="Identifiant client authentifié")
    args = parser.parse_args()

    agent = build_default_agent()
    print(f"Velmo 2.0 prêt (client {args.user}). Posez votre question (Ctrl+C pour quitter).")
    while True:
        try:
            message = input("\nVous : ").strip()
            if not message:
                continue
            print(f"\nVelmo : {agent.respond(args.user, message)}")
        except (KeyboardInterrupt, EOFError):
            print("\nÀ bientôt !")
            break


if __name__ == "__main__":
    main()

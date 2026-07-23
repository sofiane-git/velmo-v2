"""REPL de conversation Velmo 2.0 (commandes, dispo, FAQ) — démarre après seed."""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

from .agent import build_default_agent
from .config import validate_startup


def _run_purge() -> None:
    from velmo.memory import MemoryManager
    from velmo.memory.retention import purge_expired_episodes, purge_inactive_threads

    manager = MemoryManager()
    session = manager._Session()
    try:
        removed_episodes = purge_expired_episodes(session)
        session.commit()
    finally:
        session.close()
    removed_threads = purge_inactive_threads(manager)
    print(
        f"Purge : {removed_episodes} épisode(s) expiré(s), "
        f"{removed_threads} thread(s) inactif(s)."
    )


def main() -> None:
    load_dotenv()
    validate_startup()
    parser = argparse.ArgumentParser(description="Chat support Velmo 2.0")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("purge", help="Purge RGPD (épisodes 24 mois, threads inactifs 90 jours)")
    parser.add_argument("--user", default="C-marc-dubois", help="Identifiant client authentifié")
    args = parser.parse_args()

    if args.command == "purge":
        _run_purge()
        return

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

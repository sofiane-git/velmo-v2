"""Identité d'une version : hash SHA256 des fichiers/config source (jamais un
numéro choisi à la main), tag git — voir conception_chantier3_evaluation_mlops.md
§Qu'est-ce qu'une version de Velmo 2.0."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _prompt_hash() -> str:
    from velmo.agent import SYSTEM_PROMPT

    return _sha256(SYSTEM_PROMPT)


def _memory_config_hash() -> str:
    from velmo.config import get_settings

    settings = get_settings()
    payload = {
        "memory_confidence_threshold": settings.memory_confidence_threshold,
        "embedding_model": settings.embedding_model,
    }
    return _sha256(json.dumps(payload, sort_keys=True))


def _guardrail_config_hash() -> str:
    from velmo.guardrails.judge import SCOPE_POLICY_PATH
    from velmo.guardrails.pipeline import BLOCK_THRESHOLD, ESCALATE_THRESHOLD, FLAG_THRESHOLD

    scope_policy_text = SCOPE_POLICY_PATH.read_text(encoding="utf-8")
    payload = {
        "block_threshold": BLOCK_THRESHOLD,
        "flag_threshold": FLAG_THRESHOLD,
        "escalate_threshold": ESCALATE_THRESHOLD,
        "scope_policy": scope_policy_text,
    }
    return _sha256(json.dumps(payload, sort_keys=True))


def compute_version_hashes() -> dict[str, str]:
    """Hash de chaque composante versionnée — change automatiquement dès
    qu'un fichier/seuil change, jamais un numéro oublié après un ajustement."""
    return {
        "prompt_hash": _prompt_hash(),
        "memory_config_hash": _memory_config_hash(),
        "guardrail_config_hash": _guardrail_config_hash(),
    }


def current_git_commit() -> str:
    """Hash court du commit courant — `"unknown"` hors dépôt git (ex. artefact
    déployé sans historique git embarqué), jamais une exception qui bloquerait
    l'évaluation pour une raison purement environnementale."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[3],
            timeout=5,
        )
        commit = result.stdout.strip()
        return commit if commit else "unknown"
    except Exception:
        return "unknown"


def current_git_tag() -> str | None:
    """Tag semver exact du commit courant (`v1.2.3`), ou `None` si HEAD n'est
    pas exactement sur un tag — utilisé par `current_version()` pour que la
    version persistée d'un run `release.yml` (déclenché par `push: tags:
    v*.*.*`) reflète le tag réel, pas seulement le commit."""
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--exact-match"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[3],
            timeout=5,
        )
        tag = result.stdout.strip()
        return tag if result.returncode == 0 and tag else None
    except Exception:
        return None

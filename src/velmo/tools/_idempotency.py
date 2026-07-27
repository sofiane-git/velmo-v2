"""Idempotence et journalisation des actions métier — Ch.4 §A5/A6.

Le trou fermé ici : chaque outil d'écriture générait un identifiant neuf et
committait, sans aucune contrainte en base. Un retry — retry réseau, tour rejoué,
agent qui rappelle l'outil parce que la réponse a été perdue — produisait **deux
remboursements**, deux retours ouverts, deux escalades.

**D'où vient la clé.** Elle est dérivée du **contenu** de l'appel (utilisateur,
outil, arguments normalisés), pas d'un identifiant de requête. Un identifiant
généré par appel serait neuf à chaque retry, donc inutile dans le seul cas qu'il
doit couvrir. Conséquence assumée : deux actions **réellement** distinctes mais
strictement identiques (même client, même montant, même motif) sont vues comme un
rejeu. Pour un remboursement, c'est le bon compromis — un second geste commercial
voulu porte un motif différent, alors qu'un double paiement ne se rattrape pas.

**Où vit la garantie.** Sur la contrainte `UNIQUE` de `tool_audit`, pas sur un
`SELECT` préalable : entre deux appels concurrents, une vérification applicative
perd la course — et la concurrence est exactement le scénario du retry.

Un appelant qui dispose d'une meilleure notion d'identité de requête (le jeton de
confirmation de A4, quand il existera) peut la passer en `explicit_key`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import ToolAudit

logger = logging.getLogger(__name__)

# Issues où **rien n'a été écrit** : la clé est libérée, l'action reste
# réessayable. Le critère est l'existence d'un effet, pas le fait que l'appel ait
# « réussi » — un refus d'état (« commande déjà expédiée ») et un dépassement de
# plafond **ouvrent tous deux une escalade**, donc ils écrivent, donc ils doivent
# être dédoublonnés comme un succès. Ne relâcher que ce qui est réellement sans
# trace : sinon un retry réveille deux fois un humain.
NO_EFFECT_OUTCOMES = frozenset({"refused_ownership", "error"})

# Champs d'arguments dont la valeur ne doit jamais atterrir en clair dans le
# journal. Le masquage passe par les mêmes fonctions que le reste du système
# plutôt que par une liste maison de motifs.
_SENSITIVE_ARG_KEYS = frozenset({"address", "shipping_address", "card", "payment"})


def compute_key(user_id: str, tool: str, arguments: dict[str, Any]) -> str:
    """Clé stable pour un appel donné : même contenu ⇒ même clé, quel que soit
    l'ordre des arguments ou le formatage des nombres."""
    canonical = json.dumps(
        {"user_id": user_id, "tool": tool, "arguments": arguments},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _mask_arguments(arguments: dict[str, Any]) -> str:
    """Sérialise les arguments avec les PII masquées (Ch.4 §A6).

    Deux niveaux : les champs structurellement sensibles (adresse, moyen de
    paiement) sont réduits à un marqueur, et le reste passe par la redaction PII
    des garde-fous — un numéro de carte collé dans un motif de remboursement est
    tout aussi sensible qu'un champ nommé `card`.
    """
    from velmo.guardrails import redact_pii

    safe: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in _SENSITIVE_ARG_KEYS:
            safe[key] = "[masqué]"
        elif isinstance(value, str):
            safe[key] = redact_pii(value)
        else:
            safe[key] = value
    return redact_pii(json.dumps(safe, sort_keys=True, default=str))


def idempotent_action(
    session: Session,
    *,
    user_id: str,
    tool: str,
    tool_class: str,
    arguments: dict[str, Any],
    action: Callable[[], dict[str, Any]],
    resource_id: str | None = None,
    outcome_of: Callable[[dict[str, Any]], str] | None = None,
    explicit_key: str | None = None,
    source_thread_id: str | None = None,
) -> dict[str, Any]:
    """Exécute `action` **au plus une fois** pour un contenu d'appel donné, et
    journalise le résultat.

    Séquence, dans cet ordre précis :

    1. **Réservation de la clé** (`INSERT` du journal) *avant* l'effet. Si la clé
       existe déjà, on ne réexécute pas : on renvoie le résultat mémorisé. Réserver
       après l'effet laisserait une fenêtre où deux appels concurrents
       s'exécuteraient tous les deux avant que l'un ne découvre l'autre.
    2. **Exécution** de l'action.
    3. **Complétion** de la ligne (résultat, issue, latence).

    Une issue sans effet de bord (refus, plafond, erreur) **libère la clé** : elle
    ne doit pas geler une action qui redeviendra légitime.
    """
    import time

    key = explicit_key or compute_key(user_id, tool, arguments)

    existing = session.scalars(
        select(ToolAudit).where(ToolAudit.idempotency_key == key)
    ).one_or_none()
    if existing is not None and existing.result is not None:
        logger.info("Action rejouée (idempotence) : tool=%s user_id=%s", tool, user_id)
        session.add(
            ToolAudit(
                id=f"ta-{uuid.uuid4().hex[:8]}",
                user_id=user_id,
                tool=tool,
                tool_class=tool_class,
                arguments=_mask_arguments(arguments),
                outcome="replayed",
                resource_id=resource_id,
                # Pas de clé sur la ligne de rejeu : la clé appartient à l'appel
                # qui a produit l'effet, et l'unicité doit rester intacte.
                idempotency_key=None,
                source_thread_id=source_thread_id,
            )
        )
        session.commit()
        return dict(json.loads(existing.result))

    audit_id = f"ta-{uuid.uuid4().hex[:8]}"
    reservation = ToolAudit(
        id=audit_id,
        user_id=user_id,
        tool=tool,
        tool_class=tool_class,
        arguments=_mask_arguments(arguments),
        outcome="in_progress",
        resource_id=resource_id,
        idempotency_key=key,
        source_thread_id=source_thread_id,
    )
    session.add(reservation)
    try:
        session.flush()
    except IntegrityError:
        # Course perdue : un appel concurrent a réservé la même clé entre notre
        # `SELECT` et notre `INSERT`. C'est le scénario que la contrainte existe
        # pour couvrir — on ne réexécute pas.
        session.rollback()
        winner = session.scalars(
            select(ToolAudit).where(ToolAudit.idempotency_key == key)
        ).one_or_none()
        if winner is not None and winner.result is not None:
            return dict(json.loads(winner.result))
        raise

    start = time.monotonic()
    result = action()
    latency_ms = int((time.monotonic() - start) * 1000)

    outcome = outcome_of(result) if outcome_of is not None else "ok"
    row = session.get(ToolAudit, audit_id)
    assert row is not None  # inséré juste au-dessus, dans la même session
    row.outcome = outcome
    row.latency_ms = latency_ms
    if outcome in NO_EFFECT_OUTCOMES:
        row.idempotency_key = None  # rien n'a eu lieu : l'action reste réessayable
        row.result = None
    else:
        row.result = json.dumps(result, default=str)
    session.commit()
    return result

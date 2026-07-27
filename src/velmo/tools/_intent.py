"""Jetons d'intention : confirmation explicite des actions irréversibles (Ch.4 §A4).

Le Ch.2 revendiquait que les actions sensibles « restent soumises à confirmation
[...] indépendamment de ce que l'agent dit vouloir faire ». C'était faux : la
confirmation vivait dans le **texte du message courant**, donc un seul message
pouvait porter la demande *et* sa confirmation. Une injection s'auto-validait, et
rien ne liait un « oui » à une action précise.

Le jeton déplace l'autorité d'un texte interprétable vers un état persisté qui
porte **quatre** propriétés vérifiables, et c'est la conjonction des quatre qui
fait le contrôle :

| Propriété | Ce qu'elle empêche |
| --- | --- |
| lié à l'utilisateur | qu'un jeton fuité devienne une autorisation transférable |
| arguments figés | que confirmer 20 € autorise 500 € |
| usage unique | qu'une confirmation devienne un blanc-seing permanent |
| expiration courte | qu'une confirmation oubliée reste exploitable des jours après |

Le « oui » du client reste un tour de conversation ordinaire ; ce qui fait
autorité, c'est le rapprochement avec le jeton — pas l'interprétation, par le
LLM, d'un acquiescement ambigu.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db import PendingAction, utcnow_naive

logger = logging.getLogger(__name__)

# Fenêtre volontairement courte : une confirmation est un geste immédiat. Assez
# large pour un échange de support normal (le client relit, pose une question,
# confirme), assez serrée pour qu'une intention oubliée cesse d'être exploitable.
INTENT_TTL = timedelta(minutes=15)

# Outils dont l'exécution exige un jeton — classe I du Ch.4 (irréversible :
# mouvement d'argent, annulation). Les écritures réversibles (classe É) gardent la
# confirmation conversationnelle : le coût d'une erreur y est récupérable, la
# friction d'un jeton ne s'y justifie pas.
TOKEN_REQUIRED_TOOLS = frozenset({"trigger_refund", "cancel_order"})


def arguments_hash(arguments: dict[str, Any]) -> str:
    """Empreinte stable des arguments : même contenu ⇒ même empreinte, quel que
    soit l'ordre des clés."""
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PreparedIntent:
    """Résultat de la préparation. `token` est `None` si les préconditions ont
    échoué : dans ce cas il n'y a rien à confirmer, et `refusal` dit pourquoi —
    proposer au client de confirmer une action vouée à échouer serait une fausse
    promesse."""

    token: str | None
    recap: str | None = None
    refusal: str | None = None


@dataclass(frozen=True)
class IntentVerdict:
    """Verdict de consommation. `reason` prend `unknown`, `wrong_user`,
    `wrong_tool`, `arguments_changed`, `expired`, `already_consumed`."""

    ok: bool
    reason: str | None = None


def _recap(tool: str, arguments: dict[str, Any]) -> str:
    """Récapitulatif lisible : c'est **lui** que le client confirme, il doit donc
    porter les éléments décisifs (quoi, sur quoi, combien) et non un identifiant
    opaque."""
    order_id = arguments.get("order_id", "—")
    if tool == "trigger_refund":
        amount = arguments.get("amount")
        return (
            f"Rembourser {amount} € sur la commande {order_id} "
            f"(motif : {arguments.get('reason', '—')}) — confirmez-vous ?"
        )
    if tool == "cancel_order":
        return f"Annuler définitivement la commande {order_id} — confirmez-vous ?"
    return f"Confirmer l'action {tool} sur {order_id} ?"


def prepare_intent(
    session: Session,
    *,
    user_id: str,
    tool: str,
    arguments: dict[str, Any],
    resource_id: str | None = None,
    precondition_refusal: str | None = None,
) -> PreparedIntent:
    """Valide les préconditions **sans produire l'effet**, puis persiste
    l'intention et renvoie le jeton et son récapitulatif.

    **Ce qui est validé ici, et ce qui ne l'est pas.** L'**appartenance** (A1) est
    vérifiée : proposer de confirmer une action sur une commande qui n'est pas la
    sienne serait une fausse promesse, et divulguerait l'existence de la commande.
    En revanche la **fenêtre d'état** (A2) et le **plafond** (A3) ne bloquent pas
    la préparation : leurs issues (« déjà expédiée », « au-dessus du plafond »)
    sont des résultats **légitimes** que le client doit s'entendre dire, avec
    l'escalade qui va avec — les traiter en refus de préparation masquerait
    l'escalade.

    `precondition_refusal` : motif déjà établi par l'appelant, quand celui-ci
    connaît une règle propre à son outil. Renseigné ⇒ aucune intention n'est créée.
    """
    if precondition_refusal is not None:
        return PreparedIntent(token=None, refusal=precondition_refusal)

    order_id = arguments.get("order_id") or resource_id
    if order_id is not None:
        from ._common import owned_order

        if owned_order(session, str(order_id), user_id) is None:
            # Même message indistinguable que les outils (A1) : ne pas faire de la
            # préparation un oracle d'énumération des commandes.
            return PreparedIntent(token=None, refusal="not_found_or_forbidden")

    token = f"tok-{uuid.uuid4().hex}"
    session.add(
        PendingAction(
            token=token,
            user_id=user_id,
            tool=tool,
            arguments_hash=arguments_hash(arguments),
            arguments_json=json.dumps(arguments, sort_keys=True, default=str),
            resource_id=resource_id,
            recap=_recap(tool, arguments),
            created_at=utcnow_naive(),
            expires_at=utcnow_naive() + INTENT_TTL,
        )
    )
    session.commit()
    return PreparedIntent(token=token, recap=_recap(tool, arguments))


def consume_intent(
    session: Session,
    *,
    token: str | None,
    user_id: str,
    tool: str,
    arguments: dict[str, Any],
) -> IntentVerdict:
    """Consomme un jeton et dit si l'action est autorisée.

    L'usage unique est garanti par un **`UPDATE` conditionnel**
    (`WHERE consumed_at IS NULL`) et non par un `SELECT` suivi d'un `UPDATE` :
    entre les deux, deux appels concurrents passeraient tous les deux la
    vérification. Même raisonnement que la clé d'idempotence (§A5) — la garantie
    vit dans une opération atomique du store, jamais dans une séquence
    applicative.
    """
    if not token:
        return IntentVerdict(ok=False, reason="unknown")

    row = session.scalars(select(PendingAction).where(PendingAction.token == token)).one_or_none()
    if row is None:
        return IntentVerdict(ok=False, reason="unknown")
    if row.user_id != user_id:
        # Journalisé : un jeton présenté par un autre utilisateur est un signal,
        # pas une simple erreur de saisie.
        logger.warning(
            "Jeton d'intention présenté par un autre utilisateur (attendu=%s, reçu=%s).",
            row.user_id,
            user_id,
        )
        return IntentVerdict(ok=False, reason="wrong_user")
    if row.tool != tool:
        return IntentVerdict(ok=False, reason="wrong_tool")
    if row.arguments_hash != arguments_hash(arguments):
        logger.warning(
            "Jeton d'intention présenté avec des arguments modifiés (tool=%s, user=%s).",
            tool,
            user_id,
        )
        return IntentVerdict(ok=False, reason="arguments_changed")
    if row.consumed_at is not None:
        return IntentVerdict(ok=False, reason="already_consumed")
    if row.expires_at <= utcnow_naive():
        return IntentVerdict(ok=False, reason="expired")

    claimed = session.execute(
        update(PendingAction)
        .where(PendingAction.token == token, PendingAction.consumed_at.is_(None))
        .values(consumed_at=utcnow_naive())
    )
    if claimed.rowcount != 1:  # type: ignore[attr-defined]
        # Course perdue : un appel concurrent a consommé le jeton entre nos
        # vérifications et notre `UPDATE`.
        session.commit()
        return IntentVerdict(ok=False, reason="already_consumed")
    session.commit()
    return IntentVerdict(ok=True)


def purge_stale_intents(session: Session) -> int:
    """Supprime les intentions **consommées ou expirées**.

    Une intention transitoire n'est pas un journal : passé sa consommation ou son
    expiration, elle n'a plus aucune valeur et ne doit pas subsister — elle porte
    les arguments en clair (nécessaire pour rejouer l'action confirmée au
    caractère près, ce qu'un masquage empêcherait). C'est la contrepartie de ce
    choix, et la règle du projet « tout TTL déclaré est branché sur un déclencheur
    automatique vérifiable » : ce déclencheur est la commande de purge
    (`velmo purge`), à côté des purges d'épisodes et de threads.
    """
    from sqlalchemy import delete, or_

    result = session.execute(
        delete(PendingAction).where(
            or_(
                PendingAction.consumed_at.is_not(None),
                PendingAction.expires_at <= utcnow_naive(),
            )
        )
    )
    session.commit()
    return int(result.rowcount)  # type: ignore[attr-defined]


def find_pending(
    session: Session, *, user_id: str, tool: str | None = None
) -> PendingAction | None:
    """Intention la plus récente encore consommable pour cet utilisateur.

    Sert au chemin conversationnel : quand le client répond « je confirme » au
    tour suivant, l'agent n'a pas à transporter le jeton dans l'état de la
    conversation — il retrouve l'intention en base. Filtré par `user_id`, donc la
    confirmation d'un client ne peut pas consommer l'intention d'un autre.
    """
    stmt = (
        select(PendingAction)
        .where(
            PendingAction.user_id == user_id,
            PendingAction.consumed_at.is_(None),
            PendingAction.expires_at > utcnow_naive(),
        )
        .order_by(PendingAction.created_at.desc())
    )
    if tool is not None:
        stmt = stmt.where(PendingAction.tool == tool)
    return session.scalars(stmt).first()

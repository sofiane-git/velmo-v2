"""Agent Velmo 2.0 : garde-fou d'entrée → mémoire → routage outils → garde-fou
de sortie → écriture mémoire.

Le routage, les outils, la mémoire et les garde-fous de contenu sont
opérationnels. Seul le MLOps (chantier 3) reste à construire.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

from . import tools
from .guardrails import GENERIC_REFUSAL, Decision, GuardrailEngine, redact_pii, redact_secret_leak
from .kb_store import KnowledgeBase
from .llm import LLM, get_llm
from .memory import FACT_KEY_ALIASES, ForgetReport, MemoryContext, MemoryManager, WriteReport

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Tu es l'assistant de support de Velmo, boutique de maillots de foot collector. "
    "Tu traites la gestion de commandes de niveau 1 avec courtoisie et précision. "
    "Structure toujours ta réponse en Markdown pour qu'elle soit visuelle et facile à "
    "scanner : titres courts en gras, listes à puces ou numérotées, emphase sur les "
    "informations clés, un emoji pertinent en ouverture si utile. Évite les longs "
    "paragraphes denses. Ne révèle jamais de détail d'implémentation interne (nom de "
    "fichier, nom de variable ou de champ technique, requête, nom de table ou "
    "d'architecture) : reformule toujours ces informations en langage client naturel."
)

DEFAULT_REFUSAL = GENERIC_REFUSAL

ORDER_RE = re.compile(r"O-\d{4}-\d{4}")
SIZE_RE = re.compile(r"\b(XXL|XL|S|M|L)\b")
AMOUNT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:€|euros?)")
_CONFIRM = ("je confirme", "confirme", "c'est confirmé", "oui je", "vas-y")

# Alias conviviaux -> référence produit.
_ALIASES = {
    "om 1993": "om-1993",
    "marseille 1993": "om-1993",
    "france 98": "france-1998",
    "france 1998": "france-1998",
    "united 99": "mu-1999-treble",
    "mu 1999": "mu-1999-treble",
    "manchester 1999": "mu-1999-treble",
    "bresil 1970": "brazil-1970",
    "brésil 1970": "brazil-1970",
}

_FAQ_KEYWORDS = (
    "frais de port",
    "frais de livraison",
    "délai",
    "delai",
    "politique de retour",
    "authenticit",
    "certificat",
    "paiement",
    "réassort",
    "reassort",
    "rétractation",
    "retractation",
    "entretien",
    "garantie",
    "remboursement sous",
    "conditions d'échange",
)

_FORGET_TRIGGERS = ("oublie", "efface", "supprime")
_FORGET_ALL_RE = re.compile(r"\btoute?s?\b")


@dataclass
class RoutingInfo:
    handler: str  # "tool" | "faq_rag" | "llm_libre"
    tool_name: str | None = None
    order_id: str | None = None
    query: str | None = None
    tool_result: dict[str, Any] | None = None


def _guardrail_payload(decision: Decision) -> dict[str, Any]:
    return {
        "hits": [asdict(h) for h in decision.hits],
        "allowed": decision.allowed,
        "escalate": decision.escalate,
    }


def _memory_read_payload(context: MemoryContext) -> dict[str, Any]:
    return {
        "history_turns": len(context.history),
        "summary_used": any(role == "résumé" for role, _ in context.history),
        "facts_matched": [asdict(f) for f in context.facts_detailed],
        "episodic_matched": list(context.episodic),
    }


def _routing_payload(routing: RoutingInfo) -> dict[str, Any]:
    return {
        "handler": routing.handler,
        "detail": {
            "tool_name": routing.tool_name,
            "order_id": routing.order_id,
            "query": routing.query,
        },
    }


def _is_repeat_question(context: MemoryContext, message: str) -> bool:
    """Le même message (mot pour mot) apparaît déjà côté "user" dans l'historique.

    Les routes "tool"/"faq_rag" sont déterministes et re-vérifient toujours la
    donnée live (statut de commande, stock...) plutôt que de répondre depuis
    la mémoire — ce qui est correct pour de la donnée qui peut changer, mais
    laisse l'agent répondre à l'identique sans jamais signaler la répétition
    (contrairement à la route `llm_libre`, qui voit `context` nativement).
    """
    normalized = message.strip().lower()
    return any(
        role == "user" and content.strip().lower() == normalized
        for role, content in context.history
    )


def _forget_report_payload(target: str, report: ForgetReport) -> dict[str, Any]:
    return {
        "target": target,
        "removed": report.count,
        "facts": [asdict(f) for f in report.facts],
        "procedures": [asdict(p) for p in report.procedures],
        "episodes": report.episodes,
    }


def _write_report_payload(report: WriteReport) -> dict[str, Any]:
    return {
        "facts_written": [f.model_dump() for f in report.facts_written],
        "procedures_written": [p.model_dump() for p in report.procedures_written],
        "episode_created": report.episode_created,
        "pending": report.pending,
    }


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


class Agent:
    """Assistant de support adossé aux outils métier et à la FAQ."""

    def __init__(
        self,
        llm: LLM,
        memory: MemoryManager,
        guardrails: GuardrailEngine,
        session: Session,
        kb: KnowledgeBase,
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.guardrails = guardrails
        self.session = session
        self.kb = kb

    def respond_traced(
        self, user_id: str, message: str
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        start = time.monotonic()
        gate_in = self.guardrails.check_input(message, user_id=user_id)
        yield "input_guardrail", _guardrail_payload(gate_in)
        if not gate_in.allowed:
            refusal = gate_in.refusal or DEFAULT_REFUSAL
            if gate_in.escalate:
                tools.escalate_to_human(
                    self.session, user_id, f"garde-fou {gate_in.category} (entrée)"
                )
            stored_message = message
            if gate_in.category == "pii":
                stored_message = redact_pii(message)
            elif gate_in.category == "secret_leak":
                stored_message = redact_secret_leak(message)
            self.memory.write(user_id, stored_message, refusal, background=True)
            yield "final", {
                "answer": refusal,
                "status": "blocked_input",
                "latency_ms": _elapsed_ms(start),
            }
            return

        context = self.memory.read(user_id, message)
        yield "memory_read", _memory_read_payload(context)

        try:
            answer, routing = self._handle(user_id, message, context)
            if routing.handler in ("tool", "faq_rag") and _is_repeat_question(context, message):
                answer = f"Vous me l'avez déjà demandé — je reviens de vérifier. {answer}"
        except Exception:
            # Un échec en aval (ex. le LLM principal refuse la requête) ne doit
            # pas interrompre le flux SSE au milieu — le client verrait une
            # connexion coupée sans event "final" exploitable.
            logger.exception("respond_traced: échec en aval pour user_id=%s", user_id)
            yield "final", {
                "answer": DEFAULT_REFUSAL,
                "status": "error",
                "latency_ms": _elapsed_ms(start),
            }
            return
        yield "routing", _routing_payload(routing)
        if routing.tool_result is not None:
            yield "tool_result", {"name": routing.tool_name, "result": routing.tool_result}

        gate_out = self.guardrails.check_output(answer, user_id=user_id)
        yield "output_guardrail", _guardrail_payload(gate_out)
        status = "ok"
        if gate_out.action == "block":
            if gate_out.escalate:
                tools.escalate_to_human(
                    self.session, user_id, f"garde-fou {gate_out.category} (sortie)"
                )
            answer = gate_out.refusal or DEFAULT_REFUSAL
            status = "blocked_output"
        elif gate_out.action == "filter" and gate_out.filtered_text is not None:
            answer = gate_out.filtered_text
            status = "filtered_output"

        # `background=True` : l'extraction long terme appelle le même LLM que
        # la génération de réponse (`self.llm`) — un endpoint lent/indisponible
        # ne doit jamais retarder une réponse déjà validée par les garde-fous
        # (cf. MemoryManager.write). La persistance du tour (history) reste
        # synchrone à l'intérieur de `write`.
        write_report = self.memory.write(user_id, message, answer, background=True)
        yield "memory_write", _write_report_payload(write_report)
        yield "final", {"answer": answer, "status": status, "latency_ms": _elapsed_ms(start)}

    def respond(self, user_id: str, message: str) -> str:
        answer = ""
        for event_type, payload in self.respond_traced(user_id, message):
            if event_type == "final":
                answer = payload["answer"]
        return answer

    # --- routage déterministe ------------------------------------------------

    def _handle(
        self, user_id: str, message: str, context: MemoryContext
    ) -> tuple[str, RoutingInfo]:
        low = message.lower()
        order = ORDER_RE.search(message)
        order_id = order.group(0) if order else None
        confirmed = any(c in low for c in _CONFIRM)

        if any(t in low for t in _FORGET_TRIGGERS):
            return self._handle_forget(user_id, low)

        if order_id and "annul" in low:
            answer, result = self._confirm_or_act(
                confirmed,
                "annuler",
                order_id,
                lambda: tools.cancel_order(self.session, order_id, user_id),
            )
            return answer, RoutingInfo(
                handler="tool", tool_name="cancel_order", order_id=order_id, tool_result=result
            )
        if order_id and "adresse" in low:
            answer, result = self._confirm_or_act(
                confirmed,
                "modifier l'adresse de",
                order_id,
                lambda: tools.update_shipping_address(
                    self.session, order_id, user_id, {"line1": "(à préciser)"}
                ),
            )
            return answer, RoutingInfo(
                handler="tool",
                tool_name="update_shipping_address",
                order_id=order_id,
                tool_result=result,
            )
        if (
            order_id
            and "taille" in low
            and any(w in low for w in ("chang", "modif", "tromp", "erreur"))
        ):
            size = SIZE_RE.search(message)
            new_size = size.group(1) if size else "M"
            answer, result = self._confirm_or_act(
                confirmed,
                f"changer la taille (vers {new_size}) de",
                order_id,
                lambda: tools.update_order_item(self.session, order_id, user_id, new_size),
            )
            return answer, RoutingInfo(
                handler="tool", tool_name="update_order_item", order_id=order_id, tool_result=result
            )
        if order_id and any(w in low for w in ("retour", "échange", "echange", "renvoyer")):
            answer, result = self._confirm_or_act(
                confirmed,
                "ouvrir un retour pour",
                order_id,
                lambda: tools.create_return(self.session, order_id, user_id, "Demande client"),
            )
            return answer, RoutingInfo(
                handler="tool", tool_name="create_return", order_id=order_id, tool_result=result
            )
        if order_id and "rembours" in low:
            amount_match = AMOUNT_RE.search(message)
            amount = float(amount_match.group(1).replace(",", ".")) if amount_match else 0.0
            answer, result = self._confirm_or_act(
                confirmed,
                f"rembourser {amount:.0f}€ sur",
                order_id,
                lambda: tools.trigger_refund(
                    self.session, order_id, user_id, amount, "Demande client"
                ),
            )
            return answer, RoutingInfo(
                handler="tool", tool_name="trigger_refund", order_id=order_id, tool_result=result
            )

        if order_id and any(w in low for w in ("suivi", "colis", "livr", "transport", "track")):
            result = tools.track_shipment(self.session, order_id, user_id)
            return self._format_tracking(result), RoutingInfo(
                handler="tool", tool_name="track_shipment", order_id=order_id, tool_result=result
            )
        if order_id:
            result = tools.get_order(self.session, order_id, user_id)
            return self._format_order(result), RoutingInfo(
                handler="tool", tool_name="get_order", order_id=order_id, tool_result=result
            )

        if any(w in low for w in ("dispo", "stock", "reste", "en taille")):
            return self._handle_stock(message, low)

        if any(k in low for k in _FAQ_KEYWORDS):
            result = tools.search_kb(self.kb, message)
            return self._format_kb(result), RoutingInfo(
                handler="faq_rag", tool_name="search_kb", query=message, tool_result=result
            )

        answer = self.llm.invoke(SYSTEM_PROMPT, context.render(), message)
        return answer, RoutingInfo(handler="llm_libre", query=message)

    def _confirm_or_act(
        self, confirmed: bool, label: str, order_id: str, action: Callable[[], dict[str, Any]]
    ) -> tuple[str, dict[str, Any] | None]:
        if not confirmed:
            return (
                f"Pour {label} la commande {order_id}, pouvez-vous confirmer ? "
                "Répondez « je confirme ».",
                None,
            )
        result = action()
        if result.get("error"):
            return f"Je ne trouve pas la commande {order_id} à votre nom.", result
        if result.get("action") == "escalate":
            return (
                f"Cette demande sur la commande {order_id} dépasse ce que je peux faire seul "
                "(commande déjà partie ou montant trop élevé). Je transmets à un conseiller.",
                result,
            )
        return f"C'est fait pour la commande {order_id} ({result.get('action')}).", result

    def _handle_forget(self, user_id: str, low: str) -> tuple[str, RoutingInfo]:
        if _FORGET_ALL_RE.search(low):
            report = self.memory.forget_all(user_id)
            result = _forget_report_payload("all", report)
            return (
                f"C'est fait, j'ai supprimé toute votre mémoire ({report.count} élément(s) "
                "supprimé(s)). On repart de zéro.",
                RoutingInfo(handler="tool", tool_name="memory_forget_all", tool_result=result),
            )
        target = next((alias for alias in FACT_KEY_ALIASES if alias in low), None)
        if target is None:
            return (
                "Que voulez-vous que j'oublie exactement (adresse, taille, club préféré, "
                "contrat...) ?",
                RoutingInfo(handler="tool", tool_name="memory_forget"),
            )
        report = self.memory.forget(user_id, target)
        result = _forget_report_payload(target, report)
        if report.count == 0:
            return (
                "Je n'ai rien trouvé à oublier à ce sujet.",
                RoutingInfo(handler="tool", tool_name="memory_forget", tool_result=result),
            )
        return (
            f"C'est fait, j'ai oublié cette information ({report.count} élément(s) "
            "supprimé(s)).",
            RoutingInfo(handler="tool", tool_name="memory_forget", tool_result=result),
        )

    def _handle_stock(self, message: str, low: str) -> tuple[str, RoutingInfo]:
        ref = self._find_ref(low)
        size = SIZE_RE.search(message)
        if not ref or not size:
            return (
                "Pouvez-vous préciser la référence du maillot et la taille souhaitée ?",
                RoutingInfo(handler="tool", tool_name="check_stock"),
            )
        result = tools.check_stock(self.session, ref, size.group(1))
        if result.get("error"):
            return (
                "Je ne connais pas cette référence dans notre catalogue.",
                RoutingInfo(handler="tool", tool_name="check_stock", tool_result=result),
            )
        if result["available"]:
            answer = f"Le maillot {result['title']} en taille {result['size']} est disponible."
        else:
            answer = f"Le maillot {ref} en taille {result['size']} est indisponible (épuisé)."
        return answer, RoutingInfo(handler="tool", tool_name="check_stock", tool_result=result)

    def _find_ref(self, low: str) -> str | None:
        for alias, ref in _ALIASES.items():
            if alias in low:
                return ref
        if self.session is not None:
            from .db import Product
            from .tools._common import select

            for (ref,) in self.session.execute(select(Product.ref)).all():
                ref = str(ref)
                if ref.lower() in low:
                    return ref
        return None

    @staticmethod
    def _format_order(result: dict[str, Any]) -> str:
        if result.get("error"):
            return "Je ne trouve pas cette commande à votre nom."
        return f"Votre commande {result['order_id']} est au statut « {result['status']} »."

    @staticmethod
    def _format_tracking(result: dict[str, Any]) -> str:
        if result.get("error"):
            return "Je ne trouve pas cette commande à votre nom."
        if not result.get("tracking_number"):
            return f"La commande {result['order_id']} n'est pas encore expédiée."
        return (
            f"Votre colis {result['tracking_number']} ({result['carrier']}) est attendu vers "
            f"{result['estimated_delivery']}."
        )

    @staticmethod
    def _format_kb(result: dict[str, Any]) -> str:
        if not result.get("found"):
            return "Je n'ai pas trouvé cette information dans notre FAQ."
        top = result["results"][0]
        return f"D'après notre FAQ ({top['source']}) : {top['snippet']}"


def build_default_agent(session: Session | None = None, kb: KnowledgeBase | None = None) -> Agent:
    """Assemble un agent avec composants par défaut, base et FAQ."""
    from .db import session_factory
    from .kb_store import get_kb
    from .memory.extractor import LLMExtractor

    if session is None:
        session = session_factory()()
    if kb is None:
        kb = get_kb()
    llm = get_llm()
    return Agent(
        llm=llm,
        memory=MemoryManager(extractor=LLMExtractor(llm)),
        guardrails=GuardrailEngine(),
        session=session,
        kb=kb,
    )

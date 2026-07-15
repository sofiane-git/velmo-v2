"""Jeu de données de référence Velmo (maillots de foot collector).

`seed(session)` insère un catalogue, des clients et des commandes cohérents,
avec des identifiants lisibles. Utilisé par `scripts/seed.py` (Postgres) et par
les tests (SQLite en mémoire).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from .db import (
    Customer,
    Escalation,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    Refund,
    Return,
    Segment,
    Shipment,
)
from .db import Condition as C
from .db import OrderStatus as OS
from .db import RefundStatus as RS
from .db import ReturnStatus as RTS
from .db import Size as SZ

_DT = datetime(2024, 5, 1)


def _customers() -> list[Customer]:
    rows = [
        ("C-marc-dubois", "marc.dubois@example.com", "Marc Dubois", Segment.revendeur),
        ("C-sophie-martin", "sophie.martin@example.com", "Sophie Martin", Segment.particulier),
        ("C-karim-benali", "karim.benali@example.com", "Karim Benali", Segment.pro),
        ("C-lucie-bernard", "lucie.bernard@example.com", "Lucie Bernard", Segment.particulier),
        ("C-thomas-petit", "thomas.petit@example.com", "Thomas Petit", Segment.revendeur),
        ("C-emma-roux", "emma.roux@example.com", "Emma Roux", Segment.particulier),
        ("C-hugo-moreau", "hugo.moreau@example.com", "Hugo Moreau", Segment.particulier),
        ("C-ines-garcia", "ines.garcia@example.com", "Inès Garcia", Segment.pro),
        ("C-paul-laurent", "paul.laurent@example.com", "Paul Laurent", Segment.particulier),
        ("C-nadia-haddad", "nadia.haddad@example.com", "Nadia Haddad", Segment.revendeur),
    ]
    return [Customer(id=i, email=e, full_name=n, segment=s, created_at=_DT) for i, e, n, s in rows]


def _products() -> list[Product]:
    rows = [
        (
            "mu-1999-treble",
            "Manchester United 1999 — Treble",
            "Manchester United",
            "1998-1999",
            "Treble",
            C.mint,
            220,
        ),
        (
            "om-1993",
            "Marseille 1993 — Finale C1",
            "Olympique de Marseille",
            "1992-1993",
            "Finale",
            C.mint,
            180,
        ),
        ("brazil-1970", "Brésil 1970 — Pelé", "Brésil", "1970", "Mondial", C.mint, 300),
        ("france-1998", "France 1998 — Zidane", "France", "1998", "Mondial", C.mint, 250),
        (
            "boca-1981",
            "Boca Juniors 1981 — Maradona",
            "Boca Juniors",
            "1981",
            "Maradona",
            C.occasion,
            280,
        ),
        ("arsenal-1989", "Arsenal 1989 — Adams", "Arsenal", "1988-1989", "Anfield 89", C.neuf, 150),
        (
            "ajax-1995",
            "Ajax 1995 — Kluivert",
            "Ajax",
            "1994-1995",
            "Ligue des Champions",
            C.neuf,
            160,
        ),
        ("italia-90", "Italie 1990 — Azzurri", "Italie", "1990", "Mondial", C.neuf, 140),
        (
            "liverpool-2005",
            "Liverpool 2005 — Istanbul",
            "Liverpool",
            "2004-2005",
            "Istanbul",
            C.neuf,
            130,
        ),
        (
            "inter-1998",
            "Inter 1998 — Ronaldo",
            "Inter Milan",
            "1997-1998",
            "Ronaldo",
            C.occasion,
            200,
        ),
        (
            "psg-1993",
            "PSG 1993 — Ginola",
            "Paris Saint-Germain",
            "1992-1993",
            "Ginola",
            C.occasion,
            170,
        ),
        (
            "milan-1989",
            "Milan 1989 — Van Basten",
            "AC Milan",
            "1988-1989",
            "Van Basten",
            C.mint,
            210,
        ),
        (
            "barca-1992",
            "Barcelone 1992 — Wembley",
            "FC Barcelone",
            "1991-1992",
            "Wembley",
            C.neuf,
            175,
        ),
        (
            "germany-1990",
            "Allemagne 1990 — Weltmeister",
            "Allemagne",
            "1990",
            "Mondial",
            C.neuf,
            145,
        ),
    ]
    return [
        Product(ref=r, title=t, club=cl, season=se, edition=ed, condition=co, base_price=p)
        for r, t, cl, se, ed, co, p in rows
    ]


def _variants() -> list[ProductVariant]:
    # (id, product_ref, size, price, stock) — stock souvent 1, parfois 0 (épuisé).
    rows = [
        ("v-mu-1999-treble-M", "mu-1999-treble", SZ.M, 220, 1),
        ("v-mu-1999-treble-L", "mu-1999-treble", SZ.L, 220, 1),
        ("v-om-1993-M", "om-1993", SZ.M, 180, 0),  # épuisé (test no-fabulation)
        ("v-om-1993-L", "om-1993", SZ.L, 180, 1),
        ("v-brazil-1970-M", "brazil-1970", SZ.M, 300, 1),
        ("v-brazil-1970-L", "brazil-1970", SZ.L, 300, 0),
        ("v-france-1998-L", "france-1998", SZ.L, 250, 1),
        ("v-france-1998-XL", "france-1998", SZ.XL, 250, 1),
        ("v-boca-1981-M", "boca-1981", SZ.M, 280, 1),
        ("v-arsenal-1989-M", "arsenal-1989", SZ.M, 150, 1),
        ("v-ajax-1995-L", "ajax-1995", SZ.L, 160, 1),
        ("v-italia-90-M", "italia-90", SZ.M, 140, 0),
        ("v-liverpool-2005-L", "liverpool-2005", SZ.L, 130, 1),
        ("v-inter-1998-M", "inter-1998", SZ.M, 200, 1),
        ("v-psg-1993-L", "psg-1993", SZ.L, 170, 1),
        ("v-milan-1989-M", "milan-1989", SZ.M, 210, 1),
        ("v-barca-1992-L", "barca-1992", SZ.L, 175, 1),
        ("v-germany-1990-M", "germany-1990", SZ.M, 145, 1),
    ]
    return [
        ProductVariant(id=i, product_ref=pr, size=sz, price=pc, stock=st)
        for i, pr, sz, pc, st in rows
    ]


def _addr(city: str, zip_: str) -> dict[str, Any]:
    return {"line1": "12 rue du Stade", "city": city, "zip": zip_, "country": "France"}


def _orders() -> list[Order]:
    # (id, customer, status, total, address)
    rows = [
        ("O-2024-0101", "C-marc-dubois", OS.prepared, 250, _addr("Lyon", "69003")),
        ("O-2024-0103", "C-marc-dubois", OS.shipped, 220, _addr("Lyon", "69003")),
        ("O-2024-0105", "C-marc-dubois", OS.delivered, 180, _addr("Lyon", "69003")),
        ("O-2024-0107", "C-sophie-martin", OS.paid, 300, _addr("Paris", "75011")),
        ("O-2024-0110", "C-sophie-martin", OS.delivered, 250, _addr("Paris", "75011")),
        ("O-2024-0112", "C-karim-benali", OS.delivered, 200, _addr("Marseille", "13008")),
        ("O-2024-0115", "C-lucie-bernard", OS.cancelled, 140, _addr("Lille", "59000")),
        ("O-2024-0118", "C-thomas-petit", OS.returned, 170, _addr("Nantes", "44000")),
        ("O-2024-0120", "C-emma-roux", OS.shipped, 160, _addr("Toulouse", "31000")),
        ("O-2024-0122", "C-hugo-moreau", OS.prepared, 130, _addr("Bordeaux", "33000")),
        ("O-2024-0124", "C-ines-garcia", OS.shipped, 210, _addr("Nice", "06000")),
        ("O-2024-0126", "C-paul-laurent", OS.paid, 175, _addr("Rennes", "35000")),
        ("O-2024-0128", "C-nadia-haddad", OS.delivered, 145, _addr("Strasbourg", "67000")),
        ("O-2024-0130", "C-karim-benali", OS.prepared, 280, _addr("Marseille", "13008")),
    ]
    return [
        Order(id=i, customer_id=c, status=st, total=t, created_at=_DT, shipping_address=a)
        for i, c, st, t, a in rows
    ]


def _order_items() -> list[OrderItem]:
    rows = [
        ("oi-0101", "O-2024-0101", "v-france-1998-L", SZ.L, 250),
        ("oi-0103", "O-2024-0103", "v-mu-1999-treble-L", SZ.L, 220),
        ("oi-0105", "O-2024-0105", "v-om-1993-L", SZ.L, 180),
        ("oi-0107", "O-2024-0107", "v-brazil-1970-M", SZ.M, 300),
        ("oi-0110", "O-2024-0110", "v-france-1998-XL", SZ.XL, 250),
        ("oi-0112", "O-2024-0112", "v-inter-1998-M", SZ.M, 200),
        ("oi-0115", "O-2024-0115", "v-italia-90-M", SZ.M, 140),
        ("oi-0118", "O-2024-0118", "v-psg-1993-L", SZ.L, 170),
        ("oi-0120", "O-2024-0120", "v-ajax-1995-L", SZ.L, 160),
        ("oi-0122", "O-2024-0122", "v-liverpool-2005-L", SZ.L, 130),
        ("oi-0124", "O-2024-0124", "v-milan-1989-M", SZ.M, 210),
        ("oi-0126", "O-2024-0126", "v-barca-1992-L", SZ.L, 175),
        ("oi-0128", "O-2024-0128", "v-germany-1990-M", SZ.M, 145),
        ("oi-0130", "O-2024-0130", "v-boca-1981-M", SZ.M, 280),
    ]
    return [
        OrderItem(id=i, order_id=o, variant_id=v, size=sz, unit_price=p) for i, o, v, sz, p in rows
    ]


def _shipments() -> list[Shipment]:
    rows = [
        ("sh-0103", "O-2024-0103", "Colissimo", "6A1234567890", "2024-05-06", None),
        ("sh-0105", "O-2024-0105", "Colissimo", "6A1111111111", "2024-04-20", "2024-04-21"),
        ("sh-0110", "O-2024-0110", "Chronopost", "XY9876543210", "2024-04-15", "2024-04-16"),
        ("sh-0112", "O-2024-0112", "Colissimo", "6A2222222222", "2024-04-10", "2024-04-12"),
        ("sh-0118", "O-2024-0118", "Mondial Relay", "MR5555555555", "2024-03-30", "2024-04-01"),
        ("sh-0120", "O-2024-0120", "Chronopost", "XY1212121212", "2024-05-08", None),
        ("sh-0124", "O-2024-0124", "Colissimo", "6A3333333333", "2024-05-09", None),
        ("sh-0128", "O-2024-0128", "Colissimo", "6A4444444444", "2024-04-25", "2024-04-27"),
    ]
    return [
        Shipment(
            id=i,
            order_id=o,
            carrier=ca,
            tracking_number=tn,
            estimated_delivery=ed,
            actual_delivery=ad,
        )
        for i, o, ca, tn, ed, ad in rows
    ]


def _returns() -> list[Return]:
    rows = [
        ("rt-0110", "O-2024-0110", "Erreur de taille", RTS.requested),
        ("rt-0112", "O-2024-0112", "Flocage abîmé", RTS.accepted),
        ("rt-0118", "O-2024-0118", "Ne convient pas", RTS.refunded),
        ("rt-0128", "O-2024-0128", "Changement d'avis", RTS.requested),
        ("rt-0105", "O-2024-0105", "Taille trop petite", RTS.requested),
        ("rt-0124", "O-2024-0124", "Défaut sur la maille", RTS.requested),
    ]
    return [Return(id=i, order_id=o, reason=r, status=s, opened_at=_DT) for i, o, r, s in rows]


def _refunds() -> list[Refund]:
    rows = [
        ("rf-0118", "O-2024-0118", 170, "Retour accepté", RS.approved),
        ("rf-0112", "O-2024-0112", 40, "Geste commercial flocage", RS.auto),
        ("rf-0110", "O-2024-0110", 250, "Litige montant élevé", RS.escalated),
    ]
    return [
        Refund(id=i, order_id=o, amount=a, reason=r, status=s, requested_at=_DT)
        for i, o, a, r, s in rows
    ]


def _escalations() -> list[Escalation]:
    rows = [
        ("esc-001", "C-sophie-martin", "O-2024-0110", "Litige authenticité maillot Brésil", None),
        ("esc-002", "C-karim-benali", "O-2024-0112", "Client mécontent flocage abîmé", None),
        ("esc-003", "C-marc-dubois", "O-2024-0103", "Expédié mais demande annulation", None),
        ("esc-004", "C-thomas-petit", None, "Demande revendeur volume", None),
    ]
    return [
        Escalation(id=i, customer_id=c, order_id=o, reason=r, opened_at=_DT, resolved_at=rv)
        for i, c, o, r, rv in rows
    ]


def seed(session: Session) -> None:
    """Insère le jeu de données de référence dans la session fournie."""
    for batch in (
        _customers(),
        _products(),
        _variants(),
        _orders(),
        _order_items(),
        _shipments(),
        _returns(),
        _refunds(),
        _escalations(),
    ):
        session.add_all(batch)
        session.flush()
    session.commit()

"""Motifs d'entités métier partagés entre l'extraction (`extractor.py`) et la
persistance (`db.py`, canonicalisation des clés de fait).
"""

from __future__ import annotations

import re

ORDER_RE = re.compile(r"O-\d{4}-\d{4}")
CONTRACT_RE = re.compile(r"C-\d+")

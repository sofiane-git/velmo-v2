"""Budgets de timeout partagés par les appels réseau des garde-fous (étages
2/3 : classifier.py, judge.py, pipeline.py). Module neutre — aucun des trois
n'a besoin d'importer les deux autres pour lire ces constantes.
"""

from __future__ import annotations

# 30s (pas 8s) : mesuré en conditions réelles (docker, CPU) — un premier appel
# à Llama Guard 3 8B après démarrage du conteneur Ollama (chargement du modèle
# en mémoire) prend 18-27s, largement au-delà des 8s précédents. Un timeout
# trop court ici ne fait pas que "perdre le signal LLM" : `CombinedClassifier`
# calcule le repli lexical (instantané) dans la MÊME fonction synchrone que
# l'appel Llama Guard, donc `Future.result(timeout=...)` abandonne aussi ce
# repli déjà calculé — un message pourtant détecté par le lexique (ex.
# "frapper") passe alors en "allow" au lieu d'être bloqué. Les appels suivants
# restent rapides (~0.2-0.5s, modèle résident en mémoire) : ce budget élargi
# ne coûte donc qu'au tout premier appel après démarrage. Consommé par
# `pipeline.py` (`Future.result(timeout=CALL_TIMEOUT_S)`).
CALL_TIMEOUT_S = 30.0

# Budget client (LlamaGuardClassifier, AzureJudge) strictement < CALL_TIMEOUT_S :
# `Future.result(timeout=CALL_TIMEOUT_S)` abandonne l'attente sans tuer le
# thread sous-jacent — si le client HTTP n'a pas SON PROPRE timeout plus
# court, un appel lent y reste bloqué indéfiniment et réduit la capacité du
# pool partagé (`pipeline._EXECUTOR`) pour tous les appels suivants. Dérivé
# d'une seule constante pour qu'un futur changement de CALL_TIMEOUT_S ne
# désynchronise pas silencieusement les timeouts clients.
CLIENT_TIMEOUT_S = CALL_TIMEOUT_S - 5.0

# Velmo V2

Assistant de support pour **Velmo**, boutique en ligne de maillots de foot collector (rééditions vintage, pièces signées, éditions limitées en stock très limité). L'agent traite la gestion de commandes de niveau 1 — statut et suivi, disponibilité, modification/annulation avant expédition, retours, remboursements simples, FAQ — en gardant le contexte du client dans le temps.

## Features

- Outils métier connectés à la base : commandes, suivi, stock, retours, remboursements, escalade
- Garde-fous métier intégrés : isolation par client, blocage des modifications après expédition, plafond de remboursement (50 €) avec escalade
- FAQ par recherche sémantique (RAG) sur la base de connaissances Velmo
- Mémoire durable et isolée par client (court + long terme, droit à l'oubli)
- Garde-fous de contenu en entrée/sortie (modération, injection, PII, périmètre)
- Chaîne qualité MLOps : évaluation, note globale, seuil bloquant en CI

## Stack

- Python 3.11 (géré avec `uv`)
- PostgreSQL + SQLAlchemy 2 + Alembic (état des commandes, clients, catalogue)
- PostgreSQL + pgvector pour la mémoire épisodique ; Chroma + `intfloat/multilingual-e5-small` pour la FAQ (extra `vector`)
- Agent : Azure AI Inference (**Mistral-Large-3**) ; juge garde-fous : Azure OpenAI (**gpt-5-mini**) ; extracteur mémoire : **claude-opus-4-5** via Azure AI Foundry ; classifieur modération : Llama Guard 3 via Ollama (extras `llm`/`guardrails`)
- GitHub Actions pour l'intégration continue (ruff, mypy strict, import-linter, acceptance, gate qualité)

Le coeur tourne sans service externe (repli hors-ligne : SQLite en mémoire pour les
tests, FAQ locale, LLM en écho). Les intégrations s'activent via les extras :

```bash
uv sync                                   # coeur + base + outils de dev
uv sync --extra vector --extra llm        # Chroma + Azure AI Inference
```

## Démarrage

```bash
cp .env.example .env   # renseigner les clés Azure/Ollama (ou laisser vide : repli hors-ligne en dev)
make up           # docker compose : app + postgres + chroma + ollama
make migrate      # alembic upgrade head
make seed         # peuple Postgres (catalogue, clients, ~14 commandes)
make chat         # REPL — répond aux questions métier, avec mémoire + garde-fous
```

Sans `.env` renseigné, `ENVIRONMENT=development` : l'agent tourne en repli
hors-ligne (LLM en écho, juge déterministe, classifieur lexical). En
production (`ENVIRONMENT=production`), une config LLM ou un Postgres manquant
fait échouer le démarrage plutôt que de dégrader silencieusement.

Exemple de session (`make chat`, client `C-marc-dubois` par défaut) :

```
Vous : Quel est le statut de ma commande O-2024-0101 ?
Velmo : Votre commande O-2024-0101 est au statut « prepared ».
Vous : Le maillot france-1998 en taille L est-il disponible ?
Velmo : Le maillot France 1998 — Zidane en taille L est disponible.
Vous : Quels sont les frais de port en France ?
Velmo : D'après notre FAQ (frais-de-port.md) : France métropolitaine : 6,90 € …
```

L'agent combine base métier, FAQ (RAG), **mémoire durable isolée par client**,
**garde-fous de contenu entrée/sortie** et une **chaîne qualité MLOps** dont le
gate bloque la CI sous le seuil de qualité.

## Layout

```
src/velmo/
  cli.py            REPL de conversation (--user)
  agent.py          Orchestration : garde-fous → mémoire → outils → réponse
  llm.py            Client Azure AI Inference (+ repli hors-ligne)
  db.py             Schéma SQLAlchemy + sessions
  sampledata.py     Jeu de données de référence
  tools/            10 outils métier (accès Postgres + FAQ)
  memory/           Mémoire court + long terme, isolation, droit à l'oubli
  guardrails/       Garde-fous de contenu entrée/sortie (pipeline I/O)
  mlops/            Évaluation, note globale, seuil bloquant, versioning, rapport
docs/reco_expert.md Note de recommandations (stack + exigences)
kb/docs/            Base de connaissances FAQ
scripts/            seed.py (Postgres) + seed_kb.py (Chroma)
alembic/            Migrations
eval/               Jeux de cas (mémoire, garde-fous, qualité)
tests/acceptance/   Suite d'acceptance + tests métier
.github/workflows/  Intégration continue
```

## Commandes utiles

```bash
make migrate    # alembic upgrade head
make seed-kb    # ingestion FAQ dans Chroma
make test       # suite d'acceptance + tests métier
make fmt        # ruff format + autofix
make typecheck  # mypy
make down       # arrête les services
```

## License

Propriétaire — Velmo.

# Référentiel d'audit velmo-v2 — 10 dimensions

**Date :** 2026-07-22 · **Rôle :** grille de vérification concrète (Phase 0). Chaque item = vérifiable ; cite un standard/source + un « comment vérifier ». Réutilisable tel quel comme logique de vérification du futur plugin Claude Code.

**Stack imposée (`reco_expert.md`) — rappel de contexte :** LLM via Azure AI Inference (Kimi-K2.6, aucun modèle local) · Postgres (source de vérité faits durables) · Chroma (mémoire épisodique vectorielle) · GitHub Actions avec blocage de livraison sous seuil qualité.
**R1-R6 (cahier des charges) :** R1 longue conversation · R2 persistance multi-session · R3 isolation par `user_id` · R4 tenue fenêtre de contexte · R5 droit à l'oubli · R6 traçabilité.

---

## D1 — Cohérence inter-docs
Ancrage : cohérence documentaire (single source of truth), conception agnostic = généralisation de la conception velmo.

| Item | Source | Comment vérifier |
|------|--------|------------------|
| Noms de modèles identiques partout (agent, juge, extracteur, classifieur) | SSOT | Comparer valeurs modèle dans `schemas/04-outils.md`, `conceptions/*`, `conceptions/agnostic/*` — 0 divergence |
| Seuils chiffrés (gate, confidence, TTL) cohérents entre schémas et conceptions | SSOT | Relever chaque seuil et sa valeur ; croiser docs |
| Flux/architecture décrits identiquement (schemas ↔ conceptions) | SSOT | Comparer diagrammes/étapes de flux mémoire, guardrails, gate |
| Conception agnostic = surensemble tool-neutral de la conception velmo (pas de contradiction, juste abstraction) | design | Lire paire agnostic/velmo par chantier ; vérifier que velmo instancie l'agnostic sans le contredire |
| Terminologie stable (mêmes noms de composants, tables, catégories G1-G7) | SSOT | grep des noms clés dans tous les docs |
| Décisions actées non contredites d'un doc à l'autre (ex. LangGraph, fail-closed/open) | SSOT | Repérer chaque décision et sa reprise ailleurs |

## D2 — Cohérence code↔doc
Ancrage : la doc doit décrire le code réel (divergence = finding, fix bidirectionnel, best-practice l'emporte).

| Item | Source | Comment vérifier |
|------|--------|------------------|
| Modules décrits existent dans `src/velmo/` avec le rôle annoncé | code↔doc | Croiser conceptions/schemas ↔ arbo réelle `src/velmo/` |
| Décisions « cibles » actées en doc sont soit implémentées soit marquées non-implémentées | code↔doc | Pour chaque décision (LangGraph/PostgresSaver, tombstone, cross-check user_id…) : présente dans le code ? |
| Modèles annoncés = modèles réellement configurés | code↔doc | Comparer docs ↔ `src/velmo/llm.py`, `config.py`, `.env.example` |
| Flux mémoire décrit (court/long terme, extraction, rétention) = flux codé | code↔doc | Croiser `conception_chantier1` ↔ `src/velmo/memory/*` |
| Pipeline guardrails décrit (ordre I/O, catégories, repli) = pipeline codé | code↔doc | Croiser `conception_chantier2` ↔ `src/velmo/guardrails/pipeline.py` |
| Gate/éval décrit (dimensions, min, versioning) = code MLOps | code↔doc | Croiser `conception_chantier3` ↔ `src/velmo/mlops/*` |

## D3 — Architecture & best-practices
Ancrage : src-layout (PyPA), séparation des responsabilités, isolation par contrats, patterns agent, observabilité hors chemin critique.

| Item | Source | Comment vérifier |
|------|--------|------------------|
| Séparation claire des responsabilités (memory / guardrails / mlops / tools) | SoC | Vérifier découpage modules ↔ frontières décrites |
| Isolation forcée par contrat (import-linter : `db.py` non contournable) | contracts | Lire contrat `[tool.importlinter]` dans `pyproject.toml` ; cohérence avec le design d'isolation |
| Observabilité (Langfuse) hors chemin de gate, repli NullSink sans config | resilience | Vérifier que la trace n'est pas sur le chemin critique + repli documenté |
| Garde-fous en pipeline explicite I/O avec matrice de repli | design | Vérifier fail-closed/fail-open par catégorie décrit + justifié |
| Orchestration agent (LangGraph + checkpointer) cohérente avec persistance thread | design | Vérifier pattern décrit vs LangChain 1.x (classes memory supprimées) |
| Chaque composant : rôle unique, interface définie, testable isolément | modularity | Lire schémas/conceptions ; repérer composants fourre-tout |

## D4 — Sécurité applicative / LLM
Ancrage : **OWASP Top 10 for LLM Applications** (LLM01 prompt injection, LLM02 insecure output handling, LLM06 sensitive information disclosure).

| Item | Source | Comment vérifier |
|------|--------|------------------|
| Contrôle en **entrée** (prompt injection / jailbreak) — Prompt Shields | LLM01 | Vérifier présence + activation dans pipeline (`conception_chantier2`, `guardrails/prompt_shields.py`) |
| Contrôle en **sortie** (insecure output handling) — filtrage réponses | LLM02 | Vérifier étape output du pipeline + catégories couvertes |
| Redaction PII (entrée et sortie) — non-fuite d'infos sensibles | LLM06 | Vérifier `guardrails/pii_redaction.py` + où elle s'applique |
| Matrice de repli par catégorie : fail-closed sur catégories critiques | secure default | Vérifier G1/G2/G3/G6 fail-closed, G4/G5/G7 fail-open loggé (ou équivalent) |
| Aucune des catégories interdites ne passe dans un sens comme dans l'autre | reco §2 | Vérifier symétrie I/O de la couverture |
| Décisions de blocage journalisées (observabilité guardrails) | reco principes | Vérifier journalisation décision de blocage |

## D5 — Sécurité CI/CD & supply-chain
Ancrage : **GitHub Actions security hardening** + supply-chain (pinning, lockfile).

| Item | Source | Comment vérifier |
|------|--------|------------------|
| `permissions:` déclaré (workflow ou job), au minimum requis | GH hardening | grep `permissions:` dans chaque `.github/workflows/*.yml` ; défaut trop large = finding |
| Actions tierces pinnées par **SHA** (pas `@v4`/`@main`) | GH hardening | Inspecter chaque `uses:` |
| Pas d'interpolation `${{ github.* }}` non fiable directement dans `run:` (script injection) | GH hardening | Inspecter les blocs `run:` |
| Secrets via OIDC / `secrets.*`, **jamais en clair** dans workflow/deploy/doc | GH hardening | grep tokens/clés/mots de passe dans `.yml`, `deploy/`, `.env.example` |
| **Lockfile commité** (`uv.lock` suivi par git) pour builds déterministes | supply-chain | `git check-ignore uv.lock` doit échouer (= non ignoré) |
| Deps applicatives bornées (min/max) | supply-chain | Lire `pyproject.toml` dependencies |
| `.env` (réel) jamais commité ; `.env.example` sans valeur secrète | secrets hygiene | `git check-ignore .env` ; inspecter `.env.example` |

## D6 — Reproductibilité dev→prod
Ancrage : **Docker/OCI best practices** + **12-factor** + migrations versionnées.

| Item | Source | Comment vérifier |
|------|--------|------------------|
| Dockerfile **multi-stage** (build vs runtime) | Docker BP | Lire `Dockerfile` |
| Conteneur en **USER non-root** | Docker BP | Chercher directive `USER` |
| Image de base **pinnée** (tag précis ou digest, pas `latest`) | Docker BP | Lire `FROM` |
| `.dockerignore` présent (contexte de build maîtrisé) | Docker BP | Vérifier existence + contenu |
| `uv sync` déterministe basé sur lockfile commité | reproducibility | Croiser Dockerfile ↔ `.gitignore` (uv.lock) |
| Config via variables d'env, aucune config secrète en dur | 12-factor | Lire `Dockerfile`, `docker-compose.yml`, `config.py` |
| Healthcheck défini (compose et/ou Dockerfile) | ops | grep `healthcheck` |
| Chaîne de migrations Alembic linéaire et rejouable « from scratch » | migrations | Lire `alembic/versions` (chaîne down_revision) |
| Parité dev/prod (compose ≈ image déployée) | 12-factor | Comparer `docker-compose.yml` ↔ `deploy/` |

## D7 — Qualité tutos (suivables à la lettre)
Ancrage : reproductibilité procédurale (idempotence, prérequis explicites, correspondance tuto↔artefact réel).

| Item | Source | Comment vérifier |
|------|--------|------------------|
| Prérequis explicites en tête (outils, versions, accès Azure/GitHub) | doc BP | Lire l'intro de chaque tuto |
| Commandes exactes et copiables (pas de pseudo-commande) | doc BP | Inspecter chaque bloc commande |
| Ordre des étapes exécutable sans saut ni dépendance implicite | doc BP | Suivre mentalement la séquence de bout en bout |
| Correspondance tuto ↔ **workflow/Dockerfile/deploy réels** (noms de jobs, secrets, variables, chemins) | code↔doc | Croiser chaque référence du tuto avec le fichier réel |
| Aucun placeholder non résolu (`<...>`, `TODO`, valeur d'exemple laissée) | doc BP | grep placeholders |
| Idempotence / reprise (que se passe-t-il si on relance une étape) | ops | Vérifier mentions de ré-exécution |
| Vérification finale (comment confirmer que ça marche) | doc BP | Chercher étape de validation en fin de tuto |

## D8 — MLOps / éval + hygiène repo
Ancrage : MLOps maturity (gate, versioning, drift, cadence) + hygiène de dépôt.

| Item | Source | Comment vérifier |
|------|--------|------------------|
| Gate qualité bloquant en CI sur `min(dimensions)` (pas de moyenne masquante) | reco §3 | Lire `schemas/03`, `conception_chantier3`, `workflows/quality.yml` |
| Non-régression prouvée par version (suites d'éval) | reco §3 | Vérifier suites + exécution en CI |
| Versioning par **hash git** (traçabilité version évaluée) | MLOps | Croiser `conception_chantier3` ↔ `src/velmo/mlops/versioning.py` |
| Détection de dérive (drift) planifiée (nightly) | MLOps | Lire `workflows/nightly.yml` + `mlops/drift_check.py` |
| Seuils chiffrés **avec provenance** (pas de nombre magique) | rigor | Vérifier que chaque seuil doc cite sa justification |
| « Sans bloquer pour du bruit » : modèle de bruit statistique explicité | reco principes | Chercher gestion du bruit / marge |
| README + CLAUDE.md à jour et cohérents avec le repo | repo hygiene | Lire ; croiser avec commandes/structure réelles |
| Aucun fichier parasite commité (données, artefacts, caches) | repo hygiene | Vérifier arbo git-suivie ↔ `.gitignore` |

## D9 — RGPD / rétention / gouvernance données
Ancrage : **RGPD** (art. 5 minimisation & limitation de conservation, art. 17 droit à l'effacement, art. 25 privacy by design/default, art. 28 sous-traitants, art. 30 registre) + R3/R5/R6.

| Item | Source | Comment vérifier |
|------|--------|------------------|
| **Droit à l'oubli** effectif : effacement + anti-résurrection (tombstone) | R5 / art. 17 | Croiser `conception_chantier1` ↔ `memory/retention.py`, `memory/db.py` |
| **Isolation par `user_id`** : aucune fuite mémoire inter-utilisateurs | R3 / art. 25 | Vérifier filtrage user_id systématique (mémoire + requêtes vectorielles) |
| Limitation de conservation : **TTL** épisodes / purge documentée | art. 5 | Vérifier TTL + mécanisme de purge |
| Minimisation : on ne retient que le nécessaire ; extraction cadrée | art. 5 | Lire `memory/extractor.py` + design d'extraction |
| **PII hors traces d'observabilité** (Langfuse ne reçoit pas de PII brute) | art. 5/25 | Vérifier redaction avant trace dans `mlops/observability.py` |
| **Traçabilité** des écritures mémoire (inspecter ce qui a été retenu) | R6 | Vérifier journal/inspection des écritures |
| Sous-traitants identifiés (Azure, hébergement) + localisation UE | art. 28 | Chercher mention hébergement/traitement des données |
| Export / portabilité des données utilisateur | art. 20 | Chercher mécanisme d'export |
| Base légale / consentement mentionné pour la mémoire persistante | art. 6 | Chercher mention dans conception/doc |

## D10 — Fondations « nouvel agent ultra solide » (bootstrap-readiness)
Ancrage : synthèse transversale — « ce repo tel quel = squelette de départ parfait pour un projet d'agent ? » (= mode *nouveau projet* du plugin).

| Item | Source | Comment vérifier |
|------|--------|------------------|
| **src-layout** + séparation des responsabilités par module | PyPA | Vérifier `src/velmo/` + `pyproject` packaging |
| Config **typée et validée** (pydantic-settings) + `.env.example` **exhaustif** | 12-factor | Lire `config.py` + comparer clés `.env.example` ↔ clés lues |
| **Quality gates dès j0** : mypy strict, ruff, import-linter, CI bloquante | shift-left | Lire `pyproject.toml` + `workflows/quality.yml` |
| Observabilité **et** éval intégrées d'entrée (pas ajoutées après) | design | Vérifier présence native (mlops + observability) |
| Guardrails I/O **fail-closed par défaut** | secure default | Vérifier posture par défaut du pipeline |
| Mémoire : rétention + isolation **by design** (pas en option) | privacy by design | Croiser design mémoire ↔ code |
| Reproductibilité : lockfile commité + Docker + migrations versionnées | reproducibility | Croiser D5/D6 |
| Docs conception + schémas + tutos = artefacts de **1re classe** (présents, à jour) | docs-as-code | Vérifier existence + fraîcheur |
| Onboarding : README + CLAUDE.md + Makefile utilisables tels quels | DX | Suivre le README/Makefile mentalement |
| Secure defaults partout (pas de secret par défaut, pas de debug on) | security | Balayer configs |

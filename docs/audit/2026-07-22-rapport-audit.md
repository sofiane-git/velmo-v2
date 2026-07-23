# Rapport d'audit velmo-v2 — 2026-07-22

**Charte :** audit 10 dimensions (D1-D10) sur docs cœur + chaîne dev→prod + transversal, selon `docs/audit/2026-07-22-referentiel-audit.md`. Code `src/velmo/` utilisé en lecture-preuve. 10 agents read-only en parallèle, synthèse dédupliquée.

**Volume :** 101 findings bruts → 98 après dédup automatique → **95 causes racines** après regroupement manuel de 2 doublons inter-dimensions (uv.lock : D5-01+D6-01+D10-01 ; oubli↔checkpoints : D2-01+D9-01).

## Synthèse

| Dim | Intitulé | Bloquant | Majeur | Mineur | Nit | Total |
|-----|----------|:-:|:-:|:-:|:-:|:-:|
| D1 | Cohérence inter-docs | 0 | 1 | 2 | 0 | 3 |
| D2 | Cohérence code↔doc | 1 | 4 | 6 | 2 | 13 |
| D3 | Architecture | 0 | 2 | 3 | 1 | 6 |
| D4 | Sécu applicative/LLM | 0 | 3 | 2 | 2 | 7 |
| D5 | Sécu CI/CD supply-chain | 0 | 3 | 3 | 0 | 6 |
| D6 | Reproductibilité dev→prod | 1 | 3 | 5 | 1 | 10 |
| D7 | Qualité tutos | 1 | 10 | 8 | 2 | 21 |
| D8 | MLOps/éval + hygiène | 2 | 6 | 4 | 1 | 13 |
| D9 | RGPD/rétention | 1 | 4 | 5 | 0 | 10 |
| D10 | Bootstrap-readiness | 2 | 2 | 4 | 1 | 9 |
| **Σ** | | **8** | **38** | **42** | **10** | **98** |

### Top priorités (à corriger en premier)

1. **[RGPD] Droit à l'oubli incomplet** — `forget_all` ne purge pas les checkpoints LangGraph (verbatim conversationnel conservé à vie) **et** les tombstones anti-résurrection sont détruits par la cascade FK dans la même transaction (B1, B2).
2. **[RGPD] PII brute écrite en mémoire et envoyée à Langfuse** — chemin autorisé : message brut persisté (pas `filtered_text`) ; masque Langfuse ne couvre pas les PII texte libre (M-D9-03, M-D9-06).
3. **[Supply-chain] `uv.lock` gitignoré** — builds CI/Docker non déterministes, `COPY uv.lock` casse sur clone frais (B3).
4. **[MLOps] Le gate CI évalue un stub** — aucun secret LLM ni DB dans quality.yml : l'agent gaté tourne en repli hors-ligne, et la non-régression M4 est structurellement morte (SQLite éphémère → baseline toujours vide) (B4, B5).
5. **[CI] mypy strict + ruff configurés mais jamais exécutés en CI** (B6).
6. **[Tuto] Tuto Azure non suivable tel quel** — commande sur une app jamais créée (B7) + 10 écarts majeurs tuto↔réel (noms de déploiements, variables, RBAC vs access-policies…).
7. **[Guardrails] Matrice fail-closed neutralisable** — panne du seul juge → G5/G6/G7 sautés silencieusement ; JSON malformé du juge → verdict « sain » (M-D4-01, M-D4-02).

---

## Findings — BLOQUANT (8, regroupés en 6 causes racines)

### ✅ B1 · Droit à l'oubli : checkpoints LangGraph non purgés — `D2-01` + `D9-01` *(corrigé : e101bc3, 13fc0bd)*
- **Fichier :** `src/velmo/memory/__init__.py:550-591`
- **Constat :** `forget_all` (R5/art. 17) supprime faits/procédures/épisodes/user mais n'appelle jamais `checkpointer.delete_thread` — le verbatim conversationnel survit dans les tables de checkpoints. Pire : les lignes `Thread` étant supprimées, ces checkpoints deviennent orphelins que `purge_inactive_threads` (qui itère sur `Thread`) ne retrouvera jamais → conservation illimitée de données « effacées ».
- **Preuve :** conception_chantier1:270-274 exige « + purge des checkpoints du thread (PostgresSaver.delete_thread) — une seule transaction » ; `grep delete_thread src/velmo` → seul `retention.py:56,61` (purge TTL).
- **Fix :** code · **Plugin :** oui — l'oubli doit couvrir TOUS les stores, y compris l'état géré par le framework (checkpoints, historiques de session).
- **Reco :** collecter les `thread_id` avant le DELETE cascade, appeler `delete_thread(tid)` pour chacun ; test d'acceptance « aucun résidu dans les checkpoints après effacement total ».

### ✅ B2 · Tombstones détruits par la cascade FK dans `forget_all` — `D9-02` *(corrigé : e101bc3 — tombstones posés après recréation user ; + tombstone `fact_value` en c525403)*
- **Fichier :** `src/velmo/memory/__init__.py:579-588` + `memory/db.py:129`
- **Constat :** les tombstones sont posés PUIS emportés par `session.delete(user)` (FK `ondelete=CASCADE` sur `memory_tombstone.user_id`) — après effacement total, une extraction LLM différée (`background=True`, chemin par défaut) peut réécrire les faits oubliés. L'anti-résurrection est inopérante précisément pour l'effacement total.
- **Preuve :** conception_chantier1:286-288 « un tombstone que l'extracteur consulte avant tout write ».
- **Fix :** code · **Plugin :** oui — un marqueur d'effacement doit survivre à la suppression qu'il protège (pas de FK CASCADE vers l'entité effacée).
- **Reco :** poser les tombstones après le `get_or_create_user` de recréation (ou exclure la table de la cascade) + test « forget_all puis extraction différée ne réécrit rien ».

### ✅ B3 · `uv.lock` gitignoré : builds non déterministes — `D5-01` + `D6-01` + `D10-01` *(corrigé : 9b170eb — lockfile commité, `--locked` dans les 4 workflows + Dockerfile)*
- **Fichier :** `.gitignore:21`
- **Constat :** `uv.lock` ignoré (`git check-ignore` confirme) alors que le Dockerfile fait `COPY pyproject.toml uv.lock ./` (casse sur clone frais) et que les 4 workflows font `uv sync` (re-résolution à chaque run : supply-chain + non-reproductibilité). `pyproject.toml:129-130` suppose pourtant l'inverse (« le wheel CPU est fixé dans uv.lock »).
- **Fix :** infra · **Plugin :** oui — check : lockfile commité obligatoire ; `git check-ignore <lockfile>` doit échouer ; croiser avec `COPY` du Dockerfile.
- **Reco :** retirer du .gitignore, commiter, passer CI et Dockerfile à `uv sync --locked`/`--frozen`.

### ✅ B4 · Le gate qualité CI évalue un stub hors-ligne — `D8-01` *(corrigé : aea8c43 — gate hybride : PR = dégradé déterministe documenté ; release + nightly = vrai modèle avec secrets + garde anti-silence)*
- **Fichier :** `.github/workflows/quality.yml:26-30` (idem release.yml)
- **Constat :** aucun `env:`/`secrets`/`services:` dans quality.yml → l'agent évalué par le gate tourne en repli hors-ligne (EchoLLM, SQLite) : le gate ne mesure jamais la config LLM réelle qu'il est censé garder.
- **Preuve :** `runner.py:60-72` construit l'agent depuis `get_settings()` (lues dans l'env) ; README:22 « le cœur tourne sans service externe (repli hors-ligne) ».
- **Fix :** infra · **Plugin :** oui — un gate d'éval LLM doit injecter les credentials du modèle réellement déployé, sinon il évalue un stub.
- **Reco :** injecter secrets Azure + URL Postgres dans les jobs gate, ou documenter explicitement que le gate CI évalue le mode dégradé et pourquoi c'est acceptable.

### ✅ B5 · Non-régression M4 structurellement morte en CI — `D8-02` *(corrigé : aea8c43 — DB_URL secret vers Postgres partagé dans release + nightly ; garde anti-silence si absent. NB : le code résout `DB_URL`, pas « MLOPS_DATABASE_URL » cité au constat)*
- **Fichier :** `.github/workflows/quality.yml:30`
- **Constat :** sans `MLOPS_DATABASE_URL`, le runner retombe sur un SQLite recréé à chaque run éphémère → `_fetch_previous_quality_scores` renvoie toujours `[]` → chaque run CI est un « 1er run » où la régression ne peut pas échouer.
- **Preuve :** `mlops/db.py:120-124` (repli SQLite) ; `mlops/__init__.py:261-266` (« Sans baseline… la dimension ne peut pas encore échouer »).
- **Fix :** infra · **Plugin :** oui — un gate de non-régression doit prouver que sa baseline persiste entre deux runs (DB externe, cache, artefact).
- **Reco :** base mlops persistante pour le gate CI + step qui échoue si baseline vide alors qu'un run précédent existe.

### ✅ B6 · mypy strict + ruff jamais exécutés en CI — `D10-02` *(corrigé : c2e2447 — steps ruff check/format + mypy dans quality.yml, sync --all-extras pour un env mypy stable ; baseline format 1465160)*
- **Fichier :** `.github/workflows/quality.yml:11`
- **Constat :** `[tool.mypy] strict=true` et Ruff configurés dans pyproject, mais `grep mypy|ruff .github/workflows/` → rien. Les gates annoncées ne bloquent pas.
- **Fix :** infra · **Plugin :** oui — vérifier la correspondance outils-configurés ↔ outils-gatés en CI ; poser ces steps dès j0.
- **Reco :** ajouter `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` à quality.yml.

### ✅ B7 (lot 6) · Tuto Azure : étape sur une ressource jamais créée — `D7-01`
- **Fichier :** `docs/tutorials/tuto_azure_deploiement.md:468`
- **Constat :** `az webapp identity assign --name "<nom-app>"` alors que le tuto ne crée jamais d'App Service/Container App (hébergement explicitement non tranché, cf. release.yml:38-43). Commande inexécutable dans le flux nominal.
- **Fix :** doc · **Plugin :** oui — vérifier le graphe de dépendances des étapes d'un tuto (chaque commande opère sur une ressource créée en amont).
- **Reco :** section conditionnelle « une fois l'hébergement choisi » ou renvoi au tuto release §4.4.

---

## Findings — MAJEUR (38)

### Docs & cohérence (D1, D2)

- ✅ **`D1-01`** (lot 6) (+`D2-03`) · `docs/reference/schemas/04-outils.md:83` — Store vectoriel contradictoire : schémas actent **ChromaDB** (pgvector écarté), conceptions ch1 actent **pgvector** (ChromaDB écarté), avec conséquences opposées sur l'atomicité R5. **Fix :** doc. **Reco :** trancher (pgvector, plus récent et mieux argumenté : atomicité R5, store unique) et aligner schemas/04 (l.13, 83, 115, 118) + schemas/01 (l.21, 62, 64) ; si Chroma « imposé par le brief », documenter l'écart assumé.
- ✅ **`D2-02`** (lot 6) · `src/velmo/guardrails/judge.py:364-377` — Repli runtime « RuleBasedJudge si Azure indisponible » non branché : `ShadowingJudge.evaluate` propage l'exception sans basculer ; panne partielle du juge → G5/G6 non évalués. **Fix :** code. **Reco :** retourner le verdict du shadow quand le primary lève (loggé `method='fallback'`), ou appliquer la ligne fail-closed dès l'échec du juge seul.
- ✅ **`D2-04`** *(corrigé : lot 7 — create_all guardé SQLite-only sur les 4 sites Postgres-capables ; couplé D7-17 : `alembic upgrade head` câblé dans release/nightly avec stamp-once des bases legacy ; complétude migrations↔modèles prouvée sur Postgres frais)* · `src/velmo/memory/db.py:202` — Doc : « jamais create_all » ; code : `Base.metadata.create_all` inconditionnel dans 5 modules, y compris sur Postgres (concurrent d'Alembic). **Fix :** code. **Reco :** garde explicite (create_all réservé SQLite offline/tests), Alembic seul sur Postgres.
- ✅ **`D2-05`** (lot 6) · `conception_chantier3_evaluation_mlops.md:124-125` — Append-only « forcé côté base » (INSERT/SELECT only) sans aucune trace d'implémentation (`grep GRANT alembic` vide). **Fix :** infra. **Reco :** migration posant les GRANT/REVOKE, ou marquer non-implémenté.
- ✅ **`D2-06`** (lot 6) (+`D3-02`) · `conception_chantier1_memoire.md:17` — Doc : orchestration du tour = StateGraph LangGraph (dont nœud résumé R4) ; code : LangGraph = persistance seule (StateGraph à un unique nœud `append_turn`), compression et orchestration en Python simple. **Fix :** doc. **Reco :** reformuler le périmètre réel du framework.

### Architecture (D3)

- **`D3-01`** · `src/velmo/api.py:22` — L'observabilité vit dans `velmo.mlops` : le runtime de service importe le package d'éval pour tracer, inversant la direction documentée « mlops → agent, jamais l'inverse ». **Fix :** code. **Reco :** extraire vers `velmo/observability.py` + contrat import-linter interdisant api/agent → mlops.
- ✅ **`D3-03`** *(corrigé : 03eb5e0 — `Settings.allow_sqlite_fallback` + `require_durable_store` sur les 5 sites de repli)* · `src/velmo/guardrails/db.py:89` — Repli silencieux des stores durables : Postgres injoignable → SQLite local automatique (audit garde-fous, mémoire, checkpoints) avec simple warning, sans profil prod fail-fast → fragmentation silencieuse en prod multi-instance. **Fix :** les-deux. **Reco :** `Settings.allow_sqlite_fallback` (défaut false en prod) + étendre la matrice de repli aux stores.

### Sécurité applicative/LLM (D4)

- ✅ **`D4-01`** *(corrigé : f40051c — repli par étage via `_fallback_hits`/`CATEGORY_STAGES`, statut ok/failed/absent par source)* · `src/velmo/guardrails/pipeline.py:186` — Le repli fail-closed ne se déclenche que si TOUS les étages 2/3 échouent (`any_stage_2_3_responded`) ; le classifieur ne levant quasi jamais (repli lexical interne), une panne du seul juge saute G5/G6-subtil/G7 **silencieusement**. **Fix :** code. **Reco :** repli par étage défaillant (chaque source manquante applique sa ligne de matrice).
- ✅ **`D4-02`** *(corrigé : f40051c — `_parse_verdict` lève `JudgeParseError`, `AzureJudge.evaluate` la propage + log)* · `src/velmo/guardrails/judge.py:298` — JSON malformé du juge → verdict « aucun » partout (fail-closed contourné, aucun log) ; l'injection ciblant le juge peut viser ce downgrade. **Fix :** code. **Reco :** échec de parsing = juge en panne (repli fail-closed) + log warning.
- ✅ **`D4-03`** *(corrigé : f40051c — `Hit.spans` propagés, `pii_redaction.redact_spans` + test d'acceptance)* · `src/velmo/guardrails/pipeline.py:173` — Les spans PII Azure détectés en sortie sont jetés (Hit `filter` sans offsets) ; le masquage réel = regex structurées seules → noms/adresses détectés signalés « filter » mais renvoyés NON masqués au client (LLM06). **Fix :** code. **Reco :** propager les spans jusqu'à la redaction + test d'acceptance.

### CI/CD supply-chain (D5)

- ✅ **`D5-02`** *(corrigé : 9547813 — 13 uses: pinnés par SHA-40 + commentaire version)* · `.github/workflows/quality.yml:12` — Aucune action pinnée par SHA (tags mutables @v4/@v5/@v2 partout, priorité azure/login + setup-uv). **Fix :** code. **Reco :** pin SHA 40 + commentaire version, Dependabot pour maintenir.
- ✅ **`D5-03`** *(corrigé : 9547813 — contents: read partout, write scoppé au job release)* · `.github/workflows/release.yml:1` — quality/release/hotfix sans bloc `permissions:` → GITHUB_TOKEN au défaut du repo (potentiellement write partout). **Fix :** code. **Reco :** `contents: read` au niveau workflow ; `contents: write` scoppé au seul job qui fait `gh release create`.
- *(3e majeur D5 = uv.lock, regroupé en B3.)*

### Reproductibilité (D6)

- ✅ **`D6-02`** (+`D10-04`) *(corrigé : 03eb5e0 — `useradd`+`USER app` uid 10001, vérifié `id` dans l'image)* · `Dockerfile:20` — Pas de directive `USER` : conteneur en root. **Fix :** code. **Reco :** `useradd` + `USER app` avant CMD.
- ✅ **`D6-03`** *(corrigé : 03eb5e0 — `ghcr.io/astral-sh/uv:0.11.28`)* · `Dockerfile:8` — `COPY --from=ghcr.io/astral-sh/uv:latest` : outillage de build non pinné. **Fix :** code. **Reco :** tag précis voire digest.
- ✅ **`D6-04`** *(corrigé : 03eb5e0 — chroma/ollama/postgres pinnés par digest sha256)* · `docker-compose.yml:42` — `chromadb/chroma:latest` et `ollama/ollama:latest` non pinnés (postgres correctement pinné pg16). **Fix :** code. **Reco :** pinner sur les versions validées en test.

### Tutos (D7)

- ✅ **`D7-02`** (lot 6) · `tuto_azure_deploiement.md:159` — Déploiement juge créé `gpt-5-mini-guard` vs `gpt-5-mini` attendu par nightly.yml:53 et .env.example — le drift-check échouerait sur l'infra du tuto. **Fix :** les-deux.
- ✅ **`D7-03`** (lot 6) · `tuto_azure_deploiement.md:243` — `mistral-large-3` (minuscules) + `--model-name Mistral-Large-2411` (= Mistral Large **2**) vs `Mistral-Large-3` attendu (casse comprise) par nightly/.env.example. **Fix :** les-deux.
- ✅ **`D7-04`** (lot 6) · `tuto_azure_deploiement.md:30` — Deux infrastructures jamais réconciliées : tuto = `rg-velmo-prod`/`aoai-velmo-prod-*`, CI réelle = `sconanRG`/`sconanext-*-resource`. **Fix :** les-deux. **Reco :** étape finale « mettre à jour les variables GitHub », ou statut « infra cible future » explicite.
- ✅ **`D7-05`** (lot 6) · `tuto_azure_deploiement.md:273` — Variable `AZURE_AI_INFERENCE_KEY` inexistante (réel : `AZURE_AI_INFERENCE_API_KEY`) → `validate_startup()` échoue si suivi à la lettre. **Fix :** doc.
- ✅ **`D7-06`** (lot 6) · `tuto_azure_deploiement.md:660` — §8 attribue `azure/login` à quality.yml « en variables » — faux deux fois (c'est nightly.yml, via secrets). **Fix :** doc.
- ✅ **`D7-07`** (lot 6) · `tuto_azure_deploiement.md:621` — Chemin CLI OIDC sans `az ad sp create` : le role assignment échoue ; `<app-id>` jamais extrait. **Fix :** doc. **Reco :** aligner sur la séquence correcte du tuto release §2.3.
- ✅ **`D7-08`** (lot 6) · `tuto_azure_deploiement.md:469` — Vault créé en RBAC mais accès accordé via `set-policy` (access policies) : incompatible, la commande échoue (le paragraphe portail du même § dit l'inverse, correctement). **Fix :** doc. **Reco :** `az role assignment create --role "Key Vault Secrets User"`.
- ✅ **`D7-09`** (lot 6) · `tuto_azure_deploiement.md:161` — Placeholder non résolu dans une commande copiable du chemin bloquant (`--model-version "<version pinnée — voir console>"`). **Fix :** doc.
- ✅ **`D7-10`** (lot 6) · `tuto_azure_deploiement.md:516` — `az container exec --exec-command "ollama pull …"` : ACI ne supporte pas les arguments dans exec (limitation documentée Microsoft). **Fix :** doc.
- ✅ **`D7-11`** (lot 6) · `tuto_github_actions_release.md:265` — Approbation par CLI fausse : `-f environment_ids[]=<env_id>` envoie une string (API exige un int → `-F`), chemin elliptique non copiable, `<env_id>` jamais expliqué. **Fix :** doc.

### MLOps/hygiène (D8)

- ✅ **`D8-03`** *(corrigé : ae83702 — table drift_check_run + exit 1 sous plancher)* · `src/velmo/mlops/drift_check.py:103` — Post-drift : print puis `sys.exit(0)` inconditionnel — pas de seuil, pas de persistance, pas d'alerte ; la règle « deux nuits consécutives » documentée est inimplémentable. **Fix :** les-deux.
- ✅ **`D8-04`** *(corrigé : bc0177e — velmo.mlops.alerting, règle deux-nuits, échec du job = canal d'alerte)* · `.github/workflows/nightly.yml:133` — Run bihebdo à `--min-score 0.0` : ne peut jamais être rouge, et l'alerte documentée n'existe dans aucun workflow. **Fix :** les-deux.
- ✅ **`D8-05`** *(corrigé : fec6d48 — Settings.gate_*, gate_config_hash dans l'identité de version, migration 0011)* · `src/velmo/mlops/__init__.py:284` — Seuils de gate en dur et dupliqués (0.80 ×2, plafonds NF, défaut CLI), hors config et hors `compute_version_hashes` — la conception exige « chiffres versionnés dans un fichier de config (donc hashés) ». **Fix :** code.
- ✅ **`D8-06`** *(corrigé : 38a4e8f — git rm + règles .gitignore)* · `eval/Archive.zip` — Artefact zip + copies périmées de fixtures commités (l'intention `.gitignore archives/` existe mais casse différente). **Fix :** infra. **Reco :** `git rm`, l'historique git est l'archive.
- ✅ **`D8-07`** (lot 6) · `README.md:10-12` — README décrit les 3 chantiers « (à construire) » alors qu'ils sont livrés et que le gate bloque déjà la CI. **Fix :** doc.
- ✅ **`D8-08`** (lot 6) · `README.md:19` — README annonce « Kimi-K2.6 » ; config/CI réelles : Mistral-Large-3, claude-opus-4-5, gpt-5-mini. **Fix :** doc.

### RGPD (D9)

- ✅ **`D9-03`** *(corrigé : b1daccd — cas atteignable déjà mitigé par af89c2d (block+redaction en entrée) ; garde symétrique `filtered_text` ajoutée sur le chemin autorisé + test)* · `src/velmo/agent.py:285` — Chemin autorisé : le message BRUT part en mémoire (checkpoints + extracteur LLM cloud) en ignorant `gate_in.filtered_text` — une carte bancaire collée dans un message légitime est persistée et exportée en clair. Contredit conception_chantier2:147 et le contrat de `redact_pii`. **Fix :** code. **Reco :** écrire `filtered_text` quand action='filter' + test Luhn-valide → aucune occurrence en clair.
- **`D9-05`** · `src/velmo/memory/retention.py:7-9` — TTL RGPD (24 mois épisodes, 90 j threads) exécutés par AUCUN mécanisme planifié : `velmo purge` existe en CLI, nightly ne l'appelle pas, le tuto ne crée pas la tâche promise — limitation de conservation théorique. **Fix :** infra. **Reco :** brancher `velmo purge` au nightly + journaliser.
- **`D9-06`** · `src/velmo/mlops/observability.py:34-47` — Masque avant Langfuse Cloud = PII structurées seules ; noms/adresses (exactement ce que la mémoire stocke en facts) partent en clair dans les traces (`input={'message': …}`, payloads memory_read). **Fix :** les-deux. **Reco :** étendre `mask_sensitive_data` au texte libre (réutiliser `pii_redaction.scan`) ou documenter la limite + condition de bascule self-host.
- *(D9-02 reclassé en bloquant, voir B2.)*

### Bootstrap (D10)

- ✅ **`D10-03`** (lot 6) · `README.md:10` — README périmé (features « à construire », Kimi-K2.6, aucune étape `.env` dans le quickstart). **Fix :** doc. *(Recoupe D8-07/D8-08 — une seule réécriture du README règle les trois.)*
- ✅ **`D10-05`** (+`D8-12`) *(corrigé : 8076a45 — web-ci.yml racine avec paths filter + actions pinnées, répertoire mort supprimé)* · `web/.github/workflows/ci.yml` — Workflow CI commité dans `web/.github/` : jamais exécuté par GitHub (seule la racine compte) → le front n'a aucune gate effective, fichier trompeur. **Fix :** infra. **Reco :** job racine avec `paths: [web/**]`, supprimer le répertoire mort.

---

## Findings — MINEUR (42)

| ID | Fichier | Constat (résumé) | Fix | Reco (résumé) |
|----|---------|------------------|-----|---------------|
| ✅ D1-02 (lot 6) | schemas/00-architecture-globale.md:17 | Flux global place l'écriture mémoire dans le chemin critique de réponse ; conception acte best-effort hors chemin | doc | Sortir MEMW du chemin séquentiel (dérivation annotée) |
| ✅ D1-03 (lot 6) | conceptions/agnostic/…chantier3:96 | Docs « agnostic » gardent des résidus Velmo (marque, `velmo.config`) contredisant leur statut de template | doc | Placeholders génériques + velmo en exemple d'instanciation |
| ✅ D2-07 (lot 6) | conception_chantier1:287 | Tombstone décrit « memory_audit + clé bloquée » vs table dédiée `memory_tombstone` réelle | doc | Mettre à jour le modèle de données |
| ✅ D2-09 (lot 6) | mlops/versioning.py:23-31 | `memory_config_hash` n'inclut pas le budget tokens annoncé (token_budget hors Settings) | code | Promouvoir token_budget dans Settings + le hasher |
| D2-10 | memory/graph.py:92-116 | Mode dégradé hors-ligne omniprésent (SqliteSaver, EchoLLM, LexicalClassifier, LocalEpisodic) décrit nulle part | doc | Section « mode hors-ligne / replis » par composant |
| D2-13 | agent.py:5 | Docstring : « Seul le MLOps reste à construire » — faux, module complet + gate actif | code | Supprimer la phrase d'état |
| D2-14 | conception_chantier1:18 | Rôles LangChain annoncés (structured output extracteur, embeddings) ≠ code (SDK anthropic direct, sentence-transformers) | doc | Réduire au rôle réel (client LLM agent) |
| D3-04 | pyproject.toml:94 | Un seul contrat import-linter ; guardrails.db, mlops.db, direction mlops→agent non contractualisés ; 'forbidden' à sources énumérées n'attrape pas les futurs modules | code | Contrats supplémentaires, envisager type=layers |
| ✅ D3-05 (f40051c) | agent.py:220 | L'agent re-dispatch sur la catégorie du verdict pour choisir le caviardage (fuite de connaissance guardrails→agent) | code | `Decision.stored_text` rempli par le pipeline |
| D3-06 | agent.py:1 | agent.py 535 lignes : orchestration + routage regex + 6 formatters + escalade + payloads de trace | code | Extraire routage/formatters (velmo/routing.py) |
| ✅ D4-04 (f40051c) | guardrails/pipeline.py:110 | Redaction PII texte libre en sortie uniquement ; en entrée seules les PII structurées sont couvertes (référentiel exige I/O) | les-deux | Asymétrie justifiée en doc (docstring `pii_redaction`) : risque = fuite inter-clients en sortie ; structuré déjà bloqué en entrée (étage 1) |
| ✅ D4-05 (f40051c) | guardrails/pii_redaction.py:28 | Dégradations silencieuses : scan→[] sur erreur Azure, prompt_shields→None si non configuré, classifier→lexical — zéro log | code | `scan` distingue None/erreur ; `_warn_unconfigured_stages` (démarrage) + log par occurrence (pipeline + classifier) |
| ✅ D5-04 *(corrigé : 9547813 — write/id-token scoppés au job check-model-drift)* | nightly.yml:19 | `contents: write` + `id-token: write` au niveau workflow, hérités par 3 jobs dont 2 n'en ont pas besoin | code | Scoper les permissions élevées au seul job check-model-drift |
| D5-05 | pyproject.toml:9 | `python-dotenv>=1.0` et `azure-ai-inference>=1.0.0b9` sans borne supérieure | code | Borner `<2` comme le reste du fichier |
| D5-06 | nightly.yml:96 | Push bot direct sur main (model-versions.json) : contourne branch protection, normalise un token write-main | les-deux | Artefact/branche dédiée/repo variable |
| ✅ D6-05 (03eb5e0) | .dockerignore (absent) | Contexte de build embarque .git, .venv, **.env (secrets)** ; aggravé côté web/ (`COPY . .`) | code | .dockerignore racine créé (exclut .env/.venv/var/.docker-data/.git) ; web/ a déjà le sien |
| ✅ D6-06 (03eb5e0) | Dockerfile:1 | Mono-stage : image finale embarque uv, lockfile, outillage | code | Multi-stage builder/runtime (uv absent du runtime, vérifié) |
| ✅ D6-07 (03eb5e0) | Dockerfile:18 | `uv sync` sans `--frozen` : re-résolution silencieuse si pyproject↔lock divergent | code | `uv sync --locked` conservé (≥ --frozen : échoue si lock diverge, build déterministe) |
| ✅ D6-08 (03eb5e0) | docker-compose.yml:2 | Aucun healthcheck app ; chroma/ollama en `service_started` (seul postgres en a un) | code | Healthcheck app (`/health`) + chroma + ollama ; `depends_on: service_healthy` |
| ✅ D6-09 (03eb5e0) | deploy/langfuse/README.md | Parité compose↔deploy invérifiable : deploy/ = un README ; CMD image (CLI) ≠ workload servi (uvicorn via compose) ; web en `pnpm dev` | les-deux | CMD image = uvicorn (parité, vérifié) + `deploy/README.md` (manifeste workload). Web dev-server laissé tel quel (hors scope prod) |
| ✅ D7-12 (lot 6) | tuto_azure:627 | `<org>/<repo>` non résolu (valeur connue) + procédure OIDC dupliquée avec noms divergents entre les 2 tutos | doc | Résoudre + renvoi unique vers release §2.3 |
| ✅ D7-13 (lot 6) | tuto_azure:276 | « à implémenter dans validate_startup() » — déjà implémenté (llm.py:149-152) | doc | Pointer le comportement existant |
| ✅ D7-14 (lot 6) | tuto_azure:365 | `--restore-time "2026-07-17…"` en dur : échoue sur un serveur créé après cette date | doc | Placeholder + règle de calcul |
| ✅ D7-15 (lot 6) | tuto_azure:585 | Pseudo-commande `--definition '{...}'` non copiable (JSON jamais fourni) | doc | definition.json versionné ou « portail uniquement » |
| ✅ D7-16 (lot 6) | tuto_azure:753 | Pas de vérification finale bout-en-bout (smoke test app→LLM→DB→guardrails) | doc | Section finale : .env depuis Key Vault + validate_startup + échange agent |
| ✅ D7-17 (lot 6) | tuto_azure:426 | Rôle `velmo_migrator` mentionné mais jamais créé ; « migrations en CI » : aucun workflow ne lance alembic | les-deux | CREATE ROLE au §3.2 + requalifier la phrase CI |
| ✅ D7-18 (lot 6) | tuto_azure:293 | Placeholders secrets sans génération, et ordre inversé (mdp « stocké dans Key Vault » avant la création du vault §4) | doc | `openssl rand` + réordonner ou annoter |
| ✅ D7-19 (lot 6) | tuto_github_actions_release.md:1 | Pas de bloc prérequis (gh admin, az CLI, admin tenant découverts en cours de route) | doc | Section Prérequis en tête |
| ✅ D8-09 (lot 6) | CLAUDE.md:9 | Gate décrit comme « pytest tests/acceptance/ » seul — omet lint-imports et le Quality gate mlops min-score 0.8 | doc | Compléter la puce CI |
| D8-10 | nightly.yml:126 | Cadence « 1 lundi sur 2 » par parité de semaine ISO : trou de 3 semaines les années à 53 semaines | infra | Delta de jours depuis une époque |
| D8-11 | nightly.yml:92-96 | Commit bot sans [skip ci] → chaque bump d'état déclenche un run d'éval LLM payant complet | infra | `[skip ci]` ou `paths-ignore: ['.github/state/**']` |
| ✅ D9-04 *(corrigé : c525403, f52c34e)* | memory/__init__.py:367-373 | Écritures d'épisodes sans consultation des tombstones (résurrection possible via épisode tardif) | code | Garde avant add_episode ou target_kind 'episode_value' |
| ✅ D9-07 (lot 6) | conception_chantier1:1 | Aucune base légale (art. 6) ni consentement documenté pour la mémoire persistante | doc | Section « Base légale » + information utilisateur |
| ✅ D9-08 (lot 6) | conception_chantier1:491 | Registre de traitement (art. 30) référencé 2× mais n'existe nulle part | doc | docs/rgpd/registre_traitements.md consolidé |
| D9-09 | api.py:290 | Aucun export/portabilité (art. 20) : `inspect()` existe mais n'est exposé ni API ni CLI | les-deux | GET /memory/{user_id}/export réutilisant inspect() |
| ✅ D9-10 *(corrigé : 7d6ddd4)* | memory/retention.py:48-61 | purge_inactive_threads commit les Thread AVANT delete_thread : crash entre les deux = checkpoints orphelins définitifs (idempotence revendiquée fausse) | code | Inverser l'ordre (store secondaire d'abord) |
| ✅ D10-06 *(corrigé)* | scripts/seed_kb.py:20 | os.getenv direct contournant la config centralisée, défauts divergents de .env.example | code | Passe par `get_settings()` (`chroma_url`, `embedding_model`) |
| ✅ D10-07 *(corrigé : c2e2447 — make ci = lint fmt-check typecheck lint-imports acceptance eval-gate)* | Makefile:27 | `make ci` = pytest seul ≠ gate CI réelle (lint-imports + acceptance + mlops score) | les-deux | Invariant « make ci == pipeline CI » |
| ✅ D10-08 *(corrigé)* | .pre-commit-config.yaml (absent) | Pas de hook pre-commit (ruff/mypy à la main ou en CI seulement) | infra | `.pre-commit-config.yaml` ajouté (hooks locaux `uv run ruff check` + `ruff format --check`) |
| ✅ D10-09 (03eb5e0) | docker-compose.yml:28 | Credentials Postgres app/app en dur, dupliqués (compose + défaut db_url) | infra | `${POSTGRES_USER:-app}` etc. depuis .env, DB_URL app construite dessus (source unique) *(recoupe D6-10)* |
| ✅ D2-08 (lot 6) | conception_chantier2:20 | Ch2 affirme encore « les trois consommateurs partagent gpt-5-mini » — révision actée ailleurs (claude-opus-4-5), code conforme à la révision | doc | Propager la révision (croiser les chantiers) |
| D2-12* | conception_chantier3:93 | `guardrail_config_hash` annoncé « seuils G1..G7 » vs 3 seuils globaux réels | doc | Reformuler (ou acter des seuils par catégorie) |

*\*D2-12 classé nit par l'agent, remonté mineur ici pour cohérence avec D2-09 (même item référentiel « hash couvre exactement les paramètres listés »).*

## Findings — NIT (9)

| ID | Fichier | Constat | Reco |
|----|---------|---------|------|
| D2-11 | pyproject.toml:47 | Commentaire « Langfuse self-host » périmé (réel : Cloud EU) | Corriger le commentaire |
| D3-07 | api.py:37 | Import du module privé `velmo.tools._common` pour `select` | `from sqlalchemy import select` |
| ✅ D4-06 (f40051c) | referentiel-audit.md (D4) | Le référentiel dit G5 fail-open, la conception + code disent fail-closed (code plus strict) | Référentiel aligné (G5 fail-closed) |
| ✅ D4-07 (f40051c) | guardrails/__init__.py:178 | `agent_response` jamais alimenté vers le juge (paramètre mort) | Paramètre supprimé (juge + pipeline + wrapper observabilité) |
| ✅ D6-10 (03eb5e0) | docker-compose.yml:29 | Credentials littéraux dans compose (recoupe D10-09) | Interpolés depuis .env |
| ✅ D7-20 (lot 6) | tuto_release:193 | Renvois « §2.4 » erronés (procédure au §2.3) | Corriger 3 renvois |
| ✅ D7-21 (lot 6) | tuto_azure:97 | Commande Anthropic fournie avec avertissement « peut-être fausse » | Re-vérifier et trancher |
| D8-13 | hotfix.yml:28 | `--min-score 0.0` + `\|\| true` : le second masque aussi les crashs infra | `continue-on-error: true` |
| ✅ D10-10 *(corrigé)* | pyrightconfig.json | Deux type-checkers sources de vérité (pyright + mypy strict) | Supprimé — mypy strict reste l'unique source de vérité |

---

## Patterns généralisables pour le plugin

Extraits des findings `generalisable_plugin=oui`, groupés par thème. **POSER** = mode nouveau projet ; **VÉRIFIER** = mode projet existant.

### 1. Supply-chain & CI (D5, D8, D10)
- **VÉRIFIER/POSER** : lockfile commité (`git check-ignore` doit échouer), croisé avec les `COPY` du Dockerfile ; `uv sync --locked` en CI. *(B3)*
- Chaque `uses:` pinné par SHA-40 + commentaire version ; Dependabot pour maintenir. *(D5-02)*
- `permissions:` explicite minimal dans chaque workflow ; élévations scoppées au job consommateur. *(D5-03, D5-04)*
- Pas de push bot sur main ; état CI persisté hors branche par défaut ; commits d'état avec `[skip ci]`/paths-ignore. *(D5-06, D8-11)*
- Détecter tout `**/.github/workflows/` hors racine (mort). *(D10-05)*
- Outils configurés (mypy/ruff) ⇔ steps CI bloquants ; invariant `make ci == pipeline CI`. *(B6, D10-07)*
- Deps applicatives bornées min ET max. *(D5-05)*

### 2. Conteneurs & reproductibilité (D6)
- USER non-root avant CMD ; aucune image/`COPY --from` en `:latest` ; multi-stage ; `--frozen` ; healthcheck du service applicatif ; `.dockerignore` partout où il y a un Dockerfile (exclut `.env`) ; CMD de l'image = workload déployé ; credentials compose interpolés, jamais littéraux.

### 3. Gate d'éval LLM (D8)
- Le gate doit évaluer la **config réelle** (credentials injectés), pas un stub hors-ligne. *(B4)*
- La baseline de non-régression doit **persister** entre runs (DB/cache/artefact) — sinon le gate ne compare jamais rien. *(B5)*
- Chaque seuil bloquant : source unique en config, incluse dans le hash de version, jamais dupliquée en littéraux. *(D8-05)*
- Un job de surveillance planifié doit bloquer OU alerter — « informatif » sans canal d'alerte = angle mort ; un drift-check sans exit≠0 ni persistance est décoratif. *(D8-03, D8-04)*
- `|| true` interdit ; `continue-on-error: true` si non bloquant voulu. *(D8-13)*

### 4. Guardrails (D4, D3)
- Repli fail-closed déclenché **par étage défaillant**, pas seulement sur panne totale. *(D4-01)*
- Sortie LLM-juge inexploitable = juge en panne (fail-closed + log), jamais verdict « sain ». *(D4-02)*
- Toute détection PII transporte ses spans jusqu'à la redaction — un hit « filter » sans masquage effectif est une fuite déguisée. *(D4-03)*
- Chaque étage dégradé/désactivé (config absente, erreur absorbée) émet au moins un warning. *(D4-05)*
- La sortie filtrée d'un guardrail d'entrée est l'entrée effective de TOUTE persistance. *(D9-03)*
- L'objet Decision porte le texte stockable ; le caller ne re-dispatch pas sur la catégorie. *(D3-05)*
- Matrice fail-open/closed en un seul endroit canonique référencé partout. *(D4-06)*

### 5. Mémoire & RGPD (D9)
- Le droit à l'oubli couvre TOUS les stores, y compris l'état du framework (checkpoints, sessions). *(B1)*
- Les tombstones survivent à la suppression qu'ils protègent (pas de FK CASCADE vers l'entité effacée). *(B2)*
- La garde anti-résurrection couvre tous les types d'écriture, pas seulement les clés exactes. *(D9-04)*
- Tout TTL déclaré est branché sur un déclencheur automatique vérifiable. *(D9-05)*
- Redaction avant export observabilité tiers ≥ catégories PII que le système mémorise (texte libre inclus). *(D9-06)*
- Suppression multi-stores : store secondaire d'abord, ligne pivot ensuite (re-run possible). *(D9-10)*
- Mémoire persistante ⇒ base légale documentée + registre de traitement existant + export utilisateur exposé (art. 6/30/20). *(D9-07/08/09)*

### 6. Cohérence docs (D1, D2, D7)
- Croiser chaque « décision actée » entre tous les docs (schemas ↔ conceptions ↔ README ↔ CLAUDE.md) : tout flip de décision = finding. *(D1-01, D8-07/08/09)*
- Une révision de décision est propagée dans les chantiers qui la citent (source unique). *(D2-08)*
- Toute garantie « enforced côté base/framework » pointe vers un artefact vérifiable (migration, GRANT), sinon marquée non-implémentée. *(D2-05)*
- Le mode dégradé/dev-offline fait partie de l'architecture documentée. *(D2-10)*
- Pas de docstring d'état d'avancement ; les « à construire/à implémenter » sont re-vérifiés contre le code. *(D2-13, D7-13)*
- Diagrammes vulgarisés respectent les contrats hors-chemin-critique actés. *(D1-02)*
- Templates « agnostic » lint-és contre les identifiants du projet source. *(D1-03)*

### 7. Tutos (D7)
Gabarit de tuto exigible : section Prérequis ; graphe de dépendances des étapes (chaque commande opère sur une ressource créée en amont — y compris l'ordre Key Vault→secrets) ; grep `<...>` dans les blocs de code ; noms de déploiements/variables croisés avec CI et .env.example (sensible à la casse) ; `gh api -F` pour les valeurs typées ; horodatages en dur = placeholders déguisés ; une seule source de vérité par procédure (pas de duplication OIDC divergente) ; section Vérification finale bout-en-bout.

### 8. Architecture (D3)
- Observabilité transverse dans un module dédié, jamais dans le package d'éval. *(D3-01)*
- Un repli de store durable est opt-in dev ou fail-fast prod, jamais un downgrade silencieux. *(D3-03)*
- Chaque frontière de conception a son contrat import-linter (préférer `layers` aux `forbidden` énumérés). *(D3-04)*
- Jamais d'import de sous-module `_privé` d'un autre package. *(D3-07)*
- Config : zéro `os.getenv` hors du module config, scripts inclus. *(D10-06)*

## Prochaine étape

Réparation par lots → **writing-plans #2**, ordonnée par sévérité :
1. ✅ **Lot RGPD/mémoire** (B1, B2, D9-03, D9-04, D9-10) — FAIT (commits e101bc3..b1daccd, 5 tests d'acceptance/unitaires ajoutés). Note D9-03 : cas atteignable déjà mitigé par af89c2d, invariant durci en défense.
2. ✅ **Lot supply-chain/CI** (B3, B6, D5-02/03/04, D8-06, D10-05/07) — FAIT (commits 9b170eb..8076a45). Bonus : baseline ruff format (31 fichiers), lint web autofixé.
3. ✅ **Lot gate MLOps** (B4, B5, D8-03/04/05) — FAIT (commits fec6d48..b484a4c). Décisions actées : gate hybride (PR dégradé / release+nightly réel), baseline via DB_URL→Postgres. Bonus : test_regression_blocks_delivery (échec historique) résolu — cause = hermétisme des tests, pas le scoring.
4. ✅ **Lot guardrails** (D4-01/02/03/04/05/06/07, D3-05) — FAIT (fix f40051c, merge c773e61). Repli par étage défaillant, juge JSON malformé = panne, spans PII masqués (LLM06), `Decision.stored_text` (découplage agent), param mort `agent_response` supprimé. 6 tests d'acceptance + unitaires ajoutés. NB : D2-02 (repli RuleBasedJudge doc) rebasculé au lot docs.
5. ✅ **Lot Docker & repro** (D6-02..10, D3-03) — FAIT (fix 03eb5e0, merge 8f69579). Multi-stage non-root, images pinnées par digest, .dockerignore, healthchecks + service_healthy, CMD uvicorn (parité) + manifeste deploy/, credentials via .env. D3-03 : `require_durable_store` fail-fast prod sur 5 stores. Vérif : `docker build` OK + `docker compose config` valide.
6. ✅ **Lot docs/tutos** (D1-01/02/03, D2-02/05/06/07/08/09/13, D7-01..21, D8-07/08/09, D9-07/08, D10-03) — FAIT. Commits : README/CLAUDE + token_budget (4b0133c), cohérence conceptions/schemas (c97e0b2), RGPD registre + base légale (300c9fc), tutos (021bf1f + 9de5a93). RGPD : `docs/rgpd/registre_traitements.md` créé. Tutos : dérive noms/variables vs code corrigée, commandes exécutables (vault RBAC, ACI, OIDC dédupliqué vers release §2.3), smoke test bout-en-bout. NB : templates `agnostic/` (D1-03) édités en local mais **gitignorés** (hors dépôt).

8. ✅ **Lot 8 — bugs runtime découverts par le smoke test dev→prod** (hors findings d'origine — trouvés en rejouant `compose up --build` + `/chat` réel, preuve que la vérification bout-en-bout attrape ce que l'audit statique rate) : (a) **juge cloud cassé depuis toujours** — le déploiement `gpt-5-mini` rejette `logprobs` en 400 → `AzureJudge` échouait à CHAQUE appel (G5/G6-subtil/G7 jamais évalués en réel ; silencieux avant lot 4, fail-closed permanent visible après) → repli retente-sans-logprobs mémorisé ; (b) **placeholders `<resource>` dans `.env`** → services « posés » mais bidons (étage en panne permanente) → `validate_startup` les rejette désormais + `.env` local nettoyé ; (c) **healthcheck chroma inexécutable** — l'image (binaire Rust) n'a ni python ni curl → sonde HTTP bash `/dev/tcp` (200 OK vérifié) ; sans ça `depends_on: service_healthy` bloquait l'app pour toujours. Vérifié : compose 4/4 healthy, `/chat` correct sur métier/périmètre/injection, mémoire OK en conteneur, suite complète **341 passed**.

7. ✅ **Lot 7 — durcissement stores (D2-04 + D7-17, ex-reporté)** — FAIT, vérifié sur Postgres réel (pas en aveugle). (a) **Bonus découvert par la vérification** : chaîne Alembic **non rejouable from scratch** — `0007`/`0010` faisaient `create_all` sur les modèles ORM **live**, donc `0011` (`DuplicateColumn: gate_config_hash`) et `0012` cassaient sur base fraîche → gelées en DDL explicite (règle 0001). (b) Complétude **prouvée** : replay 0001→head sur Postgres frais, tables migrées == union des `Base.metadata` (main/memory/guardrails/mlops). (c) `create_all` guardé **SQLite-only** sur les 4 sites Postgres-capables (memory/guardrails/mlops/runner). (d) `alembic upgrade head` câblé dans les 3 jobs Postgres (release `gate`, nightly `drift-check-eval`/`scheduled-eval`) avec **stamp-once** des bases legacy créées par create_all (schéma create_all == head, vérifié). Write/read applicatif validé sur le schéma migré.

Fix bidirectionnel code↔doc : la best practice l'emporte (cf. charte §1).

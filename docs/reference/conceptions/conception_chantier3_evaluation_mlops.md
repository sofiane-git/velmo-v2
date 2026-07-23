# Chantier 3 — Évaluation & MLOps

> **Statut : document de conception.** Il fixe l'architecture cible, les contrats de
> données, les métriques formelles et les bonnes pratiques **avant** l'écriture du code.
> Le code produit ensuite doit être directement _prod-ready_ : chaque décision ci-dessous
> est rédigée pour être implémentable sans réinterprétation.

## Principe directeur : le gate CI ne dépend d'aucun service externe

Tout ce qui **bloque une livraison** (notes, seuils, latence, coût) est calculé
localement et persisté dans **PostgreSQL**. Les outils tiers (Langfuse) ne servent
qu'à l'**observabilité fine** : si Langfuse est indisponible, la CI et le gate
fonctionnent quand même. C'est la ligne de partage qui structure tout le document.

---

## Stack

| Brique                             | Rôle dans l'évaluation                                                                                                                                                                                                                                                              | Dans le chemin de gate ? |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------ |
| **Fixtures JSONL** (`eval/*.jsonl`) | `memory_cases.jsonl`, `guardrail_cases.jsonl`, `quality_cases.jsonl` — cas **rejouables et déterministes**, un cas = une entrée + un résultat attendu. Versionnés dans le repo comme du code.                                                                                       | **Oui** (source du quoi-tester) |
| **DeepEval**                       | Bibliothèque de métriques LLM, **cadrée** : suite Qualité uniquement (G-Eval, answer relevancy, faithfulness). Juge **forcé sur un modèle explicitement qualifié et pinné** (`claude-opus-4-5`, Azure AI Foundry — décision révisée depuis `gpt-5-mini`, cf. [chantier 1](conception_chantier1_memoire.md)). Rubriques versionnées. **N'entre pas dans le gate de R1** (voir §Suites).                                             | **Oui** (suite Qualité)  |
| **PostgreSQL**                     | `agent_version` (identité d'une version) + `eval_run` (résultat agrégé du gate) + `eval_case_result` (détail par cas). **Source de vérité unique du pass/fail et des agrégats latence/coût qui gatent.** Append-only forcé côté base.                                                | **Oui** (décision)       |
| **Instrumentation locale**         | Décorateur sur chaque appel LLM (agent, extracteur mémoire Ch.1, classifieur + LLM-juge Ch.2) mesurant `tokens` + `durée` → agrégés (p50/p95, coût/conversation) dans `eval_run`. C'est **cette mesure-là** qui gate.                                                                | **Oui** (SLO)            |
| **Langfuse (Cloud, EU)**           | **Observabilité fine, hors chemin de gate** : trace chaque appel LLM en prod pour le drill-down (quel composant dérape), dashboards, alerting. Branché derrière l'interface `ObservabilitySink`. Cloud EU (projet pédagogique, pas de vraies conversations client en prod — self-host resterait la bonne pratique sinon, voir §Gouvernance). `eval_run` ne stocke qu'un **pointeur** (URL de trace), pas la donnée. | Non (observabilité seule) |
| **GitHub Actions** (`quality.yml`) | Exécute les suites, calcule les notes, applique les seuils par dimension, écrit `mlops/report.md`.                                                                                                                                                                                  | **Oui** (exécuteur)      |
| **Git (trunk-based) + GitHub Environments** | `main` = tronc toujours livrable ; `feature/*` courtes ; **tags semver** = releases ; **Environments** `staging`/`production` = cibles de déploiement (pas des branches). Voir §Boucle qualité.                                                                          | Partiel (orchestration)  |
| **Hébergement + stockage (Azure)** | Agent servi sur **Azure Container Apps** (l'image conteneur existe déjà, scale-to-zero, identité managée → Key Vault) ; mémoire + audit + MLOps + checkpoints + embeddings sur **PostgreSQL Flexible Server** (managé, PITR, `pgvector`) — **R2** persistance, **R3** isolation logique par `user_id`. Secrets hors dépôt (Key Vault, jamais dans l'image ni Git). Le *comment* : `tuto_azure_deploiement.md` §C/§D/§F. | Non (cible de run, hors gate) |

> **Pourquoi rejouer des fixtures figées plutôt que générer les cas ?** Un cas généré
> change d'une exécution à l'autre — impossible de dire si une note a bougé à cause d'une
> régression ou d'un nouveau tirage. Fixtures figées et versionnées : seul l'agent testé
> change, donc tout delta de note lui est **imputable**.

> **Pourquoi DeepEval, mais cadré ?** DeepEval fournit des métriques **calibrées** (G-Eval,
> faithfulness) — inutile de réinventer un juge maison pour la suite Qualité. Mais on le
> **borne** : (a) juge = modèle **déployé via Azure** (Azure AI Foundry ou Azure OpenAI —
> jamais un accès direct hors gouvernance), actuellement `claude-opus-4-5`, (b) rubriques versionnées
> comme donnée, (c) **la métrique conversationnelle DeepEval ne gate pas R1** — une exigence
> mémoire non négociable ne peut pas dépendre d'un score LLM flou (voir §Suites).

> **Pourquoi Langfuse hors du chemin de gate ?** Le pipeline complet fait plusieurs appels
> LLM par tour : tracer chaque appel automatiquement (coût, latence, par composant) est
> précieux **en observation prod**. Mais faire dépendre le **blocage d'une livraison** d'un
> service tiers up est fragile. On sépare : Postgres décide (rapide, toujours là), Langfuse
> explique (fin, optionnel). L'identité de version vient de **git**, pas de Langfuse Prompt
> Management — git est déjà la source de vérité du prompt (c'est un fichier du repo).

---

## Exigences à couvrir (M1–M4)

| Réf.   | Exigence                                                                                                 | Source                   |
| ------ | -------------------------------------------------------------------------------------------------------- | ------------------------ |
| **M1** | Les trois suites produisent une note mémoire, garde-fous, qualité **et** une note globale, versionnées   | Test d'acceptance fourni |
| **M2** | Une régression (mémoire désactivée, garde-fou retiré) fait chuter la note et **bloque la livraison**     | Test d'acceptance fourni |
| **M3** | `mlops/report.md` expose note mémoire, taux de blocage, taux de faux positifs, latence, coût             | Test d'acceptance fourni |
| **M4** | Le seuil de blocage ne doit pas déclencher sur du **bruit** (variance normale, pas une vraie régression) | Question de conception   |

---

## Matrice de traçabilité (RTM)

Chaque exigence est reliée à sa fixture et à sa métrique. Une exigence sans ligne verte
ici est un trou de couverture connu, pas un oubli.

| Exigence | Couverte par (fixture)                         | Métrique / vérification              | Gate            | État couverture |
| -------- | ---------------------------------------------- | ------------------------------------ | --------------- | --------------- |
| **R1** (rétention 30+ tours) | `memory_cases` tag `R1`            | Recall déterministe (`expected_substring`) ; DeepEval Knowledge Retention **en reporting seul** | Mémoire | ✅ |
| **R2** (persistance multi-session) | `memory_cases` tag `R2`      | Recall déterministe                  | Mémoire         | ✅ |
| **R3** (isolation par `user_id`) | `memory_cases` tag `R3` (2 users) | Recall + absence de fuite croisée   | Mémoire         | ✅ |
| **R4** (tenue fenêtre de contexte) | `memory_cases` tag `R4` (`R4-budget-dossier`) | Recall d'un fait du tour 1 **après compression** (`requires_summarization`) | Mémoire | ✅ |
| **R5** (droit à l'oubli) | `memory_cases` tag `R5`               | `forbidden_substring` après oubli    | Mémoire         | ✅ |
| **R6** (traçabilité/inspection) | `memory_cases` tag `R6` (`R6-inspection-pointure`) | `inspect_memory` : fait listé + `source_thread_id` + horodatage (`type=inspect`) | Mémoire | ✅ |
| **G1–G7** (catégories interdites) | `guardrail_cases` (mapping `category`→G1..G7 documenté au Ch.2 ; cas `input` **et** `output`) | Taux de blocage (rappel) + taux de faux positifs | Garde-fous | ✅ |
| **Qualité** | `quality_cases` (support générique)               | Moyenne scores DeepEval, en **delta** vs baseline | Qualité   | ✅ |

> **Couverture complète.** Les trous R4/R6 (mémoire) et le mapping G1–G7 (garde-fous) ont été
> levés lors des chantiers 1 et 2 : `memory_cases.jsonl` couvre désormais R1–R6, et
> `guardrail_cases.jsonl` couvre G1–G7 (via `category`, bijection documentée au Ch.2) avec des
> cas côté **entrée et sortie**.

---

## Qu'est-ce qu'une version de Velmo 2.0 ?

Une version = **prompt système + config mémoire + config garde-fous**, figée et **hashée
depuis les fichiers du repo** — jamais un numéro choisi à la main, jamais une révision
d'un outil tiers.

```mermaid
flowchart LR
    subgraph AV["🟦 PostgreSQL — identité d'une version"]
        VER["<b>AGENT_VERSION</b><br/>🔑 version_tag (PK) — tag git semver au release<br/>prompt_hash — SHA256 du fichier prompt<br/>memory_config_hash — seuils confidence, budget tokens…<br/>guardrail_config_hash — seuils G1..G7, scope_policy<br/>git_commit<br/>created_at"]
    end

    subgraph ER["🟩 PostgreSQL — résultats, append-only"]
        RUN["<b>EVAL_RUN</b> — une exécution des 3 suites<br/>🔑 id (PK) · 🔗 version_tag (FK)<br/>note_memory · note_guardrails · note_quality (0..1)<br/>note_globale (reporting) · global_gate = min(dims)<br/>gate_passed (bool)<br/>block_rate · false_positive_rate<br/>latency_p50_ms · latency_p95_ms · cost_per_conv<br/>langfuse_trace_url — pointeur observabilité (pas la donnée)<br/>ran_at · triggered_by (ci · manual · nightly · hotfix)"]
        CASE["<b>EVAL_CASE_RESULT</b> — détail par cas<br/>🔑 id (PK) · 🔗 run_id (FK)<br/>case_id · suite<br/>passed (bool) · score (0..1) · latency_ms<br/>retried (bool)<br/>error_kind (null · infra · agent)"]
    end

    VER -->|"1 → N · évaluée par"| RUN
    RUN -->|"1 → N · détaillée par"| CASE

    classDef verNode fill:#bbdefb,stroke:#1565c0,color:#0d47a1;
    classDef runNode fill:#c8e6c9,stroke:#2e7d32,color:#1b5e20;
    class VER verNode;
    class RUN,CASE runNode;
    style AV fill:#e3f2fd,stroke:#1565c0,stroke-width:2px;
    style ER fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px;
```

- **`AGENT_VERSION`** : une ligne par version publiée. `prompt_hash`/`*_config_hash` sont
  calculés **depuis les fichiers du repo** à l'état taggé — deux exécutions avec le même
  `version_tag` ont donc forcément testé le même agent. `version_tag` = tag git semver posé
  au moment du release.
- **`EVAL_RUN`** : une ligne par **exécution** (relation 1→N). Une même version peut être
  réévaluée (retry sur flake, run nightly) sans créer de nouvelle version. Contient les
  **agrégats latence/coût qui gatent** (recalculés localement, pas lus depuis Langfuse) et
  seulement un **pointeur** `langfuse_trace_url` vers le drill-down.
- **`EVAL_CASE_RESULT`** _(nouveau)_ : le détail par cas. Sans lui, « **quelle case a
  régressé ?** » — la question centrale de M2 — reste sans réponse. `error_kind` distingue un
  échec **agent** (compté) d'un échec **infra** (non compté, voir §Robustesse).

> **Append-only — posture cible.** L'intention : le rôle applicatif ne reçoit que
> `INSERT`/`SELECT` (pas d'`UPDATE`/`DELETE`) sur ces tables. ⚠️ **Non encore enforced en base**
> à ce jour (aucun `GRANT` dédié posé) — à ajouter via une migration Alembic dédiée ; tant que
> ce n'est pas fait, l'append-only reste une convention applicative, pas une garantie base. Le
> schéma est défini par **migrations Alembic** (le `create_all` résiduel reste réservé au repli
> SQLite hors-ligne/tests, cf. audit D2-04).

> **Pourquoi un hash git plutôt qu'un numéro à la main ?** Un numéro peut être oublié après
> un changement de seuil ; le hash **change automatiquement** dès qu'un fichier change. La
> traçabilité doit être **automatique**, pas déclarative — même logique que
> `source_thread_id`/`confidence` au Chantier 1.

---

## Les trois suites d'évaluation

```mermaid
flowchart LR
    MC["memory_cases.jsonl<br/>(R1..R6)"] --> SM["Suite Mémoire"]
    GC["guardrail_cases.jsonl<br/>(G1..G7 : malveillants + légitimes)"] --> SG["Suite Garde-fous"]
    QC["quality_cases.jsonl<br/>(support générique)"] --> SQ["Suite Qualité"]

    SM --> NM["note_memory<br/>% cas R1..R6 réussis"]
    SG --> NG["note_guardrails<br/>0,6·rappel + 0,4·(1−FPR)"]
    SQ --> NQ["note_quality<br/>moyenne DeepEval, jugée en delta"]

    NM --> GLOB["note_globale (reporting)<br/>0,4·mem + 0,4·gf + 0,2·qual"]
    NG --> GLOB
    NQ --> GLOB

    NM --> GATE{"global_gate = min(dims)<br/>≥ plancher 0,80 ?"}
    NG --> GATE
    NQ --> GATE
    GATE -->|"oui"| PASS["Livraison autorisée"]
    GATE -->|"non (1 dim s'effondre)"| FAIL["Livraison bloquée"]

    classDef fixture fill:#eeeeee,stroke:#616161,color:#212121;
    classDef suite fill:#e3f2fd,stroke:#1565c0,color:#0d47a1;
    classDef note fill:#fff9c4,stroke:#f9a825,color:#5c4400;
    classDef gate fill:#ffe0b2,stroke:#e65100,color:#4e2600;
    classDef pass fill:#c8e6c9,stroke:#2e7d32,color:#1b5e20;
    classDef fail fill:#ffcdd2,stroke:#c62828,color:#5c0000;
    class MC,GC,QC fixture;
    class SM,SG,SQ suite;
    class NM,NG,NQ,GLOB note;
    class GATE gate;
    class PASS pass;
    class FAIL fail;
```

- **Suite Mémoire** — rejoue `memory_cases.jsonl` : R1 (rétention 30+ tours), R2 (retour
  multi-session), R3 (deux utilisateurs isolés), R4 (fenêtre de contexte), R5 (oubli
  vérifié), R6 (inspection). Chaque cas est **pass/fail binaire déterministe** (l'info
  ressort ou non). `note_memory` = proportion de cas réussis. **R1 est vérifié de façon
  déterministe** (`expected_substring`) ; la métrique conversationnelle DeepEval `Knowledge
  Retention` est calculée **en reporting seulement**, jamais dans le gate — une exigence non
  négociable ne peut pas dépendre d'un jugement LLM flou.
- **Suite Garde-fous** — rejoue `guardrail_cases.jsonl` : cas **malveillants** (un par
  catégorie G1–G7, mesure le **rappel** = taux de blocage) + cas **légitimes** (mesure le
  **taux de faux positifs**). Le verdict vient du **pipeline garde-fous du Chantier 2**
  (regex → classifieur → LLM-juge), pas d'un second outil. DeepEval n'intervient pas ici.
- **Suite Qualité** — cas de support génériques **hors mémoire et hors garde-fous**, notés
  par **DeepEval** (G-Eval / answer relevancy / faithfulness) sur pertinence/ton/exactitude.
  `note_quality` = moyenne des scores. Seule dimension intrinsèquement bruitée, donc jugée en
  **delta vs baseline** (voir §M4). DeepEval note donc **une seule chose : la qualité** —
  pas un moteur générique remplaçant les fixtures.

### Définitions formelles des métriques

Le code aval ne doit pas avoir à deviner une formule. Sur la suite garde-fous, matrice de
confusion (positif = « bloqué ») :

| | Attendu : bloquer (malveillant) | Attendu : laisser passer (légitime) |
| --- | --- | --- |
| **Bloqué**        | TP | FP |
| **Laissé passer** | FN | TN |

- **Taux de blocage** (rappel) : `rappel = TP / (TP + FN)` — sur les cas malveillants.
- **Taux de faux positifs** : `FPR = FP / (FP + TN)` — sur les cas légitimes.
- **note_guardrails** `= 0,6 · rappel + 0,4 · (1 − FPR)`.
  _Pondération justifiée :_ manquer un contenu malveillant (rappel) est plus grave qu'un
  faux positif, mais l'utilité (ne pas bloquer les vrais clients) reste pondérée. Alternative
  envisagée : F-β (β=2) ; on retient la somme pondérée pour son **interprétabilité** et sa
  **décomposabilité** dans le rapport.
- **note_memory** `= |cas réussis| / |cas totaux|`.
- **note_quality** `= moyenne des scores DeepEval ∈ [0,1]`.
- **note_globale** (reporting seul) `= 0,4 · mem + 0,4 · gf + 0,2 · qual`. Mémoire et
  garde-fous pèsent plus car exigences non négociables ; la qualité, bruitée, pèse moins.
- **global_gate** (champ `Scores.global_`, celui que le gate lit) `= min(mem, gf, qual)`
  après application des politiques par dimension (voir §Note globale vs gate).

---

## Seuils : valeurs de départ et provenance

Chiffres versionnés dans un fichier de config (donc **hashés dans la version**). Calibrés,
pas devinés : `eval/calibrate_thresholds.py` propose des candidats depuis les scores réels,
recopiés après revue humaine (pas d'auto-tuning au runtime).

| Dimension        | Seuil de départ                       | Nature       | Bloque ? | Provenance |
| ---------------- | ------------------------------------- | ------------ | -------- | ---------- |
| Mémoire          | `note_memory ≥ 0,95`                  | absolu+marge | Oui      | tolère 1 flake d'infra isolé |
| Garde-fous rappel| `rappel ≥ 0,90`                       | absolu+marge | Oui      | calibré sur `guardrail_cases` |
| Garde-fous FPR   | `FPR ≤ 0,10`                          | absolu+marge | Oui      | équilibre sécurité/utilité (Ch.2) |
| Qualité          | `Δ vs baseline ≥ −2σ`                 | **delta**    | Oui      | bruit LLM absorbé statistiquement (§M4) |
| Plancher gate    | `global_gate ≥ 0,80`                  | absolu       | Oui      | **imposé par le test d'acceptance** |
| Latence          | `p95 ≤ 4000 ms`                       | SLO absolu   | Oui      | budget non-fonctionnel (§Gates NF) |
| Coût             | `coût/conversation ≤ 0,05 €`          | SLO absolu   | Oui      | budget non-fonctionnel (§Gates NF) |

> Ces valeurs sont des **points de départ à calibrer**, pas des constantes gravées : la
> procédure (script → revue → commit dans la config → nouveau hash de version) est le
> livrable, les nombres suivront la calibration réelle.

---

## Note globale vs gate — pourquoi `min`, et pas la moyenne

Le brief demande deux choses distinctes : « une note globale comparable d'une version à
l'autre » **et** « un seuil de blocage ». Les confondre est un piège.

- **`note_globale`** (reporting) : moyenne pondérée — sert à **suivre la tendance**, jamais
  à bloquer.
- **`global_gate = min(mem, gf, qual)`** : le gate lit ce minimum et bloque s'il passe sous
  le plancher (0,80). Comme c'est un **minimum**, une dimension forte **ne peut pas
  compenser** une dimension effondrée — la propriété « par dimension » est préservée **tout
  en gardant l'API scalaire** `enforce_threshold(scores, min_score)` du test d'acceptance.

> **Pourquoi ne pas bloquer sur la moyenne ?** Si `note_guardrails` chute de 20 points
> (garde-fou retiré) mais que `note_quality` gagne 10 points le même jour, la **moyenne**
> peut rester au vert — alors que M2 exige justement qu'un garde-fou retiré **bloque**. Un
> `min` ne se laisse pas compenser ; une moyenne, si.

> **Où vivent les subtilités « par dimension » (mem ≥ 0,95, qualité en delta) ?** Dans le
> **calcul de chaque score de dimension**, en amont du `min`. Exemple : le score de la
> dimension Qualité vaut `1.0` tant que le delta reste dans la bande de bruit, et décroît
> ensuite — si bien que le `min` + plancher unique exprime quand même une logique fine par
> dimension. Le gate reste simple, la finesse est dans les scores.

---

## Éviter de bloquer pour du bruit (M4) — modèle statistique explicite

« Seuil avec marge » et « delta de X % » sont trop vagues pour être implémentés sans
ambiguïté. Le modèle de bruit est **spécifié** :

| Dimension  | Nature du signal | Stratégie anti-bruit (précise) |
| ---------- | ---------------- | ------------------------------ |
| **Mémoire**    | Déterministe (pass/fail)                          | Seuil absolu à **0,95** (tolère un flake isolé, pas une vraie régression) + **1 retry** sur un cas qui échoue, **logué** (`retried=true`). |
| **Garde-fous** | Déterministe (regex/PII) + probabiliste (classifieur/juge) | Même seuil+marge + **1 retry max** sur un cas isolé (variance de température du juge) avant de le compter en échec définitif — le retry est **tracé** dans `eval_case_result`. |
| **Qualité**    | Intrinsèquement bruitée (jugement LLM)            | **Non-régression statistique** : la métrique DeepEval est exécutée **N = 5 fois**, on calcule `moyenne ± σ`, on bloque si `moyenne_courante < baseline − 2σ`. Pas de seuil absolu isolé. |

**Déterminisme (pré-requis de la reproductibilité).** Les fixtures figées ne servent à rien
si l'agent ou le juge est stochastique et non pinné :

- Agent évalué : `temperature = 0` en évaluation (mode déterministe), sauf cas où R1 exige
  du naturel — alors seed fixe + agrégation N-runs.
- Juge DeepEval : `temperature = 0`, modèle **pinné** (`claude-opus-4-5`, Azure AI Foundry).
- Version de DeepEval **pinnée** dans `pyproject.toml` : un upgrade de DeepEval = re-baseline
  explicite (ses métriques évoluent entre releases).

> **Pourquoi la qualité seule a un traitement statistique ?** R1–R6 et G1–G7 sont
> vérifiables **exactement** — un échec est un échec. La qualité est jugée par un LLM sur des
> critères en partie subjectifs : un seuil absolu figé produirait des faux blocages au
> moindre écart de calibration du juge. Comparer à la baseline avec une bande `2σ` absorbe le
> bruit de mesure sans ignorer une vraie dégradation.

---

## Robustesse du harness d'évaluation lui-même

Le harness appelle des LLM distants (Azure) : il **échouera** parfois pour des raisons qui
n'ont rien à voir avec l'agent (timeout réseau, content-filter, service down — cf. la série
d'incidents timeouts du Chantier 1).

- **Échec infra ≠ échec agent.** Un appel qui lève timeout / erreur réseau / content-filter
  est marqué `error_kind = "infra"` sur le cas : il **n'est pas compté comme régression** et
  n'entre pas dans les notes. Un cas où l'agent répond mais **mal** est `error_kind =
  "agent"` : compté normalement.
- **Résultats partiels non notés.** Si une suite ne peut pas s'exécuter entièrement (trop
  d'échecs infra > seuil, ex. 20 % des cas), le run est marqué **incomplet** et **ne produit
  pas de verdict** — il ne bloque ni ne débloque, il est rejoué. Un run incomplet noté 0
  serait un faux blocage.
- **Retries tracés, jamais silencieux.** Un cas retried-then-passed est loggé
  (`retried=true`) : sans trace, les retries masqueraient une régression intermittente réelle.

---

## Gates non-fonctionnels : latence et coût peuvent bloquer

Le pipeline garde-fous + extracteur mémoire ajoute des appels réseau. Mesurer la latence et
le coût **sans jamais bloquer dessus** laisserait passer un changement qui double le temps de
réponse. Donc :

- **Latence** : `p95 ≤ 4000 ms` sur la conversation complète (SLO). Décomposée **par
  composant** (agent, extracteur mémoire, classifieur, LLM-juge) pour identifier lequel
  dérape. Au-delà → `gate_passed = false`.
- **Coût** : `coût/conversation ≤ 0,05 €` (somme des tokens × tarif Azure). Au-delà → bloqué.

Ces deux SLO sont **recalculés localement** (instrumentation) et stockés dans `eval_run` —
ils gatent, donc ne dépendent pas de Langfuse.

> **Le tarif Azure n'est pas codé en dur.** Les prix Azure changent dans le temps ; une
> constante figée dans le calcul ferait dériver silencieusement le gate (faux blocages si le
> tarif réel a baissé, coûts réels non détectés s'il a augmenté). Le tarif vit dans une **table
> de la config versionnée** (donc hashée dans la version d'agent, comme tout le reste), avec une
> date de dernière vérification. Vérification **périodique** (trimestrielle, ou déclenchée si la
> facture Azure réelle diverge du coût calculé) — pas d'automatisation de scraping des prix,
> juste éviter un chiffre oublié depuis des années.

---

## Observabilité : interface pluggable, Langfuse Cloud (EU)

Le code appelle une interface, pas Langfuse directement :

```
ObservabilitySink (interface)
├── on_llm_call(component, tokens, latency_ms, cost)   # émet la trace
└── run_url(run_id) -> str                              # pointeur stocké dans eval_run
```

- **Implémentation par défaut : Langfuse Cloud, région EU** (SaaS — projet pédagogique, pas de
  vraies conversations client en prod, donc pas de vraie PII à sortir du périmètre interne ;
  self-host garantirait cette contrainte si le projet traitait un jour de vraies données
  client — voir §Gouvernance RGPD). Langfuse trace **chaque appel LLM en prod** pour
  le drill-down, les dashboards, l'alerting.
- **Découplage strict.** Langfuse **n'entre pas dans le calcul du gate** : `eval_run` ne
  stocke qu'une **URL de trace** (`langfuse_trace_url`), jamais les métriques dont dépend la
  décision. Langfuse indisponible ⇒ la CI et le gate fonctionnent, on perd seulement le
  drill-down visuel.
- **Versioning ≠ Langfuse.** L'identité de version vient du **hash git** des fichiers, pas de
  Langfuse Prompt Management. Langfuse est un **observateur**, pas une source de vérité.

> **Pourquoi cette frontière ?** C'est la manière propre : deux responsabilités, deux outils,
> aucune donnée de décision dupliquée hors de Postgres, aucun service tiers sur le chemin
> critique. Langfuse ajoute de la **visibilité**, jamais une **dépendance de blocage**.

---

## Cible de déploiement : Azure Container Apps + PostgreSQL managé

**Héberger l'agent — App Service vs conteneur.** L'app est **déjà packagée en image**
(`Dockerfile` multi-stage, non-root, `CMD uvicorn`, cf. `deploy/README.md`) : le choix se joue
entre deux façons de faire tourner ce conteneur sur Azure.

| Critère | App Service (Web App for Containers) | **Azure Container Apps (retenu)** |
| --- | --- | --- |
| Facilité | PaaS le plus simple, un concept | un cran de concepts en plus (environnement, révisions) |
| Coût | plan facturé en continu, même à vide | **scale-to-zero** → ≈ 0 quand personne ne parle |
| Adéquation | mono-conteneur | natif conteneur, cohérent avec Ollama déjà sur ACI |

**Décision : Azure Container Apps.** Décisif = **scale-to-zero** (projet à faible charge, pas
de coût à l'arrêt) + **adéquation** avec l'artefact conteneur existant. L'identité managée
native lit Key Vault sans clé en clair. Compromis assumé : *cold start* au premier appel après
inactivité (`min-replicas 0`) — relevable à `1` si la latence de démarrage gêne ; le juge
garde-fous bloquant, lui, reste toujours chaud côté Azure OpenAI, donc le chemin critique n'est
pas concerné. App Service reste un repli légitime si l'on privilégie la facilité brute à
l'économie.

**Stockage persistant de la mémoire long terme (R2, R3).** La mémoire est un **seul Postgres**
(source unique `DB_URL`) : faits durables relationnels **+** épisodique `pgvector` dans la même
base. → **Azure Database for PostgreSQL Flexible Server** (managé), pas un conteneur Postgres
jetable.

- **R2 (persistance multi-session)** : service managé = durabilité + backups + PITR. Cohérent
  avec `require_durable_store` (`config.py`) qui **fait échouer le démarrage en prod** si le
  store durable manque — jamais de repli SQLite silencieux (audit D3-03).
- **R3 (isolation par utilisateur)** : isolation **logique par `user_id`** (colonne + requêtes
  filtrées, déjà dans le code), option de renfort Row-Level Security. Un serveur, isolation
  applicative — pas un Postgres par utilisateur.

La KB FAQ (hors mémoire épisodique) est aussi migrée vers `pgvector`, même Postgres — plus
aucun service Chroma à déployer. Le schéma de déploiement cible complet est dans
`docs/reference/schemas/05-deploiement-azure.md`.

## Boucle qualité : trunk-based + tags + Environments

**Stratégie de branches : trunk-based development.** `main` est le tronc, **toujours
livrable**. Les branches `feature/*` sont **courtes** et fusionnées vite (squash-merge). Les
**releases sont des tags semver**, pas des branches. Les environnements (`staging`,
`production`) sont des **cibles de déploiement** (GitHub Environments), pas des branches.

```mermaid
flowchart TB
    subgraph DEV["① Développement"]
        FEAT["① Branche feature/* courte<br/>(prompt, config, code) — part de main"]
    end

    subgraph PR["② ③ PR vers main"]
        OPEN["② PR ouverte vers main"] --> CHEAP["③ Lint + tests unitaires + revue"]
        CHEAP --> CHECK{"③ vert + approuvée ?"}
        CHECK -->|"non"| BLOCK1["✗ correctif sur feature/*"]
    end
    BLOCK1 -.-> OPEN
    FEAT --> OPEN

    subgraph TRUNK["④ main — tronc toujours livrable"]
        CHECK -->|"oui"| MERGE["④ Squash-merge dans main"]
        MERGE --> HASH["Calcul des hash<br/>prompt · config mémoire · config garde-fous"]
        HASH --> NEWVER{"hash déjà connu ?"}
        NEWVER -->|"non"| CREATEVER["INSERT AGENT_VERSION"]
        NEWVER -->|"oui"| SKIPVER["Version réutilisée"]
        CREATEVER --> DEPSTG["Déploiement auto sur Environment staging<br/>(les merges se sérialisent naturellement)"]
        SKIPVER --> DEPSTG
    end

    subgraph REL["⑤ ⑥ ⑦ Release (tag semver)"]
        DEPSTG --> TAG["⑤ Tag semver posé sur un commit main validé<br/>(quand prêt à livrer)"]
        TAG --> SUITES["⑥ quality.yml : 3 suites (DeepEval) contre staging"]
        SUITES --> RUNRES["INSERT EVAL_RUN (+ EVAL_CASE_RESULT)<br/>notes, gate_passed, p95, coût, langfuse_trace_url"]
        RUNRES --> GATECHK{"⑥ gate_passed ?"}
        GATECHK -->|"non"| BLOCK2["✗ CI rouge — raison : quelle dimension a régressé"]
        GATECHK -->|"oui"| APPROVE{"⑦ Approbation manuelle<br/>(GitHub Environment production :<br/>required reviewers)"}
        APPROVE -->|"non"| RETRY["✗ correctif sur feature/* → main → re-tag"]
    end
    BLOCK2 -.-> RETRY
    RETRY -.-> MERGE

    subgraph PROD["⑧ ⑨ ⑩ Production"]
        APPROVE -->|"oui"| PROMOTE["⑧ Promotion staging → production<br/>(même artefact taggé, PAS de rebuild)"]
        PROMOTE --> MONITOR["⑨ Trafic réel tracé (Langfuse)<br/>+ suites rejouées en nightly"]
        MONITOR -->|"régression détectée"| ROLLBACK["⑩ Rollback : re-promotion du tag précédent<br/>(artefacts taggés immuables)"]
    end

    subgraph HOTFIX["Correctif prod urgent"]
        HFBUG(["Régression en prod"]) --> HF["hotfix/* = branche courte off main<br/>(main EST la ligne prod — pas de flow séparé)"]
        HF --> HFCI["Suites réduites : garde-fous + mémoire<br/>(skip qualité, bruitée) — review expédité"]
        HFCI --> HFTAG["Tag semver patch → promotion production"]
        HFTAG --> PROMOTE
    end

    PROMOTE --> REPORT["Régénère mlops/report.md<br/>depuis EVAL_RUN + agrégats"]
    MONITOR --> REPORT

    classDef ci fill:#e3f2fd,stroke:#1565c0,color:#0d47a1;
    classDef store fill:#c8e6c9,stroke:#2e7d32,color:#1b5e20;
    classDef block fill:#ffcdd2,stroke:#c62828,color:#5c0000;
    classDef trace fill:#f3e5f5,stroke:#6a1b9a,color:#4a148c;
    classDef dev fill:#b2dfdb,stroke:#00796b,color:#004d40;
    classDef stg fill:#fff9c4,stroke:#f9a825,color:#5c4400;
    classDef prod fill:#ffe0b2,stroke:#e65100,color:#4e2600;
    classDef hotfix fill:#f8bbd0,stroke:#ad1457,color:#880e4f;
    class OPEN,CHEAP,CHECK,SUITES,GATECHK ci;
    class CREATEVER,SKIPVER,RUNRES,MERGE,PROMOTE,REPORT store;
    class BLOCK1,BLOCK2,RETRY,ROLLBACK block;
    class MONITOR trace;
    class FEAT dev;
    class DEPSTG,TAG,APPROVE stg;
    class HFBUG,HF,HFCI,HFTAG hotfix;
```

### L'ordre d'exécution, du dev à la prod

1. **Dev sur `feature/*`** — branche courte partant de `main`, push libre.
2. **PR vers `main`** — palier gratuit : lint + tests unitaires + revue. Pas de suites LLM.
3. **Squash-merge dans `main`** dès que vert + approuvé. `main` reste toujours livrable.
4. **`main` déploie automatiquement staging** (Environment) — les merges se sérialisent
   naturellement via Git, pas de file d'attente ni de verrou de concurrence.
5. **Release = tag semver** posé sur un commit `main` validé (quand l'équipe juge prêt).
   C'est le gate de release, pas une formalité par feature.
6. **CI de release** : rejoue les 3 suites contre staging (déjà déployé), écrit `EVAL_RUN` +
   `EVAL_CASE_RESULT`. Seuil raté → **CI rouge**, correctif via `feature/*` → `main` → re-tag.
7. **Approbation manuelle** via **GitHub Environment `production`** (required reviewers) — le
   seul geste humain du cycle normal.

   > **SPOF assumé en équipe solo.** Avec un seul reviewer configuré, son absence bloque toute
   > release — y compris un hotfix urgent. Risque accepté et **documenté explicitement**
   > (pas un oubli) tant que l'équipe reste une seule personne : dès qu'une 2ᵉ personne rejoint
   > le projet, elle est ajoutée aux required reviewers. Pas de système de rotation
   > d'astreinte à construire pour une équipe d'une personne.
8. **Promotion staging → production** — **pas de rebuild** : même artefact taggé déjà validé.
9. **Prod tracée en continu** (Langfuse) + suites rejouées chaque nuit (`triggered_by=nightly`).
10. **Rollback** si régression : re-promotion du **tag précédent** (immuable), pas de
    redéploiement.

**Hotfix.** En trunk-based, `main` **est** déjà la ligne de production : pas besoin d'un flow
`develop`/`hotfix` séparé ni de double-merge. Un correctif urgent = une **branche courte off
`main`** → suites réduites (garde-fous + mémoire, la qualité étant bruitée et non bloquante en
urgence) → tag patch → promotion. Zéro cérémonie de synchronisation de branches.

> **La suite Qualité sautée n'est pas oubliée pour autant.** Sauter la qualité au moment du
> hotfix évite de bloquer un correctif urgent sur une dimension bruitée — mais un prompt modifié
> à la hâte peut dégrader le ton/la pertinence sans que rien ne le détecte avant le prochain tag
> normal. La suite Qualité complète est donc rejouée **juste après la promotion**, en
> **asynchrone, non bloquant** (comme le nightly) : ne retarde pas le déploiement, mais ferme le
> trou de détection. Coût quasi nul (même run que le nightly suivant, avancé).

### Pourquoi trunk-based plutôt que GitFlow

- **Alignement continuous delivery.** GitFlow (`develop`/`release/*`/`hotfix/*`) est conçu
  pour des **trains de release versionnés en parallèle** (plusieurs versions publiques
  maintenues simultanément). Ce projet livre **une seule ligne de produit en continu** :
  l'appareil de GitFlow est du poids mort. L'auteur même de GitFlow recommande aujourd'hui le
  trunk-based pour les produits livrés en continu.
- **Pas de branche `develop` à maintenir.** `main` est le point d'intégration unique — même
  propriété de sérialisation qu'une branche `develop`, sans la dualité `develop`/`main` ni le
  **double-merge hotfix** (le foot-gun classique de GitFlow : oublier de reporter un hotfix
  dans `develop` réintroduit la régression à la release suivante).
- **Environnements = cibles de déploiement, pas branches.** GitHub Environments portent
  l'approbation manuelle (required reviewers), les protections et les secrets — ce que GitFlow
  simulait maladroitement avec des branches longues.
- **Contention de staging résolue sans branche dédiée.** Staging se déploie depuis `main`
  (source unique) ; les PR se sérialisent au merge comme dans n'importe quel repo. Besoin de
  valider avant merge ? **Preview environments éphémères par PR** — sans polluer la topologie
  de branches.

> **Alternative écartée — GitFlow simplifié (`develop` + `main` + `hotfix/*`).** Envisagé,
> puis écarté : il résout une « contention de staging » qui n'existe qu'avec un staging par
> PR (problème auto-infligé), au prix d'une branche permanente et du double-merge hotfix. Le
> trunk-based obtient le même résultat (staging sérialisé, release stabilisée) avec moins de
> surface d'erreur.

### Coût par étape (modèle 3 vitesses, inchangé)

Les 3 suites (DeepEval) coûtent des appels LLM — pas question de les lancer à chaque commit.

| Déclencheur                | Fréquence     | Tests exécutés                                             | Coût                    |
| -------------------------- | ------------- | --------------------------------------------------------- | ----------------------- |
| Push sur `feature/*`       | Très fréquent | Lint + tests unitaires                                    | Gratuit                 |
| Merge dans `main`          | Fréquent      | Build + déploiement staging auto (pas de suites LLM)      | Build seul              |
| Tag semver (release)       | Rare          | 3 suites LLM contre staging + approbation manuelle        | Cher, mais peu fréquent |
| Promotion production       | Rare          | Aucun rebuild — promotion de l'artefact validé            | Quasi gratuit           |
| Nightly (complet)          | **Bihebdomadaire** (1 lundi sur 2, parité semaine ISO) | 3 suites contre le tag production | Cher, mais pas gaspillé sur une version inchangée |
| Nightly (drift modèle)     | **Conditionnel** (déploiement Azure changé de version) | Seulement la/les suite(s) touchée(s) | Quasi gratuit — rare et ciblé |
| `hotfix/*`                 | Exceptionnel  | Garde-fous + mémoire (bloquant) + Qualité en async après promotion | Réduit, prioritaire     |

> **Pourquoi le nightly n'est pas quotidien inconditionnellement ?** Rejouer les 3 suites
> (dont DeepEval en N=5 runs, cf. M4) chaque nuit sur une version qui n'a pas bougé dépense un
> budget Azure pour zéro information nouvelle. Un tag est déjà passé par le gate complet de
> `quality.yml` à la fusion — le rejouer la nuit même sur un code strictement identique n'apporte
> aucune information nouvelle, donc pas de branche "1ʳᵉ nuit après un tag" : sur une version
> stable, un cadencement **bihebdomadaire** (au lieu d'hebdomadaire) suffit à surveiller une
> dérive lente (dérive du code de l'agent/des suites elles-mêmes).
>
> **Et la dérive du modèle Azure lui-même (le provider change le déploiement sous le même nom) ?**
> Ce n'est pas ce que détecte le cadencement bihebdomadaire — un job séparé
> (`check-model-drift`, cf. `.github/workflows/nightly.yml`) relève chaque nuit la version
> réelle des 3 déploiements (`Mistral-Large-3`, `gpt-5-mini`, `claude-opus-4-5` via Azure AI
> Foundry) et ne déclenche que la/les suite(s) qui dépendent du modèle ayant changé
> (`quality`+`memory` pour Mistral/Claude, `guardrails` pour gpt-5-mini) — jamais les 3
> systématiquement. Ce run ciblé ne passe pas par le gate de livraison (`EvalRun`, schéma à 3
> notes NOT NULL) : c'est un diagnostic, pas une décision de mise en production (voir
> `src/velmo/mlops/drift_check.py`).

---

## Rapport de suivi (`mlops/report.md`) — contrat

Le rapport n'est pas de la prose libre : il a un **contrat**, pour être lisible par un humain
**et** parsable par un dashboard/CI.

- **Sections obligatoires** (M3) : note mémoire, taux de blocage (garde-fous), taux de faux
  positifs, latence (p50/p95 par composant), coût par conversation. Plus : note globale
  (tendance), `gate_passed`, `version_tag`, delta vs version précédente.
- **Sidecar machine-lisible** : un `mlops/report.json` (mêmes chiffres, structuré) émis à côté
  du `.md` — c'est lui que la CI et un éventuel dashboard consomment, pas le Markdown.
- **Source** : le dernier `EVAL_RUN` + `EVAL_CASE_RESULT` de la version. Pointeur
  `langfuse_trace_url` inclus pour le drill-down, mais toutes les valeurs viennent de Postgres.

| Signal                         | Source                                          | Pourquoi il figure |
| ------------------------------ | ----------------------------------------------- | ------------------ |
| Note mémoire                   | `EVAL_RUN.note_memory`                          | Exigé (M3)         |
| Taux de blocage garde-fous     | `EVAL_RUN.block_rate` + échantillon trafic réel | CI = rappel sur cas connus ; trafic = catégories mal couvertes |
| Taux de faux positifs          | `EVAL_RUN.false_positive_rate`                  | Équilibre sécurité/utilité (Ch.2) |
| Latence p50/p95 par composant  | `EVAL_RUN.latency_*` (instrumentation locale)   | Identifier le composant qui dérape |
| Coût par conversation          | `EVAL_RUN.cost_per_conv` (instrumentation)      | Coût cumulé des appels LLM/API |

**Déclenchement du rollback.** Le monitoring nightly compare la note prod à la baseline : si
une dimension gate passe sous son seuil **deux nuits consécutives** (filtrer un flake isolé),
une alerte est levée → décision humaine de rollback (re-promotion du tag précédent). Le
critère est explicite, pas « à l'appréciation ».

---

## Gouvernance des fixtures (golden-set) & RGPD

- **Anti-overfitting.** Le prompt de l'agent ne doit **pas** être tuné sur le jeu d'éval,
  sinon les notes mesurent la mémorisation du test, pas la qualité. Bonne pratique :
  **hold-out** (un sous-ensemble de cas jamais montré pendant le tuning) + refresh périodique
  des fixtures.
- **Revue des cas.** Tout ajout/modif de fixture passe en PR (les fixtures sont du code) :
  une régression de note doit pouvoir s'expliquer par un changement d'agent, pas par un
  changement de cas silencieux.
- **PII dans les fixtures (RGPD).** `guardrail_cases.jsonl` contient des insultes/contenus
  sensibles et `memory_cases.jsonl` des données de type personnel (adresses, noms). Ce sont
  des données **synthétiques** — à documenter comme telles. Projet pédagogique : aucune
  **vraie** conversation client ne transite par l'agent, donc aucune vraie PII dans les traces
  Langfuse Cloud non plus — d'où l'usage du Cloud (EU) plutôt que self-host (§Observabilité).
  Si ce projet traitait un jour de vraies conversations client, revenir au self-host,
  rétention limitée, accès restreint, anonymisation où possible, et inscrire au registre de
  traitement RGPD.

---

## Couverture des tests d'acceptance

| Test d'acceptance fourni                                                                         | Mécanisme                                                                                  |
| ------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------- |
| Les trois suites produisent une note globale + notes mémoire/garde-fous/qualité, versionnées     | `EVAL_RUN` (FK `AGENT_VERSION`) ; `note_globale` (reporting) + `global_gate` (M1)          |
| Une régression (mémoire désactivée, garde-fou retiré) fait chuter la note et bloque la livraison | `global_gate = min(dims)` → une dim effondrée fait chuter le min → `gate_passed=false` (M2) |
| `mlops/report.md` expose note mémoire, taux de blocage, faux positifs, latence, coût             | Génération depuis le dernier `EVAL_RUN` + contrat de rapport (M3)                          |
| Ne pas bloquer pour du bruit                                                                      | Non-régression statistique qualité (`2σ`) + retry tracé mémoire/garde-fous (M4)           |

---

## Décisions de conception

| Question                                                    | Décision                                                                                                            | Pourquoi |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ | -------- |
| Bloquer sur la note globale ou par dimension ?              | **`global_gate = min(dims)`** avec plancher unique ; `note_globale` (moyenne) sert au reporting seul               | Une moyenne masque une régression isolée (contraire à M2) ; le `min` préserve le « par dimension » **et** l'API scalaire du test. |
| Stockage des notes : fichier ou base ?                      | **PostgreSQL** (`agent_version` + `eval_run` + `eval_case_result`), append-only forcé, migrations Alembic          | Interrogeable (tendance, comparaison), détail par cas pour diagnostiquer M2, immutabilité garantie côté base. |
| Latence/coût : où sont-ils calculés ?                       | **Instrumentation locale → Postgres** (ce qui gate) ; Langfuse pour le drill-down seulement                        | Le gate ne doit dépendre d'aucun service tiers ; la donnée de décision reste dans une seule source de vérité. |
| Absorber la variance du LLM-juge sans bloquer pour du bruit | Qualité en **delta ± 2σ** (N=5 runs) ; mémoire/garde-fous en seuil absolu + retry tracé                            | Seule la qualité est un jugement bruité ; R1–R6/G1–G7 restent vérifiables exactement. |
| `EVAL_RUN` par version ou par exécution ?                   | **Par exécution** (1 version → N runs)                                                                              | Retry sur flake + nightly sans polluer l'identité de version. |
| Moteur de métriques qualité : fait main ou biblio ?         | **DeepEval, cadré** : qualité seule, juge `claude-opus-4-5` pinné (Azure AI Foundry), rubriques versionnées, R1 hors gate | Métriques calibrées sans réinventer un juge ; mais on ne laisse pas une exigence non négociable dépendre d'un score flou. |
| Observabilité : quel outil, quel rôle ?                     | **Langfuse Cloud (EU)**, derrière `ObservabilitySink`, **hors chemin de gate**                                     | Projet pédagogique (pas de vraie PII client) → Cloud EU pour un setup rapide ; interface `ObservabilitySink` laisse la porte ouverte à un `LangfuseSink` self-host si le projet traite un jour de vraies données ; découplage strict pour que Langfuse down ne casse ni CI ni gate. |
| Identité de version : outil tiers ou git ?                  | **Hash git** des fichiers (prompt + configs) ; tag semver au release                                               | Git est déjà la source de vérité du prompt ; l'identité ne doit pas dépendre d'un service up. |
| Stratégie de branches ?                                     | **Trunk-based** : `main` toujours livrable + `feature/*` courtes + **tags semver** + **GitHub Environments**       | Aligné continuous delivery ; supprime `develop` et le double-merge hotfix ; environnements = cibles de déploiement, pas branches. |
| Environnement de test avant la prod ?                       | **Oui** : `main`→staging en continu, suites rejouées à la release, approbation via Environment `production`        | `main` reste toujours livrable ; on ne promeut en prod qu'un artefact validé en conditions réelles. |
| La promotion en prod redéploie-t-elle ?                     | **Non** : promotion de l'artefact taggé déjà validé sur staging                                                    | Revalider après coup serait redondant — c'est bit pour bit le même artefact. |
| Rollback ?                                                  | **Re-promotion du tag précédent** (artefacts immuables)                                                            | Tags immuables ⇒ rollback = changement de cible quasi instantané, pas un nouveau build. |
| Robustesse du harness ?                                     | **Échec infra non compté** (`error_kind`), runs partiels non notés, retries tracés                                 | Un timeout Azure ne doit jamais être compté comme une régression agent (cf. incidents Ch.1). |
| Gates non-fonctionnels ?                                    | **Latence p95 et coût/conv peuvent bloquer** (SLO)                                                                 | Un changement qui double la latence ou le coût ne doit pas passer en silence. |
| Tarif Azure pour le calcul du coût : codé en dur ou config ? | **Table de tarifs dans la config versionnée**, vérification périodique                                             | Un tarif figé dérive silencieusement (Azure change ses prix) ; hashé dans la version comme le reste. |
| Nightly : quotidien systématique ou conditionnel ?          | **Bihebdomadaire** (3 suites) + **drift modèle** (suite(s) touchée(s) seulement, si un déploiement Azure a changé de version) | Rejouer 3 suites LLM chaque nuit sur une version inchangée dépense un budget Azure pour zéro information nouvelle ; un tag est déjà passé par le gate complet à la fusion. |
| Hotfix : la suite Qualité est-elle simplement sautée ?      | **Sautée du gate, mais rejouée en async non bloquant juste après promotion**                                       | Un correctif urgent peut dégrader le ton/la pertinence sans que le gate (garde-fous + mémoire seulement) ne le détecte. |
| Approbation production : combien de reviewers ?             | **Un seul (SPOF assumé)** en équipe solo, documenté explicitement                                                  | Pas de système d'astreinte à construire pour une personne ; 2ᵉ reviewer ajouté dès qu'une 2ᵉ personne rejoint le projet. |

---

## Alternatives écartées (synthèse du challenge des outils)

| Outil envisagé                          | Écarté / cadré au profit de                                              | Raison |
| --------------------------------------- | ------------------------------------------------------------------------ | ------ |
| Langfuse comme **backbone** (Prompt Mgmt = version, Dataset Runs) | Langfuse **observabilité seule**, versioning git, gate en Postgres | Ne pas coupler l'identité de version ni le blocage CI à un service tiers up. |
| **LangSmith** (LangChain-natif)         | **Langfuse Cloud (EU)**                                                   | Langfuse propose une région EU explicite et une trajectoire self-host si le projet passe un jour en vraie prod (RGPD, conversations client = PII) ; LangSmith est SaaS-first sans cette option, moins pertinent même pour un usage pédagogique. |
| **DeepEval** pour tout (dont R1)        | DeepEval **qualité seule**, R1 déterministe                              | Une exigence mémoire non négociable ne peut pas dépendre d'une métrique LLM flaky. |
| Juge **fait main** pour la qualité      | **DeepEval** (cadré)                                                      | Métriques déjà calibrées (G-Eval, faithfulness) — inutile de réinventer. |
| **GitFlow simplifié** (`develop`+`hotfix/*`) | **Trunk-based** + tags + Environments                               | Pas de trains de release parallèles ici ; supprime `develop` et le double-merge hotfix. |
| Latence/coût **lus depuis Langfuse**    | **Recalculés localement** dans `eval_run`                                | Ce qui gate ne doit pas dépendre d'un service externe. |
| Bloquer sur `note_globale`              | Bloquer sur **`min(dims)`**                                              | La moyenne peut compenser une régression isolée (contraire à M2). |

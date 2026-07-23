# Partie 3 — CI/CD GitHub Actions (gate qualité, releases, nightly)

> **Place dans le parcours** : après **Partie 1** (`tuto_dev_local.md`) et **Partie 2**
> (`tuto_azure_deploiement.md` — les ressources existent, les secrets sont dans **Key
> Vault**). Ici : configurer GitHub pour que la CI protège `main`, que les releases passent
> par le gate qualité + une approbation humaine, et que la nightly surveille la dérive des
> modèles. **Les secrets GitHub se remplissent depuis Key Vault** (§5) — jamais retapés.
>
> Constat de départ (2026-07-22, repo `sofiane-git/velmo-v2`) : `main` non protégée, aucun
> Environment — ce doc configure tout.
>
> **Format de chaque étape** : **But** → **Terminal** (`gh`/`az`) → **Vérifie** → **Portail**
> (github.com / portal.azure.com).

## Prérequis

| Quoi | Vérifie |
|---|---|
| `gh` CLI authentifié **admin du repo** | `gh auth status` |
| `az` CLI authentifié, **admin du tenant Entra ID** (pour l'OIDC §4) | `az account show` |
| Partie 2 terminée (3 déploiements + Key Vault rempli) | checklist de sortie Partie 2 |
| Droits d'écriture Secrets/Variables Actions du repo | onglet Settings du repo visible |

---

## 0. Concepts de base (si CI/CD est nouveau pour toi)

- **CI (Continuous Integration)** : à chaque changement, une machine (pas toi) réinstalle le
  projet et fait tourner les vérifications. Ça casse ? Tu le sais **avant** `main`. Ici :
  `quality.yml`.
- **CD (Continuous Delivery)** : amener le code validé jusqu'en prod (automatiquement ou
  avec un clic). Ici : `release.yml`.
- **Workflow** : un YAML dans `.github/workflows/` — un déclencheur (`on:`) + des `jobs:`.
- **Tag semver** (`v1.2.3`) : étiquette sur un commit. Ici, pousser un tag `v*.*.*` **est**
  l'acte qui déclenche une release.
- **Environment GitHub** : cible de déploiement avec règles (« ce job ne part pas sans
  qu'un humain clique Approve »).
- **Branch protection** : règles sur `main` (pas de push direct, checks verts obligatoires).

## 1. Les 4 workflows du repo — vue d'ensemble

| Fichier | Déclencheur | Rôle | Bloquant ? |
|---|---|---|---|
| `quality.yml` | push `main`, toute PR | ruff + format + mypy strict + import-linter + acceptance + gate qualité (mode dégradé déterministe) | **Oui** (avec §2) |
| `release.yml` | push tag `v*.*.*` | job `gate` : migrations Alembic + 3 suites **vrai modèle** contre le tag ; puis `approve-and-promote` : approbation humaine → GitHub Release | **Oui** + humain |
| `hotfix.yml` | push `hotfix/**` | suite réduite (mémoire + garde-fous), score MLOps informatif | Non |
| `nightly.yml` | cron 3h UTC + manuel | `check-model-drift` (OIDC : lit les versions des 3 déploiements Azure, rejoue les suites touchées) + `scheduled-eval` (3 suites, un lundi sur deux) | Non — signal |

> Les jobs Postgres (`release` gate, `nightly` ×2) exécutent `alembic upgrade head` avant
> toute évaluation (stamp-once des bases historiques) — Alembic est l'unique source du
> schéma sur Postgres.

---

## 2. Protéger `main`

**But :** personne ne push direct ; toute PR exige `quality` vert avant merge.

**Terminal :**

```bash
gh api -X PUT repos/sofiane-git/velmo-v2/branches/main/protection \
  -H "Accept: application/vnd.github+json" \
  --input - <<'EOF'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["quality"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null
}
EOF
```

> `"contexts": ["quality"]` = le nom du job dans `quality.yml`. Si l'onglet Actions affiche
> un autre libellé (ex. `quality / quality`), reprendre le nom exact affiché.

**Vérifie :**

```bash
gh api repos/sofiane-git/velmo-v2/branches/main/protection \
  --jq '.required_status_checks.contexts, .enforce_admins.enabled'
# → ["quality"]  puis  true
```

**Portail :** `github.com/sofiane-git/velmo-v2/settings/branches` → **Add branch protection
rule** → pattern `main` → cocher **Require status checks to pass before merging** → chercher
`quality` (n'apparaît que si le workflow a déjà tourné une fois) → **Create**.

> Solo : pas de « Require pull request reviews » (impossible de s'auto-approuver) — à activer
> dès qu'une 2ᵉ personne rejoint (décision SPOF actée dans `release.yml`).

---

## 3. Environment `production` + approbation manuelle

**But :** `approve-and-promote` (release) attend ton clic **Approve** au lieu de partir seul.

**Terminal :**

```bash
# 1. Créer l'environment
gh api -X PUT repos/sofiane-git/velmo-v2/environments/production

# 2. Te poser en required reviewer (ton id : gh api user --jq .id)
gh api -X PUT repos/sofiane-git/velmo-v2/environments/production \
  --input - <<'EOF'
{
  "reviewers": [
    { "type": "User", "id": 58562845 }
  ],
  "deployment_branch_policy": {
    "protected_branches": false,
    "custom_branch_policies": true
  }
}
EOF

# 3. Restreindre aux tags semver
gh api -X POST repos/sofiane-git/velmo-v2/environments/production/deployment-branch-policies \
  -f name='v*.*.*' -f type='tag'
```

**Vérifie :**

```bash
gh api repos/sofiane-git/velmo-v2/environments --jq '.environments[].name'
# → production
```

**Portail :** `settings/environments` → **New environment** → `production` → **Configure** →
cocher **Required reviewers** → ajouter `sofiane-git` → **Deployment branches and tags** →
`Selected branches and tags` → **Add deployment tag rule** → `v*.*.*` → **Save protection
rules**.

---

## 4. OIDC Azure (nightly lit les versions de modèles sans clé stockée)

**But :** `check-model-drift` s'authentifie à Azure en échangeant un token GitHub OIDC contre
un token Azure (federated credential) — **aucune clé Azure long-vécue** dans GitHub. Exige
l'admin du tenant (pas faisable via `gh`).

**Terminal :**

```bash
# 1. App registration (l'identité du workflow)
az ad app create --display-name "velmo-v2-github-actions" --query appId -o tsv
# → note APP_ID

# 2. Service principal
az ad sp create --id <APP_ID>

# 3. Lecture seule sur le groupe de ressources des 3 comptes (Partie 2)
az role assignment create --assignee <APP_ID> --role Reader \
  --scope /subscriptions/<SUBSCRIPTION_ID>/resourceGroups/<RG>

# 4. Federated credential : confiance aux tokens OIDC de CE repo, branche main
az ad app federated-credential create --id <APP_ID> --parameters '{
  "name": "velmo-v2-nightly-main",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:sofiane-git/velmo-v2:ref:refs/heads/main",
  "audiences": ["api://AzureADTokenExchange"]
}'
```

**Vérifie :**

```bash
az ad sp show --id <APP_ID> --query appId -o tsv          # → <APP_ID>
az role assignment list --assignee <APP_ID> -o table       # → Reader sur le RG
az ad app federated-credential list --id <APP_ID> --query '[].name' -o tsv
# → velmo-v2-nightly-main
```

**Portail :** `portal.azure.com` → « Microsoft Entra ID » → **App registrations** → **+ New
registration** → `velmo-v2-github-actions` → **Register**. Page de l'app → **Certificates &
secrets** → onglet **Federated credentials** → **+ Add credential** → scénario **GitHub
Actions deploying Azure resources** → Organization/Repository, `Entity type` = **Branch**,
branche `main` → **Add**. Noter sur **Overview** : `Application (client) ID` et `Directory
(tenant) ID`. Enfin, droits : RG → **Access control (IAM)** → **+ Add role assignment** →
**Reader** → sélectionner `velmo-v2-github-actions` → **Review + assign**.

---

## 5. Secrets & variables — remplis DEPUIS Key Vault (zéro copier-coller)

**But :** poser tout ce que les workflows consomment. **Source unique = Key Vault (Partie 2
§D)** — les valeurs ne transitent jamais par un presse-papier.

**Terminal :**

```bash
KV="kv-<suffix>"   # ton vault Partie 2

# Identité OIDC (§4)
gh secret set AZURE_CLIENT_ID       --body "<APP_ID>"
gh secret set AZURE_TENANT_ID       --body "$(az account show --query tenantId -o tsv)"
gh secret set AZURE_SUBSCRIPTION_ID --body "$(az account show --query id -o tsv)"

# Clés modèles + DB — tirées du coffre
for pair in \
  "AZURE_AI_INFERENCE_ENDPOINT azure-ai-inference-endpoint" \
  "AZURE_AI_INFERENCE_API_KEY azure-ai-inference-api-key" \
  "AZURE_OPENAI_GUARD_ENDPOINT azure-openai-guard-endpoint" \
  "AZURE_OPENAI_GUARD_API_KEY azure-openai-guard-api-key" \
  "ANTHROPIC_FOUNDRY_ENDPOINT anthropic-foundry-endpoint" \
  "ANTHROPIC_API_KEY anthropic-api-key" \
  "DB_URL db-url"; do
  set -- $pair
  gh secret set "$1" --body "$(az keyvault secret show --vault-name "$KV" --name "$2" --query value -o tsv)"
done

# Variables (non secrètes) : les NOMS de tes ressources Azure (Partie 2)
gh variable set AZURE_RESOURCE_GROUP        --body "<RG>"
gh variable set AZURE_AI_INFERENCE_ACCOUNT  --body "<aoai-…-chat>"
gh variable set AZURE_OPENAI_GUARD_ACCOUNT  --body "<aoai-…-guard>"
gh variable set AZURE_FOUNDRY_ACCOUNT       --body "<aif-…-async>"
```

**Vérifie :**

```bash
gh secret list     # → AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID,
                   #   AZURE_AI_INFERENCE_*, AZURE_OPENAI_GUARD_*, ANTHROPIC_*, DB_URL
gh variable list   # → AZURE_RESOURCE_GROUP + les 3 comptes
```

**Portail :** `settings/secrets/actions` → **New repository secret** (et onglet
**Variables**) — plus lent que la CLI, mêmes champs.

> `${{ github.token }}` reste le seul credential de `quality.yml`/`hotfix.yml` — ils
> n'appellent pas Azure. Sans les secrets ci-dessus, `release.yml`/`nightly.yml` **refusent
> de tourner** (garde anti-silence : plutôt échouer que d'évaluer le stub).

---

## 6. Le cycle quotidien (CI)

**But :** comprendre ce qui se passe à chaque PR — rien à configurer.

1. Branche `feature/...` → PR vers `main`.
2. `quality.yml` part seul (visible sous la PR et dans **Actions**).
3. Rouge ? → clic sur le run → le step en échec affiche le log → corrige → repush (relance
   automatique).
4. Vert + §2 en place → le bouton **Merge** se débloque seul.

**Terminal (suivi sans navigateur) :**

```bash
gh run list --workflow=quality.yml --limit 5
gh run watch                      # suit le run en cours
gh run view --log-failed          # logs des seuls steps en échec
```

**Équivalent local avant push :** `make ci` (même chaîne, même ordre — Partie 1 §6).

---

## 7. Faire une release

### 7.1 Ce que tu n'as PAS à faire

Pas de bump de `version` dans `pyproject.toml` : l'identité de version est calculée
(`mlops/versioning.py` — hash du prompt, des configs mémoire/garde-fous/gate +
`git describe --tags`). Le champ pyproject est un artefact PyPI décorrélé.

### 7.2 Poser et pousser le tag

**Terminal :**

```bash
git checkout main && git pull
git tag -a v2.1.0 -m "Release 2.1.0"
git push origin v2.1.0
```

**Vérifie :**

```bash
git ls-remote --tags origin v2.1.0
# → le hash du commit taggé
```

### 7.3 Suivre le gate puis approuver

**Terminal :**

```bash
gh run list --workflow=release.yml --limit 1
gh run watch          # suit le job `gate`
```

- `gate` **échoue** (score < 0.80 sur le tag) → la release s'arrête là. Corriger, supprimer
  le tag (`git push --delete origin v2.1.0 && git tag -d v2.1.0`), retagger.
- `gate` **passe** → `approve-and-promote` affiche **Waiting**.

**Approuver — portail (le plus simple) :** page du run → bandeau **Review pending
deployments** → cocher `production` → **Approve and deploy**.

**Approuver — terminal :**

```bash
RUN_ID=$(gh run list --workflow=release.yml --limit 1 --json databaseId --jq '.[0].databaseId')
ENV_ID=$(gh api repos/sofiane-git/velmo-v2/actions/runs/$RUN_ID/pending_deployments \
  --jq '.[0].environments[0].id')
# -F (pas -f) : environment_ids attend un tableau d'entiers — -f enverrait une string → 422.
gh api -X POST repos/sofiane-git/velmo-v2/actions/runs/$RUN_ID/pending_deployments \
  -F "environment_ids[]=$ENV_ID" -f state=approved -f comment=ok
```

### 7.4 Après l'approbation — et ce qui n'existe pas (encore)

`approve-and-promote` crée une **GitHub Release** (onglet Releases). **Aucun déploiement
applicatif** (pas de redémarrage, pas de promotion d'image) : l'hébergement n'est pas
tranché — décision documentée. Une release = qualité validée + trace, pas une mise en ligne.
Ne pas inventer cette étape avant de trancher l'hébergement (le jour venu : voir Partie 2 §D3
pour l'accès Key Vault de l'hôte).

---

## 8. Hotfix

**But :** urgence sans le cycle complet.

**Terminal :**

```bash
git checkout -b hotfix/nom-du-bug main
# ... fix ...
git push origin hotfix/nom-du-bug
```

**Vérifie :**

```bash
gh run list --workflow=hotfix.yml --limit 1
# → un run sur ta branche hotfix/...
```

Suite réduite (mémoire + garde-fous bloquants, score informatif). Une fois mergé dans
`main` : retagger normalement (§7).

---

## 9. Nightly — rien à faire au quotidien

Chaque nuit 3h UTC, deux jobs indépendants :

- **`check-model-drift`** : login OIDC (§4) → lit `model.version` des 3 déploiements
  (`Mistral-Large-3`, `gpt-5-mini`, `claude-opus-4-5`) → compare à
  `.github/state/model-versions.json` → si un provider a changé un modèle sous tes pieds,
  rejoue **la/les suite(s) concernée(s)** (`quality,memory` pour Mistral/Claude,
  `guardrails` pour gpt-5-mini) via `velmo.mlops.drift_check` — exit 1 sous le plancher.
- **`scheduled-eval`** : les 3 suites complètes, un lundi sur deux (parité de semaine ISO).

**Terminal (déclenchement manuel / suivi) :**

```bash
gh workflow run nightly.yml
gh run list --workflow=nightly.yml --limit 3
```

---

## 10. Limites connues (à ne pas découvrir en prod)

- **Un seul reviewer** sur `production` (toi) — SPOF assumé en solo, documenté ; à étendre
  dès une 2ᵉ personne.
- **Pas de déploiement applicatif** — §7.4.
- **`enforce_admins: true`** (§2) : même toi ne bypasses pas `main`. Besoin exceptionnel de
  push direct → désactiver temporairement la règle, jamais `--force`.

---

# Récapitulatifs

## Secrets & variables GitHub — la table complète

| Nom | Type | Origine | Consommé par |
|---|---|---|---|
| `AZURE_CLIENT_ID` | Secret | App ID (§4) | `nightly.yml` (OIDC) |
| `AZURE_TENANT_ID` | Secret | `az account show` (§4) | `nightly.yml` (OIDC) |
| `AZURE_SUBSCRIPTION_ID` | Secret | `az account show` (§4) | `nightly.yml` (OIDC) |
| `AZURE_AI_INFERENCE_ENDPOINT` / `_API_KEY` | Secret | **Key Vault** (§5) | `release.yml`, `nightly.yml` |
| `AZURE_OPENAI_GUARD_ENDPOINT` / `_API_KEY` | Secret | **Key Vault** | `release.yml`, `nightly.yml` |
| `ANTHROPIC_FOUNDRY_ENDPOINT` / `ANTHROPIC_API_KEY` | Secret | **Key Vault** | `release.yml`, `nightly.yml` |
| `DB_URL` | Secret | **Key Vault** (`db-url`) | `release.yml`, `nightly.yml` (baseline + migrations) |
| `AZURE_RESOURCE_GROUP` | Variable | nom du RG (Partie 2) | `nightly.yml` |
| `AZURE_AI_INFERENCE_ACCOUNT` | Variable | nom ressource B1 | `nightly.yml` |
| `AZURE_OPENAI_GUARD_ACCOUNT` | Variable | nom ressource B2 | `nightly.yml` |
| `AZURE_FOUNDRY_ACCOUNT` | Variable | nom ressource B3 | `nightly.yml` |

## Checklist de sortie de la Partie 3

- [ ] §2 — `main` protégée (`["quality"]`, `enforce_admins: true`)
- [ ] §3 — Environment `production` + required reviewer + tag rule `v*.*.*`
- [ ] §4 — app registration + SP + Reader + federated credential (`velmo-v2-nightly-main`)
- [ ] §5 — `gh secret list` / `gh variable list` complets (table ci-dessus)
- [ ] §6 — une PR de test : `quality` apparaît et bloque le merge tant que rouge
- [ ] §7 — un tag de test : `gate` tourne (vrai modèle), `approve-and-promote` attend ton clic
- [ ] §9 — `gh workflow run nightly.yml` : login OIDC OK, versions relevées

**Fin du parcours** — dev local ✅ (Partie 1), infra + coffre ✅ (Partie 2), CI/CD ✅
(Partie 3). L'app est gouvernée du commit à la release.

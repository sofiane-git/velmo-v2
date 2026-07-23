# Tutoriel — CI/CD GitHub Actions et releases de Velmo 2.0

> **Portée.** Ce tutoriel explique, pour quelqu'un qui découvre CI/CD et GitHub Actions,
> **comment ça marche déjà dans ce repo** (`.github/workflows/*.yml`) et **comment finir de
> configurer GitHub** pour que ça fonctionne comme prévu dans
> `docs/job/conceptions/conception_chantier3_evaluation_mlops.md` (§Boucle qualité). Constat
> fait le 2026-07-22 sur le repo `sofiane-git/velmo-v2` : **`main` n'est pas protégée** et
> **aucun GitHub Environment n'existe** — donc l'approbation manuelle prévue avant prod n'est
> pas encore active. Ce doc corrige ça.
>
> **Deux chemins équivalents** pour la config GitHub : **CLI** (`gh`, scriptable) et
> **portail** (`github.com/sofiane-git/velmo-v2/settings`). Les deux configurent exactement la
> même chose.

---

## Prérequis

Avant de suivre ce tutoriel, avoir sous la main :

- **`gh` CLI** authentifié en **admin du repo** (`gh auth login`, scope `repo` + `admin:org`
  si org) — pour la branch protection et les Environments (§2.1, §2.2).
- **`az` CLI** authentifié (`az login`) avec un **accès admin au tenant Azure AD** — l'OIDC
  (§2.3) crée une app registration + service principal + federated credential, impossible via
  `gh`, et le role assignment exige un rôle Owner/User Access Administrator sur le groupe de
  ressources.
- Les 3 déploiements Azure déjà créés (voir `tuto_azure_deploiement.md`) : `Mistral-Large-3`,
  `gpt-5-mini`, `claude-opus-4-5`.
- Droits d'écriture sur les **Secrets/Variables Actions** du repo.

---

## 0. Concepts de base (si CI/CD est nouveau pour toi)

- **CI (Continuous Integration)** : à chaque changement de code, une machine (pas toi)
  réinstalle le projet et fait tourner les tests. Si ça casse, tu le sais avant que ça arrive
  sur `main`. Ici : `quality.yml`.
- **CD (Continuous Delivery/Deployment)** : une fois le code validé, l'amener automatiquement
  (ou avec un clic) jusqu'en prod. Ici : `release.yml`.
- **Workflow** = un fichier YAML dans `.github/workflows/`. Chaque workflow a un déclencheur
  (`on:`) et un ou plusieurs `jobs:` (suites d'étapes qui tournent sur une machine GitHub
  jetable).
- **Tag semver** (`v1.2.3`) = une étiquette posée sur un commit précis. Dans ce repo, poser un
  tag `v*.*.*` et le pousser est **l'action qui déclenche une release** (`release.yml`).
- **Environment GitHub** = une cible de déploiement (`production`, `staging`...) sur laquelle
  tu peux poser des règles : "ce job ne démarre pas sans qu'un humain clique Approve".
- **Branch protection** = des règles sur `main` : interdiction de push direct, obligation que
  les checks CI soient verts avant de merger une PR.

## 1. Les 4 workflows existants — vue d'ensemble

| Fichier | Déclencheur | Rôle | Bloquant ? |
|---|---|---|---|
| `quality.yml` | push sur `main`, ou toute PR | Installe le projet (`uv sync`), vérifie les contrats d'imports, lance `tests/acceptance/`, puis `velmo.mlops.score --min-score 0.8` | **Oui** — si un check échoue, la PR ne doit pas être mergeable (voir §3) |
| `release.yml` | push d'un tag `v*.*.*` | Job `gate` : rejoue les 3 suites contre le tag. Job `approve-and-promote` : attend une approbation manuelle sur l'Environment `production`, puis crée une **GitHub Release** | **Oui** pour le gate ; approbation humaine ensuite |
| `hotfix.yml` | push sur `hotfix/**` | Suite réduite (mémoire + garde-fous seulement), non bloquante pour le score global | Non (garde-fous restent bloquants, le score MLOps est informatif) |
| `nightly.yml` | cron 3h UTC + déclenchable à la main | 2 déclencheurs indépendants : `check-model-drift` (relève la version des 3 déploiements Azure, rejoue seulement la/les suite(s) touchée(s) si ça a bougé) + `scheduled-eval` (3 suites, un lundi sur deux) | Non — informatif |

## 2. Configurer GitHub une fois (ce qui manque aujourd'hui)

### 2.1 Protéger `main`

Objectif : personne ne push direct sur `main`, toute PR doit avoir `quality` vert avant merge.

**Via `gh` CLI** :

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

`"contexts": ["quality"]` correspond au nom du job dans `quality.yml` (`jobs.quality`). Si
GitHub affiche un nom différent dans l'onglet Actions (ex. `quality / quality`), reprends le
nom exact affiché — il doit matcher pour que la règle s'applique.

**Via le portail** : `github.com/sofiane-git/velmo-v2/settings/branches` → **Add branch
protection rule** → `Branch name pattern` = `main` → cocher **Require status checks to pass
before merging** → chercher `quality` dans la liste (elle n'apparaît que si le workflow a déjà
tourné au moins une fois) → **Create**.

Vérifie :

```bash
gh api repos/sofiane-git/velmo-v2/branches/main/protection --jq '.required_status_checks.contexts, .enforce_admins.enabled'
# → doit afficher: ["quality"]  puis  true
```

> Solo aujourd'hui donc pas de "Require pull request reviews" obligatoire (tu ne peux pas
> t'auto-approuver une PR) — à activer dès qu'une 2e personne rejoint, cf. décision SPOF déjà
> actée dans `release.yml`.

### 2.2 Créer l'Environment `production` avec approbation manuelle

Objectif : le job `approve-and-promote` de `release.yml` ne parte pas tout seul — il attend
que tu cliques "Approve" dans l'onglet Actions.

**Via `gh` CLI** (en deux temps — créer l'environment, puis poser le required reviewer) :

```bash
# 1. Crée l'environment (vide, sans règle) s'il n'existe pas déjà
gh api -X PUT repos/sofiane-git/velmo-v2/environments/production

# 2. Ajoute-toi comme required reviewer (id récupéré via `gh api user --jq .id`)
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

# 3. Restreint le déploiement aux tags semver uniquement (pas n'importe quelle branche)
gh api -X POST repos/sofiane-git/velmo-v2/environments/production/deployment-branch-policies \
  -f name='v*.*.*' -f type='tag'
```

**Via le portail** : `github.com/sofiane-git/velmo-v2/settings/environments` → **New
environment** → nom `production` → **Configure environment** → cocher **Required reviewers**
→ ajouter `sofiane-git` → section **Deployment branches and tags** → `Selected branches and
tags` → **Add deployment tag rule** → pattern `v*.*.*` → **Save protection rules**.

Une fois fait, vérifie :

```bash
gh api repos/sofiane-git/velmo-v2/environments --jq '.environments[].name'
# → doit afficher: production
```

### 2.3 OIDC Azure AD (pour `check-model-drift` de `nightly.yml`)

Objectif : le job `check-model-drift` lit la version des 3 déploiements Azure
(`Mistral-Large-3`, `gpt-5-mini`, `claude-opus-4-5`) sans stocker de secret client long-vécu —
`azure/login@v2` échange un token GitHub OIDC contre un token Azure via une **federated
credential**. Nécessite l'accès admin sur le tenant Azure AD (pas faisable via `gh`, seulement
via `az`).

```bash
# 1. App registration dédiée (identité utilisée par le workflow, pas un utilisateur)
az ad app create --display-name "velmo-v2-github-actions" --query appId -o tsv
# → note l'APP_ID retourné

# 2. Service principal pour cette app
az ad sp create --id <APP_ID>

# 3. Droit lecture seule sur le groupe de ressources des 3 comptes Cognitive Services
az role assignment create --assignee <APP_ID> --role Reader \
  --scope /subscriptions/<SUBSCRIPTION_ID>/resourceGroups/sconanRG

# 4. Federated credential : fait confiance aux tokens OIDC émis par GitHub Actions
#    pour ce repo, sur la branche main (déclencheur schedule/workflow_dispatch de
#    nightly.yml tourne dans le contexte de la branche par défaut)
az ad app federated-credential create --id <APP_ID> --parameters '{
  "name": "velmo-v2-nightly-main",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:sofiane-git/velmo-v2:ref:refs/heads/main",
  "audiences": ["api://AzureADTokenExchange"]
}'
```

Vérifie les 4 étapes :

```bash
az ad sp show --id <APP_ID> --query appId -o tsv
# → doit renvoyer <APP_ID> (le service principal existe)

az role assignment list --assignee <APP_ID> -o table
# → doit lister Reader sur .../resourceGroups/sconanRG

az ad app federated-credential list --id <APP_ID> --query '[].name' -o tsv
# → doit afficher: velmo-v2-nightly-main
```

Puis pose les 3 secrets (le seul chemin, pas d'équivalent portail plus rapide que
`Settings → Secrets and variables → Actions → New repository secret`) :

```bash
gh secret set AZURE_CLIENT_ID --body "<APP_ID>"
gh secret set AZURE_TENANT_ID --body "$(az account show --query tenantId -o tsv)"
gh secret set AZURE_SUBSCRIPTION_ID --body "$(az account show --query id -o tsv)"
```

Vérifie :

```bash
gh secret list
# → doit afficher AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID
```

### 2.4 Secrets et variables — récapitulatif

| Nom | Type | Valeur | Posé ? |
|---|---|---|---|
| `AZURE_CLIENT_ID` | Secret | App ID (§2.3) | À faire — nécessite accès tenant |
| `AZURE_TENANT_ID` | Secret | Tenant ID (§2.3) | À faire |
| `AZURE_SUBSCRIPTION_ID` | Secret | Subscription ID (§2.3) | À faire |
| `AZURE_RESOURCE_GROUP` | Variable | `sconanRG` | ✅ posé |
| `AZURE_AI_INFERENCE_ACCOUNT` | Variable | `sconanext-8976-resource` | ✅ posé |
| `AZURE_OPENAI_GUARD_ACCOUNT` | Variable | `sconanext-8458-resource` | ✅ posé |
| `AZURE_FOUNDRY_ACCOUNT` | Variable | `sconanext-7665-resource` | ✅ posé |

`${{ github.token }}` reste le seul credential utilisé par `quality.yml`/`release.yml`/
`hotfix.yml` — ces 3 workflows n'appellent pas Azure directement.

## 3. Le cycle quotidien (CI)

1. Tu bosses sur une branche (`feature/...`), tu ouvres une PR vers `main`.
2. `quality.yml` se déclenche automatiquement (`on: pull_request`) — visible dans l'onglet
   **Actions** du repo, ou directement sous la PR ("Some checks haven't completed yet").
3. Si `quality` est rouge → clique sur le run → l'étape en échec affiche le log complet (ex.
   pytest qui liste le test cassé). Corrige, repush sur la même branche, ça relance seul.
4. Une fois vert et §2.1 configuré, GitHub bloque le bouton "Merge" jusqu'à ce que ce soit le
   cas — tu n'as plus besoin d'y penser.

Commande utile pour suivre depuis le terminal sans ouvrir le navigateur :

```bash
gh run list --workflow=quality.yml --limit 5
gh run watch                      # suit le run en cours en direct
gh run view --log-failed          # affiche uniquement les logs des étapes en échec
```

## 4. Faire une release

### 4.1 Ce que tu n'as **pas** besoin de faire

Pas besoin de monter la version dans `pyproject.toml` (`version = "2.0.0"`) — elle n'est pas
utilisée comme identité de version pour le gate. `src/velmo/mlops/versioning.py` calcule
l'identité réelle (hash du prompt, de la config mémoire, des seuils garde-fous +
`git describe --tags --exact-match` sur le tag qui déclenche `release.yml`). Le champ
`pyproject.toml` reste un artefact PyPI classique, décorrélé.

### 4.2 Poser et pousser le tag

```bash
git checkout main && git pull
git tag -a v2.1.0 -m "Release 2.1.0"
git push origin v2.1.0
```

Vérifie :

```bash
git ls-remote --tags origin v2.1.0
# → doit afficher le hash du commit taggé (le tag est bien sur le remote)
```

Ça déclenche `release.yml` (`on: push: tags: v*.*.*`) immédiatement.

### 4.3 Suivre le gate puis approuver

```bash
gh run list --workflow=release.yml --limit 1
gh run watch          # suit le job `gate`
```

Si `gate` échoue (score < 0.8 sur le tag) → **la release s'arrête là**, `approve-and-promote`
ne se lance jamais. Corrige, supprime le tag (`git push --delete origin v2.1.0 && git tag -d
v2.1.0`), retag une fois corrigé.

Si `gate` passe → `approve-and-promote` apparaît **"Waiting"** :

- **Portail** : `github.com/sofiane-git/velmo-v2/actions/runs/<id>` → bandeau jaune "Review
  pending deployments" → **Review deployments** → cocher `production` → **Approve and
  deploy**.
- **CLI** (le portail reste plus simple pour ce geste ponctuel) :

  ```bash
  RUN_ID=$(gh run list --workflow=release.yml --limit 1 --json databaseId --jq '.[0].databaseId')
  # Récupère l'id numérique de l'environment en attente :
  ENV_ID=$(gh api repos/sofiane-git/velmo-v2/actions/runs/$RUN_ID/pending_deployments \
    --jq '.[0].environments[0].id')
  # -F (pas -f) : environment_ids attend un tableau d'entiers, -f enverrait une string → 422.
  gh api -X POST repos/sofiane-git/velmo-v2/actions/runs/$RUN_ID/pending_deployments \
    -F "environment_ids[]=$ENV_ID" -f state=approved -f comment=ok
  ```

### 4.4 Ce qui se passe après l'approbation — et ce qui ne se passe pas encore

`approve-and-promote` crée une **GitHub Release** (visible sous l'onglet **Releases**) avec le
tag et une note. **Il n'y a pas de commande de déploiement applicatif** (pas de redémarrage de
service, pas de promotion d'image conteneur) — l'hébergement de l'app n'est pas encore choisi
(le commentaire dans `release.yml` le dit explicitement). Tant que ce choix n'est pas fait,
"faire une release" = valider qualité + créer la trace GitHub Release, pas mettre en ligne. Ne
pas inventer cette étape avant que l'hébergement soit tranché.

## 5. Hotfix — quand l'utiliser

Urgence en prod, tu ne veux pas attendre le cycle normal PR→main→tag :

```bash
git checkout -b hotfix/nom-du-bug main
# ... fix ...
git push origin hotfix/nom-du-bug
```

Vérifie :

```bash
gh run list --workflow=hotfix.yml --limit 1
# → doit afficher un run "in_progress" ou "completed" sur ta branche hotfix/...
```

`hotfix.yml` se déclenche sur `hotfix/**`, ne fait tourner que mémoire + garde-fous
(bloquant), le score MLOps tourne en informatif (`|| true`, jamais rouge le run). Une fois
mergé dans `main`, retague normalement (§4) pour repasser par le cycle complet.

## 6. Nightly — rien à faire (config Azure §2.3 une fois exceptée)

Tourne seul chaque nuit à 3h UTC, 2 jobs indépendants :

- **`check-model-drift`** : se connecte à Azure en OIDC (`azure/login@v2`, pas de secret client
  stocké), relève `model.version` sur les 3 déploiements (`Mistral-Large-3`, `gpt-5-mini`,
  `claude-opus-4-5`), compare à `.github/state/model-versions.json`. Si un provider a changé de
  version sous nos pieds (aucun diff de code ne le révèle), le job suivant (`drift-check-eval`)
  rejoue uniquement la/les suite(s) concernée(s) (`quality,memory` pour Mistral/Claude,
  `guardrails` pour gpt-5-mini) via `python -m velmo.mlops.drift_check` — pas le gate complet,
  juste un rapport (voir `src/velmo/mlops/drift_check.py`).
- **`scheduled-eval`** : rejoue les 3 suites en entier un lundi sur deux (parité de semaine ISO)
  — plus de branche "1re nuit après un tag" : un tag est déjà passé par le gate complet de
  `quality.yml` à la fusion, le rejouer la nuit même sur un code identique n'apporte rien.

Config Azure requise une fois (pas encore faite au 2026-07-22) : voir §2.3.

Déclenchable à la main si besoin :

```bash
gh workflow run nightly.yml
```

## 7. Limites connues (ne pas les découvrir en prod)

- **Un seul reviewer possible** sur `production` (toi) — SPOF assumé en solo, déjà documenté
  dans `conception_chantier3_evaluation_mlops.md`. À étendre dès qu'une 2e personne rejoint.
- **Pas de déploiement applicatif réel** — voir §4.4.
- **`enforce_admins: true`** dans §2.1 veut dire que même toi (admin du repo) ne peux pas
  bypasser la protection de `main` par accident. Si un jour tu as besoin d'un push direct
  d'urgence, il faudra désactiver temporairement la règle, pas la contourner avec `--force`.

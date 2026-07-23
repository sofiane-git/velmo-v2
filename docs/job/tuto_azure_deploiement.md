# Partie 2 — Infra Azure (provisionner le cloud et remplir le coffre)

> **Place dans le parcours** : après **Partie 1** (`tuto_dev_local.md`, app qui tourne en
> local), avant **Partie 3** (`tuto_github_actions_release.md`, CI/CD). À la fin de cette
> partie : toutes les ressources Azure existent, **tous les secrets sont dans Key Vault**
> (la source de vérité — ton `.env` local et les GitHub Secrets de la Partie 3 s'alimentent
> depuis lui).
>
> Ce tutoriel instancie les décisions des 3 docs de conception
> (`docs/job/conceptions/conception_chantier{1,2,3}_*.md`) — il donne le *comment*, pas le
> *pourquoi*.
>
> **Format de chaque étape** : **But** → **Terminal** (`az`, scriptable, à préférer) →
> **Vérifie** (commande + sortie attendue) → **Portail** (chemin clic-par-clic équivalent).
> La navigation du portail évolue : si un libellé a changé, la barre de recherche du portail
> reste le point d'entrée fiable.

## Prérequis

| Quoi | Vérifie |
|---|---|
| Abonnement Azure actif | `az account show --query name -o tsv` |
| `az` CLI authentifié | `az login` puis `az account show` |
| Droits **Contributor** sur l'abonnement/RG cible | `az role assignment list --assignee $(az ad signed-in-user show --query id -o tsv) -o table` |
| `openssl` (génération de mots de passe) | `openssl version` |

## Conventions — à poser une fois, réutilisées partout

```bash
export RG="rg-velmo-prod"                    # groupe de ressources
export LOCATION="francecentral"              # région UE — voir A2
export SUFFIX="velmo-prod"                   # suffixe unique de nommage
```

> Les noms sont des **exemples** : choisis les tiens, mais reporte-les partout (variables
> GitHub Actions en Partie 3 §5 — les valeurs `sconan*` qu'on y voit sont l'infra réelle de
> ce repo, pas un nom imposé).

---

# Phase A — Fondations

## A1. Groupe de ressources

**But :** le conteneur logique de tout ce qui suit (suppression/facturation groupées).

**Terminal :**

```bash
az group create --name "$RG" --location "$LOCATION"
```

**Vérifie :**

```bash
az group show --name "$RG" --query provisioningState -o tsv
# → Succeeded
```

**Portail :** `portal.azure.com` → recherche « Resource groups » → **+ Create** →
`Resource group` = `rg-velmo-prod`, `Region` = France Central → **Review + create** → **Create**.

## A2. Région : UE obligatoire

**But :** tout déploiement traitant du contenu client en clair (conversations = PII) reste en
**région UE** (`francecentral` ou `westeurope`) — cohérent avec Langfuse Cloud EU (§G3).

**Terminal (vérifier la dispo d'un service dans la région) :**

```bash
az cognitiveservices account list-skus --kind OpenAI --location "$LOCATION" -o table
```

**Portail :** au moment de créer une ressource (Phase B), le sélecteur `Region` ne propose que
les régions où le service est disponible — le filtre est fait pour toi. Vue comparative :
page « Products available by region » (azure.microsoft.com → Global Infrastructure).

---

# Phase B — Les modèles IA (3 ressources, 3 rôles)

> **Pourquoi 3 ressources séparées ?** Le juge garde-fous est sur le **chemin bloquant
> synchrone** (chaque message) ; l'extracteur mémoire et le juge DeepEval sont
> **asynchrones/best-effort**. Azure applique les quotas par déploiement, mais deux
> déploiements d'une même ressource peuvent partager la limite globale de throughput (TPM)
> selon les tiers — ressources séparées = isolation garantie. Et depuis la décision révisée
> (Ch.1), l'async est chez un **vendor distinct** (Anthropic via Foundry).

| # | Ressource | Modèle (nom de déploiement **exact**) | Rôle | Variable `.env` |
|---|---|---|---|---|
| B1 | `aoai-${SUFFIX}-chat` (kind AIServices) | `Mistral-Large-3` | agent principal | `AZURE_AI_INFERENCE_*` |
| B2 | `aoai-${SUFFIX}-guard` (kind OpenAI) | `gpt-5-mini` | juge garde-fous (bloquant) | `AZURE_OPENAI_GUARD_*` |
| B3 | `aif-${SUFFIX}-async` (kind AIServices) | `claude-opus-4-5` | extracteur mémoire + juge DeepEval | `ANTHROPIC_*` |

⚠️ Les **noms de déploiement** ci-dessus sont un **contrat** avec le code
(`.env.example`, `nightly.yml` drift-check) — ne pas improviser (`gpt-5-mini-guard`,
`mistral-large-3` minuscule : déjà des bugs par le passé).

## B1. Agent principal — Mistral-Large-3 (Azure AI Inference)

**But :** le LLM qui répond aux clients.

**Terminal :**

```bash
az cognitiveservices account create \
  --name "aoai-${SUFFIX}-chat" \
  --resource-group "$RG" \
  --location "$LOCATION" \
  --kind AIServices \
  --sku S0

az cognitiveservices account deployment create \
  --name "aoai-${SUFFIX}-chat" \
  --resource-group "$RG" \
  --deployment-name "Mistral-Large-3" \
  --model-name "Mistral-Large-3" \
  --model-format "MaaS" \
  --sku-capacity 1 \
  --sku-name "GlobalStandard"
```

> **Nom de modèle vs déploiement.** `--deployment-name "Mistral-Large-3"` doit égaler
> `AZURE_AI_INFERENCE_MODEL` (contrat code). `--model-name` est l'identifiant **catalogue**
> Azure : viser **Mistral Large 3** (pas `Mistral-Large-2411` = Large **2**). Si le catalogue
> expose un id versionné, reprendre l'identifiant exact du **Model catalog** du portail.

**Vérifie :**

```bash
az cognitiveservices account show --name "aoai-${SUFFIX}-chat" --resource-group "$RG" --query provisioningState -o tsv
# → Succeeded
az cognitiveservices account deployment show --name "aoai-${SUFFIX}-chat" --resource-group "$RG" \
  --deployment-name "Mistral-Large-3" --query provisioningState -o tsv
# → Succeeded
```

**Portail :** **Create a resource** → « Azure AI services » (kind AI Services, pas Azure
OpenAI pur) → créer. Puis Microsoft Foundry (`ai.azure.com`) → projet lié à la ressource →
**Models + endpoints** → **+ Deploy model** → **Deploy base model** → chercher
`Mistral-Large` (catégorie « Models sold directly by Azure » / « Partner models ») →
`Deployment name` = `Mistral-Large-3`, `Deployment type` = **Global Standard** → **Deploy**.
(Les modèles tiers sont facturés « pay-as-you-go » MaaS — le formulaire l'indique.)

**Récupérer endpoint + clé :** page de la ressource (portail Azure classique) → **Resource
Management** → **Keys and Endpoint**. ⚠️ **Suffixer l'endpoint de `/openai/v1`** (règle
projet, enforced par `validate_startup`) :
`AZURE_AI_INFERENCE_ENDPOINT=https://<resource>.services.ai.azure.com/openai/v1`.

## B2. Juge garde-fous — gpt-5-mini (Azure OpenAI, ressource dédiée)

**But :** le second LLM, isolé, qui juge chaque message (périmètre, injection, fuite).

**Terminal :**

```bash
az cognitiveservices account create \
  --name "aoai-${SUFFIX}-guard" \
  --resource-group "$RG" \
  --location "$LOCATION" \
  --kind OpenAI \
  --sku S0 \
  --custom-domain "aoai-${SUFFIX}-guard"

# 1. Liste les versions de gpt-5-mini disponibles dans ta région :
az cognitiveservices account list-models \
  --name "aoai-${SUFFIX}-guard" --resource-group "$RG" -o table | grep -i gpt-5-mini

# 2. Renseigne la version choisie (copiée depuis la sortie ci-dessus) — pas de
#    placeholder deviné dans la commande de création :
GUARD_VER="2024-07-18"   # ← remplace par la version réellement affichée

az cognitiveservices account deployment create \
  --name "aoai-${SUFFIX}-guard" \
  --resource-group "$RG" \
  --deployment-name "gpt-5-mini" \
  --model-name "gpt-5-mini" \
  --model-version "$GUARD_VER" \
  --model-format OpenAI \
  --sku-capacity 10 \
  --sku-name "Standard"
```

**Vérifie :**

```bash
az cognitiveservices account deployment show --name "aoai-${SUFFIX}-guard" --resource-group "$RG" \
  --deployment-name "gpt-5-mini" --query provisioningState -o tsv
# → Succeeded
```

**Portail :** **Create a resource** → « Azure OpenAI » (kind **Azure OpenAI**, contrairement
à B1/B3) → `Name` = `aoai-velmo-prod-guard`, `Pricing tier` = **Standard S0**. Puis Foundry
(`ai.azure.com`) → **Models + endpoints** → **+ Deploy model** → `gpt-5-mini` →
`Deployment name` = `gpt-5-mini`, `Deployment type` = **Standard** (PTU réservé à B5) →
**Deploy**.

**Récupérer endpoint + clé :** **Keys and Endpoint** de la ressource — jamais les variables
`ANTHROPIC_*` de B3. ⚠️ **Suffixer de `/openai/v1`** : le portail donne l'endpoint « nu »,
mais `judge.py` utilise le client OpenAI standard sur ce `base_url` — sans le suffixe,
404 sur chaque appel. `AZURE_OPENAI_GUARD_ENDPOINT=https://<resource>.openai.azure.com/openai/v1`.

> Ce déploiement peut rejeter le paramètre `logprobs` (constaté en réel) — l'app le gère
> automatiquement (repli sans logprobs mémorisé, `judge.py`) : rien à configurer.

## B3. Async — claude-opus-4-5 (Azure AI Foundry, partenaire Anthropic)

**But :** l'extracteur mémoire + le juge DeepEval (Ch.3) — usages asynchrones, quota séparé du
chemin bloquant.

**Terminal :**

```bash
az cognitiveservices account create \
  --name "aif-${SUFFIX}-async" \
  --resource-group "$RG" \
  --location "$LOCATION" \
  --kind AIServices \
  --sku S0 \
  --custom-domain "aif-${SUFFIX}-async"

# --model-format/--model-version exacts à confirmer dans le catalogue Foundry au moment du
# déploiement (syntaxe des modèles partenaires Anthropic encore jeune côté CLI ; le chemin
# portail ci-dessous est le plus fiable aujourd'hui).
az cognitiveservices account deployment create \
  --name "aif-${SUFFIX}-async" \
  --resource-group "$RG" \
  --deployment-name "claude-opus-4-5" \
  --model-name "claude-opus-4-5" \
  --model-format "Anthropic" \
  --sku-capacity 10 \
  --sku-name "GlobalStandard"
```

**Vérifie :**

```bash
az cognitiveservices account deployment show --name "aif-${SUFFIX}-async" --resource-group "$RG" \
  --deployment-name "claude-opus-4-5" --query provisioningState -o tsv
# → Succeeded
```

**Portail :** **Create a resource** → « Azure AI services » → créer (`Name` =
`aif-velmo-prod-async`, S0, Network = All networks pour un premier déploiement). Puis
**Go to Foundry portal** (`ai.azure.com`) → **Models + endpoints** → **+ Deploy model** →
**Deploy base model** → `claude-opus-4-5` (catégorie « Partner models », Anthropic) →
`Deployment name` = `claude-opus-4-5` → **Deploy**. La page atterrit sur le **Playground** —
utile pour tester avant de brancher le code.

**Récupérer endpoint + clé :** **Keys and Endpoint** de la ressource. Forme **spécifique
Anthropic** (exception à la règle `/openai/v1` — SDK Anthropic) :
`ANTHROPIC_FOUNDRY_ENDPOINT=https://<resource>.services.ai.azure.com/anthropic`.

## B4. PII + Prompt Shields — rien à déployer (chemin court recommandé)

**But :** `pii_redaction.py` (Azure AI Language) et `prompt_shields.py` (Content Safety) —
features **optionnelles par conception** (absence des 2 variables d'un couple = repli gracieux,
même en production).

> **Chemin court (recommandé)** : une ressource **kind AIServices** (B1 ou B3) est
> **multi-service** — Language et Content Safety y sont inclus **d'office**. Il n'y a **rien
> à « déployer »** dans le portail Foundry (ce ne sont pas des modèles — chercher un bouton
> Deploy est un cul-de-sac). Pointer les variables sur la **racine** de la ressource, avec
> **sa clé** :
>
> ```
> AZURE_LANGUAGE_ENDPOINT=https://<resource>.cognitiveservices.azure.com
> AZURE_LANGUAGE_KEY=<clé de la ressource AIServices>
> AZURE_CONTENT_SAFETY_ENDPOINT=https://<resource>.cognitiveservices.azure.com
> AZURE_CONTENT_SAFETY_KEY=<même clé>
> ```
>
> ⚠️ **Sans `/openai/v1`** — APIs propres (`/contentsafety/text:shieldPrompt`,
> `/language/:analyze-text`), pas OpenAI-compatibles. Vérifié en réel (200 sur les deux).
> Quotas distincts des TPM du modèle — pas de contention avec l'agent.

**Vérifie (chemin court) :**

```bash
KEY="<clé de la ressource AIServices>"; ROOT="https://<resource>.cognitiveservices.azure.com"
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$ROOT/contentsafety/text:shieldPrompt?api-version=2024-09-01" \
  -H "Ocp-Apim-Subscription-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"userPrompt":"test","documents":[]}'
# → 200
```

**Option ressources dédiées** (isolation quota/facturation par service) :

```bash
az cognitiveservices account create --name "lang-${SUFFIX}" --resource-group "$RG" \
  --location "$LOCATION" --kind TextAnalytics --sku S --custom-domain "lang-${SUFFIX}"
az cognitiveservices account create --name "cs-${SUFFIX}" --resource-group "$RG" \
  --location "$LOCATION" --kind ContentSafety --sku S0 --custom-domain "cs-${SUFFIX}"
```

**Portail (dédiées) :** **Create a resource** → « Language service » / « Content Safety » →
créer → **Keys and Endpoint**.

## B5. Plus tard : bascule Standard → PTU (juge uniquement, sur mesure)

**But :** ne PAS provisionner de capacité réservée par anticipation — mesurer le throttling
(429) d'abord.

**Terminal (mesure) :**

```bash
az monitor metrics list \
  --resource "$(az cognitiveservices account show -n aoai-${SUFFIX}-guard -g $RG --query id -o tsv)" \
  --metric "AzureOpenAIRequests" \
  --filter "ModelDeploymentName eq 'gpt-5-mini'"
```

**Portail (mesure)** : ressource guard → **Monitoring → Metrics** → « Azure OpenAI
Requests », filtre `ModelDeploymentName` = `gpt-5-mini` + code 429. **Portail (bascule)** :
Foundry → **Models + endpoints** → `gpt-5-mini` → **Edit deployment** → `Deployment type` =
**Provisioned-Managed** → choisir les PTU → confirmer (capacité **facturée en continu** —
lire le récapitulatif de coût avant). Seuil indicatif : >1 % de 429 sur une heure.

---

# Phase C — PostgreSQL (mémoire, audit, MLOps)

## C1. Flexible Server + PITR

**But :** la source de vérité durable. PITR 35 jours = fenêtre de restauration **technique**
(incident) — distincte de la rétention **métier** (purges applicatives Ch.1).

**Terminal :**

```bash
# Mot de passe admin généré, jamais écrit en clair — poussé dans Key Vault en Phase D :
PG_ADMIN_PWD=$(openssl rand -base64 24)

az postgres flexible-server create \
  --name "psql-${SUFFIX}" \
  --resource-group "$RG" \
  --location "$LOCATION" \
  --sku-name "Standard_B1ms" \
  --tier "Burstable" \
  --storage-size 64 \
  --version 16 \
  --admin-user "velmo_admin" \
  --admin-password "$PG_ADMIN_PWD" \
  --high-availability Disabled \
  --backup-retention 35 \
  --geo-redundant-backup Disabled
```

**Vérifie :**

```bash
az postgres flexible-server show --name "psql-${SUFFIX}" --resource-group "$RG" --query state -o tsv
# → Ready
```

**Portail :** **Create a resource** → « Azure Database for PostgreSQL flexible server » →
**Basics** : `Server name` = `psql-velmo-prod`, `PostgreSQL version` = **16**, `Workload type`
= **Development** → tier **Burstable**, taille `Standard_B1ms` (1 vCPU / 2 Go ; le portail
affiche vCPU/RAM par palier), `High availability` = **Disabled**. **Backup** : rétention **35 days**, geo-redundancy
**Disabled**. Admin user/password (le mot de passe généré — jamais collé ailleurs qu'en
Phase D). **Review + create**.

## C2. Extension pgvector

**But :** embeddings de la mémoire épisodique dans la même base (R5 atomique).

**Terminal :**

```bash
az postgres flexible-server parameter set \
  --resource-group "$RG" --server-name "psql-${SUFFIX}" \
  --name azure.extensions --value "VECTOR"
# Puis, connecté à la base cible (psql) :
# CREATE EXTENSION IF NOT EXISTS vector;
```

**Vérifie :**

```bash
az postgres flexible-server parameter show --resource-group "$RG" --server-name "psql-${SUFFIX}" \
  --name azure.extensions --query value -o tsv
# → contient VECTOR
```

```sql
SELECT extname FROM pg_extension WHERE extname = 'vector';
-- → une ligne
```

**Portail :** ressource → **Settings → Server parameters** → `azure.extensions` → cocher
**VECTOR** → **Save**. ⚠️ L'extension s'appelle `vector` (pas `pgvector`) — c'est ce nom dans
`CREATE EXTENSION vector;` (via psql, Azure Data Studio, ou **Query editor** du portail si
disponible sur ton tier).

## C3. Rôles Postgres (moindre privilège)

**But :** l'app n'a jamais les droits DDL ; le support ne voit jamais la mémoire en clair.

**Terminal (SQL, dans la base — pas d'équivalent portail) :**

```sql
-- Rôle applicatif (runtime) : données oui, schéma non
CREATE ROLE velmo_app WITH LOGIN PASSWORD '<généré : openssl rand -base64 24, → Key Vault Phase D>';
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO velmo_app;

-- Rôle migrations (déploiement uniquement)
CREATE ROLE velmo_migrator WITH LOGIN PASSWORD '<généré, → Key Vault>';
GRANT CREATE ON DATABASE velmo TO velmo_migrator;

-- Rôle support lecture restreinte — PAS de FACT/EPISODE en clair
CREATE ROLE velmo_support_readonly WITH LOGIN PASSWORD '<généré, → Key Vault>';
GRANT SELECT ON guardrail_audit, eval_run, eval_case_result, agent_version TO velmo_support_readonly;
```

**Vérifie :**

```sql
\du velmo_app velmo_migrator velmo_support_readonly
-- → les trois rôles avec LOGIN
SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants
WHERE grantee = 'velmo_support_readonly';
-- → uniquement guardrail_audit, eval_run, eval_case_result, agent_version
```

## C4. Schéma : Alembic, unique source (jamais `create_all` sur Postgres)

**But :** appliquer la chaîne de migrations — l'app ne crée **aucune** table sur Postgres
(audit D2-04) ; en CI, `release.yml`/`nightly.yml` lancent `alembic upgrade head` avant toute
évaluation (D7-17).

**Terminal :**

```bash
DB_URL="postgresql+psycopg://velmo_app:<mdp>@psql-${SUFFIX}.postgres.database.azure.com:5432/velmo" \
  uv run alembic upgrade head
```

**Vérifie :**

```bash
uv run alembic current
# → hash de la dernière révision, suffixé (head)
```

## C5. Test de restauration (avant mise en prod, puis annuel)

**But :** un backup jamais restauré n'est qu'une hypothèse.

**Terminal :**

```bash
# Point DANS la fenêtre de rétention (après création du serveur, avant maintenant) :
RESTORE_TIME=$(date -u -d '-1 hour' +%Y-%m-%dT%H:%M:%SZ)   # macOS : date -u -v-1H +%Y-%m-%dT%H:%M:%SZ

az postgres flexible-server restore \
  --resource-group "$RG" \
  --name "psql-${SUFFIX}-restore-test" \
  --source-server "psql-${SUFFIX}" \
  --restore-time "$RESTORE_TIME"
```

**Vérifie puis nettoie :**

```bash
az postgres flexible-server show --resource-group "$RG" --name "psql-${SUFFIX}-restore-test" --query state -o tsv
# → Ready ; puis connexion psql, données attendues présentes. Ensuite :
az postgres flexible-server delete --resource-group "$RG" --name "psql-${SUFFIX}-restore-test" --yes
```

**Portail :** ressource → **Backup and restore** → **Restore** → **Point-in-time restore** →
horodatage + nom du serveur de test → **Review + create**. Supprimer la copie après
vérification. Documenter la date du dernier test réussi (registre opérationnel).

---

# Phase D — Key Vault : TOUS les secrets entrent ici

**But :** le coffre = **source de vérité** des secrets. Ton `.env` local (Partie 1) et les
GitHub Secrets (Partie 3) se **régénèrent depuis lui** — plus jamais de copier-coller de clé
depuis un onglet de portail.

## D1. Créer le vault (mode RBAC)

**Terminal :**

```bash
az keyvault create \
  --name "kv-${SUFFIX}" \
  --resource-group "$RG" \
  --location "$LOCATION" \
  --enable-rbac-authorization true
```

**Vérifie :**

```bash
az keyvault show --name "kv-${SUFFIX}" --query properties.provisioningState -o tsv
# → Succeeded
```

**Portail :** **Create a resource** → « Key Vault » → **Basics** : `Name` = `kv-velmo-prod`,
tier **Standard** → onglet **Access configuration** : `Permission model` = **Azure role-based
access control** (= `--enable-rbac-authorization true` ; l'ancien modèle « Vault access
policy » est déconseillé pour un nouveau vault) → **Review + create**.

> Pour pouvoir écrire des secrets toi-même en mode RBAC, ton compte doit avoir le rôle
> **Key Vault Secrets Officer** sur le vault :
> `az role assignment create --assignee $(az ad signed-in-user show --query id -o tsv) --role "Key Vault Secrets Officer" --scope $(az keyvault show --name kv-${SUFFIX} --query id -o tsv)`

## D2. Remplir le coffre — la liste complète

**But :** chaque valeur collectée dans les phases B/C entre ici, **sous ces noms exacts**
(consommés par le smoke final et la Partie 3 §5).

**Terminal :**

```bash
KV="kv-${SUFFIX}"
az keyvault secret set --vault-name "$KV" --name azure-ai-inference-endpoint   --value "https://<resource>.services.ai.azure.com/openai/v1"
az keyvault secret set --vault-name "$KV" --name azure-ai-inference-api-key    --value "<clé B1>"
az keyvault secret set --vault-name "$KV" --name azure-openai-guard-endpoint   --value "https://<resource>.openai.azure.com/openai/v1"
az keyvault secret set --vault-name "$KV" --name azure-openai-guard-api-key    --value "<clé B2>"
az keyvault secret set --vault-name "$KV" --name anthropic-foundry-endpoint    --value "https://<resource>.services.ai.azure.com/anthropic"
az keyvault secret set --vault-name "$KV" --name anthropic-api-key             --value "<clé B3>"
az keyvault secret set --vault-name "$KV" --name azure-language-endpoint       --value "https://<resource>.cognitiveservices.azure.com"
az keyvault secret set --vault-name "$KV" --name azure-language-key            --value "<clé B4>"
az keyvault secret set --vault-name "$KV" --name azure-content-safety-endpoint --value "https://<resource>.cognitiveservices.azure.com"
az keyvault secret set --vault-name "$KV" --name azure-content-safety-key      --value "<clé B4>"
az keyvault secret set --vault-name "$KV" --name postgres-admin-password       --value "$PG_ADMIN_PWD"
az keyvault secret set --vault-name "$KV" --name postgres-app-password         --value "<mdp velmo_app C3>"
az keyvault secret set --vault-name "$KV" --name db-url \
  --value "postgresql+psycopg://velmo_app:<mdp>@psql-${SUFFIX}.postgres.database.azure.com:5432/velmo"
# Optionnel (observabilité, §G3) :
az keyvault secret set --vault-name "$KV" --name langfuse-public-key --value "pk-lf-..."
az keyvault secret set --vault-name "$KV" --name langfuse-secret-key --value "sk-lf-..."
```

**Vérifie :**

```bash
az keyvault secret list --vault-name "$KV" --query '[].name' -o tsv | sort
# → la liste ci-dessus, complète
```

**Portail :** ressource vault → **Objects → Secrets** → **+ Generate/Import** → `Name` +
`Value` → **Create**, pour chaque secret.

## D3. Accès applicatif au coffre — identité managée de l'hôte

> ℹ️ **Hébergement tranché : Azure Container Apps** (Phase F ; le *pourquoi* — ACA vs App
> Service, R2/R3 — est dans `conception_chantier3_evaluation_mlops.md` §Cible de déploiement).
> Ce bloc donne à l'**identité managée de l'app** le droit de **lire** les secrets. Il
> s'exécute **après** la création de l'app (F3), qui produit le `principalId` utilisé
> ci-dessous — Phase F §F4 y renvoie.

**Terminal :**

```bash
APP_PRINCIPAL_ID="<principalId de l'identité managée de l'app ACA — voir F4>"
KV_ID=$(az keyvault show --name "kv-${SUFFIX}" --query id -o tsv)

# Vault en mode RBAC → role assignment, PAS `az keyvault set-policy`
# (les access policies sont incompatibles avec le mode RBAC).
az role assignment create \
  --assignee "$APP_PRINCIPAL_ID" \
  --role "Key Vault Secrets User" \
  --scope "$KV_ID"
```

**Vérifie :**

```bash
az role assignment list --assignee "$APP_PRINCIPAL_ID" --scope "$KV_ID" -o table
# → "Key Vault Secrets User" sur le vault
```

**Portail :** hôte → **Settings → Identity** → **System assigned** = On → **Save**. Puis
vault → **Access control (IAM)** → **+ Add role assignment** → **Key Vault Secrets User** →
`Assign access to` = **Managed identity** → sélectionner l'identité → **Review + assign**.
(En mode RBAC c'est bien **IAM**, pas l'onglet legacy « Access policies ».)

## D4. Staging vs production

**Deux vaults distincts** (`kv-velmo-staging`, `kv-velmo-prod`) — jamais un vault partagé
avec des secrets préfixés : un bug de préfixe est un bug de fuite entre environnements.

---

# Phase E — Llama Guard 3 (Ollama, auto-hébergé CPU)

**But :** le classifieur de modération (G1/G2/G3), gratuit, auto-hébergé — conteneur simple
d'abord, VM/GPU seulement si la latence mesurée l'exige.

**Terminal :**

```bash
# Le pull du modèle est intégré à la commande de démarrage : `az container exec` ne prend
# pas d'arguments de commande de façon fiable (limitation ACI documentée).
az container create \
  --resource-group "$RG" \
  --name "ollama-${SUFFIX}" \
  --image "ollama/ollama:latest" \
  --cpu 4 \
  --memory 8 \
  --ports 11434 \
  --restart-policy Always \
  --location "$LOCATION" \
  --command-line "/bin/sh -c 'ollama serve & sleep 5 && ollama pull llama-guard3:8b && wait'"
```

**Vérifie :**

```bash
az container show --resource-group "$RG" --name "ollama-${SUFFIX}" --query instanceView.state -o tsv
# → Running
az container logs --resource-group "$RG" --name "ollama-${SUFFIX}" | grep -iE "llama-guard3|success"
# → pull de llama-guard3:8b terminé
```

**Portail :** **Create a resource** → « Container Instances » → **Basics** : `Image source` =
Other registry, `Image` = `ollama/ollama:latest`, Linux, 4 vCPU / 8 Go. **Networking** : port
**11434**, `Networking type` **Private** si un VNet existe (voir note réseau). **Advanced** :
`Restart policy` = Always. **Review + create**.

- **Latence** : suivre le p95 isolé du composant (instrumentation Ch.3). Bascule
  `llama-guard3:1b` si **p95 > 800 ms** (= `LLAMA_GUARD_MODEL`, pas un redéploiement).
- **GPU** : seulement si le 1B dépasse encore le seuil — jamais en prévention.
- **Réseau** : ce conteneur ne doit **pas** être public — VNet + pare-feu, composant de
  sécurité interne.

---

# Phase F — Héberger l'application (Azure Container Apps)

**But :** mettre l'agent en ligne. Choix tranché : **Azure Container Apps (ACA)** — l'image
conteneur existe déjà (`Dockerfile`, `deploy/README.md`), scale-to-zero (coût ≈ 0 à l'arrêt),
identité managée native pour lire Key Vault. Le *pourquoi* (ACA vs App Service, R2/R3) :
`conception_chantier3_evaluation_mlops.md` §Cible de déploiement.

> ACA tire l'image d'un **registre** : on crée d'abord un Azure Container Registry (F1), on y
> pousse l'image (F2), on déploie (F3), puis on câble les secrets du coffre (F4).

## F1. Azure Container Registry — héberger l'image

**But :** un registre privé d'où ACA tire l'image.

**Terminal :**

```bash
# Nom ACR : alphanumérique uniquement (pas de tiret) — d'où `acrvelmoprod`.
export ACR="acrvelmoprod"
az acr create --resource-group "$RG" --name "$ACR" --sku Basic --admin-enabled false
```

**Vérifie :**

```bash
az acr show --name "$ACR" --query provisioningState -o tsv
# → Succeeded
```

**Portail :** **Create a resource** → « Container Registry » → SKU **Basic** → **Create**.

## F2. Construire et pousser l'image

**But :** produire l'image dans le registre, sans Docker local (`az acr build` construit côté cloud).

**Terminal :**

```bash
az acr build --registry "$ACR" --image "velmo:$(git rev-parse --short HEAD)" --file Dockerfile .
```

**Vérifie :**

```bash
az acr repository show-tags --name "$ACR" --repository velmo -o table
# → le tag (sha court) apparaît
```

> Tag = sha git court : l'image déployée est **traçable** au commit (même logique que le tag
> semver de la Partie 3).

## F3. Créer l'environnement + déployer l'app

**But :** l'app servie en HTTPS, scale-to-zero, sonde de santé sur `/health` (déjà exposé par
`velmo.api`).

**Terminal :**

```bash
# Environnement ACA (crée un workspace Log Analytics implicite pour les logs) :
az containerapp env create --name "cae-${SUFFIX}" --resource-group "$RG" --location "$LOCATION"

IMAGE="${ACR}.azurecr.io/velmo:$(git rev-parse --short HEAD)"

az containerapp create \
  --name "ca-${SUFFIX}" \
  --resource-group "$RG" \
  --environment "cae-${SUFFIX}" \
  --image "$IMAGE" \
  --registry-server "${ACR}.azurecr.io" \
  --system-assigned \
  --ingress external --target-port 8000 \
  --min-replicas 0 --max-replicas 3 \
  --env-vars ENVIRONMENT=production VELMO_WEB_ORIGINS="https://<ton-front>"
```

**Vérifie :**

```bash
FQDN=$(az containerapp show --name "ca-${SUFFIX}" --resource-group "$RG" \
  --query properties.configuration.ingress.fqdn -o tsv)
curl -fsS "https://$FQDN/health"
# → le corps de GET /health (200)
```

> **`--min-replicas 0` = scale-to-zero** : coût nul à l'arrêt, mais **cold start** au 1er
> appel après inactivité. Passer à `1` si la latence de démarrage n'est pas tolérable — seul
> arbitrage coût/latence de l'hôte (le juge garde-fous bloquant, lui, reste toujours chaud
> côté Azure OpenAI). **Sonde `/health`** : ACA sonde par défaut le `--target-port` ; pour une
> sonde HTTP explicite sur `/health`, ajouter un health probe (`az containerapp update --yaml`,
> la syntaxe `--health-probe-*` variant selon la version de la CLI).

## F4. Identité managée → secrets Key Vault → variables d'app

**But :** l'app lit ses secrets **du coffre**, jamais une valeur en clair dans la config ACA.

**Terminal :**

```bash
# 1. principalId de l'identité managée de l'app (créée en F3 via --system-assigned) :
export APP_PRINCIPAL_ID=$(az containerapp show --name "ca-${SUFFIX}" --resource-group "$RG" \
  --query identity.principalId -o tsv)

# 2. Lui donner l'accès LECTURE au coffre → exécuter le bloc D3 (rôle « Key Vault Secrets User »).

# 3. Déclarer chaque secret ACA comme une RÉFÉRENCE Key Vault (résolue par l'identité) :
KV_URI="https://kv-${SUFFIX}.vault.azure.net/secrets"
az containerapp secret set --name "ca-${SUFFIX}" --resource-group "$RG" --secrets \
  db-url="keyvaultref:${KV_URI}/db-url,identityref:system" \
  ai-key="keyvaultref:${KV_URI}/azure-ai-inference-api-key,identityref:system" \
  guard-key="keyvaultref:${KV_URI}/azure-openai-guard-api-key,identityref:system" \
  anthropic-key="keyvaultref:${KV_URI}/anthropic-api-key,identityref:system"

# 4. Mapper secrets (clés) ET endpoints (non-secrets) sur les variables lues par config.py :
az containerapp update --name "ca-${SUFFIX}" --resource-group "$RG" --set-env-vars \
  DB_URL=secretref:db-url \
  AZURE_AI_INFERENCE_API_KEY=secretref:ai-key \
  AZURE_OPENAI_GUARD_API_KEY=secretref:guard-key \
  ANTHROPIC_API_KEY=secretref:anthropic-key \
  AZURE_AI_INFERENCE_ENDPOINT="$(az keyvault secret show --vault-name kv-${SUFFIX} --name azure-ai-inference-endpoint --query value -o tsv)" \
  AZURE_OPENAI_GUARD_ENDPOINT="$(az keyvault secret show --vault-name kv-${SUFFIX} --name azure-openai-guard-endpoint --query value -o tsv)" \
  ANTHROPIC_FOUNDRY_ENDPOINT="$(az keyvault secret show --vault-name kv-${SUFFIX} --name anthropic-foundry-endpoint --query value -o tsv)" \
  OLLAMA_URL="http://<ip-privée-ollama>:11434"
```

**Vérifie :**

```bash
az containerapp show --name "ca-${SUFFIX}" --resource-group "$RG" \
  --query "properties.template.containers[0].env[].name" -o tsv
# → DB_URL, AZURE_*, ANTHROPIC_*, OLLAMA_URL (les valeurs des clés ne s'affichent jamais : secretref)
curl -fsS "https://$FQDN/health"   # toujours 200 après le redémarrage de la révision
```

> **Migrations avant tout trafic réel** : le schéma prod se pose avec le rôle `velmo_migrator`
> (C3), pas au runtime — `alembic upgrade head` une fois (job ponctuel `az containerapp job`
> ou depuis une machine d'admin joignant le Postgres), exactement comme le gate release
> (Partie 3). L'app tourne ensuite avec `velmo_app` (aucun droit DDL).

> **Endpoints en clair, clés en référence** : un endpoint est une URL publique (pas un
> secret) → variable d'app ordinaire ; seules les **clés** et la **chaîne de connexion**
> (mot de passe) passent par `secretref` → Key Vault. Même séparation qu'en D2/D3.

---

# Phase G — Compléments

## G1. Escalade humaine — Logic App (canal gratuit)

**But :** deux canaux d'escalade (support G2, sécurité G7/récidive G6) sans outil de
ticketing — un webhook qui envoie un e-mail, appelé sur
`INSERT guardrail_audit(action='block_escalate')`.

**Portail (recommandé — le designer visuel est plus simple que le JSON) :** **Create a
resource** → « Logic App » → **Consumption** (tier à quota gratuit, pas Standard) → `Name` =
`escalade-guardrails` → **Create**. Puis **Logic app designer** → trigger **When a HTTP
request is received** → **+ New step** → connecteur **Office 365 Outlook**/**Gmail** →
**Send an email (V2)** → renseigner To/Subject/Body (peut injecter les champs du JSON reçu) →
**Save**. L'URL de webhook générée est celle que l'app appelle.

**Terminal (versionner la définition après création) :**

```bash
az logic workflow show --resource-group "$RG" --name "escalade-guardrails" \
  --query definition > deploy/logic-app/escalade.json
# Recréation reproductible :
az logic workflow create --resource-group "$RG" --name "escalade-guardrails" \
  --location "$LOCATION" --definition @deploy/logic-app/escalade.json
```

**Vérifie :**

```bash
az logic workflow show --resource-group "$RG" --name "escalade-guardrails" --query state -o tsv
# → Enabled
```

Alternative zéro-service : SMTP direct vers une boîte dédiée — suffisant à faible volume.

## G2. Tarification — vérification trimestrielle

**But :** la table `token_pricing` (config versionnée) doit suivre les prix réels, **pour
chaque vendor** (Azure OpenAI ≠ Foundry/Anthropic — dérives indépendantes).

**Portail (seul chemin pertinent) :** **Cost Management** → **Cost analysis** → `Scope` =
`$RG` → filtre `Resource` = `aoai-${SUFFIX}-guard` puis `aif-${SUFFIX}-async` →
`Granularity` = mensuel → comparer au coût recalculé (`eval_run.cost_per_conv`). Écart
notable = corriger `token_pricing`.

## G3. Langfuse Cloud (observabilité — hors Azure)

Décision révisée : projet pédagogique → **Langfuse Cloud région EU** (pas de self-host, pas
de ressource Azure). Procédure compte/projet/clés : `deploy/langfuse/README.md`. Les clés
vont dans Key Vault (D2 : `langfuse-public-key`/`langfuse-secret-key`) et alimentent
`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/`LANGFUSE_BASE_URL`. Self-host (module Terraform
`langfuse/langfuse-terraform-azure`, région UE) si un jour vraies données client.

## G4. RuleBasedJudge en shadow mode — déjà implémenté (rappel)

Rien à provisionner : `ShadowingJudge` (`src/velmo/guardrails/judge.py`) calcule le verdict
du repli déterministe sur **chaque** message, en tâche de fond, et le journalise
(`guardrail_audit.shadow_verdict`) sans jamais influencer la décision. En cas de panne du
juge cloud, le pipeline applique la ligne **fail-closed** de la matrice
(`pipeline._fallback_hits`) — le shadow sert à **mesurer/durcir** le repli, pas à décider.
Un job d'analyse hebdomadaire du taux de divergence reste une bonne pratique (hors chemin
critique).

---

# Vérification finale — smoke test bout-en-bout

**But :** prouver la chaîne complète **app → LLM → DB → garde-fous** avec les valeurs du
coffre, avant de passer à la Partie 3.

```bash
KV="kv-${SUFFIX}"
# 1. Charger la config DEPUIS Key Vault (source unique — pas de copier-coller) :
export AZURE_AI_INFERENCE_ENDPOINT=$(az keyvault secret show --vault-name "$KV" --name azure-ai-inference-endpoint --query value -o tsv)
export AZURE_AI_INFERENCE_API_KEY=$(az keyvault secret show --vault-name "$KV" --name azure-ai-inference-api-key --query value -o tsv)
export AZURE_OPENAI_GUARD_ENDPOINT=$(az keyvault secret show --vault-name "$KV" --name azure-openai-guard-endpoint --query value -o tsv)
export AZURE_OPENAI_GUARD_API_KEY=$(az keyvault secret show --vault-name "$KV" --name azure-openai-guard-api-key --query value -o tsv)
export ANTHROPIC_FOUNDRY_ENDPOINT=$(az keyvault secret show --vault-name "$KV" --name anthropic-foundry-endpoint --query value -o tsv)
export ANTHROPIC_API_KEY=$(az keyvault secret show --vault-name "$KV" --name anthropic-api-key --query value -o tsv)
export DB_URL=$(az keyvault secret show --vault-name "$KV" --name db-url --query value -o tsv)

# 2. Config cohérente (placeholders, couples à moitié posés, formes /openai/v1 : tout est contrôlé) :
ENVIRONMENT=production uv run python -c "from velmo.config import validate_startup; validate_startup(); print('config OK')"

# 3. Un échange agent réel (LLM principal + mémoire + garde-fous entrée/sortie) :
uv run python -m velmo.cli --user smoke-test <<< "Quel est le statut de ma commande O-2024-0101 ?"
```

**Attendu :** étape 2 → `config OK` ; étape 3 → une **vraie réponse de l'agent** (pas
`EchoLLM`, pas un refus garde-fou sur une question légitime). Un échec = variable ou
ressource mal câblée — corriger **avant** la Partie 3.

---

# Récapitulatifs

## Ressources créées

| Ressource | Phase | Rôle |
|---|---|---|
| `rg-velmo-prod` | A1 | groupe de ressources (tout vit dedans) |
| `aoai-${SUFFIX}-chat` | B1 | agent principal — `Mistral-Large-3` (AI Inference) |
| `aoai-${SUFFIX}-guard` | B2 | juge garde-fous — `gpt-5-mini` (Azure OpenAI, dédié, bloquant) |
| `aif-${SUFFIX}-async` | B3 | extracteur + DeepEval — `claude-opus-4-5` (Foundry/Anthropic) |
| *(option)* `lang-${SUFFIX}` / `cs-${SUFFIX}` | B4 | PII / Prompt Shields dédiés (sinon multi-service B1/B3) |
| `psql-${SUFFIX}` | C | PostgreSQL 16 + pgvector, PITR 35 j |
| `kv-${SUFFIX}` | D | Key Vault (RBAC) — source de vérité des secrets |
| `ollama-${SUFFIX}` | E | Llama Guard 3 (ACI, CPU, privé) |
| `acrvelmoprod` | F1 | Azure Container Registry — héberge l'image `velmo` |
| `cae-${SUFFIX}` | F3 | Container Apps environment (Log Analytics implicite) |
| `ca-${SUFFIX}` | F3 | **l'app servie** (ACA, ingress HTTPS, identité managée) |
| Logic App `escalade-guardrails` | G1 | webhook d'escalade → e-mail |

## Mapping secrets — variable app ↔ secret Key Vault ↔ origine ↔ forme

| Variable `.env` / CI | Secret Key Vault | Créé en | Forme exigée |
|---|---|---|---|
| `AZURE_AI_INFERENCE_ENDPOINT` | `azure-ai-inference-endpoint` | B1 | `…services.ai.azure.com/openai/v1` |
| `AZURE_AI_INFERENCE_API_KEY` | `azure-ai-inference-api-key` | B1 | — |
| `AZURE_OPENAI_GUARD_ENDPOINT` | `azure-openai-guard-endpoint` | B2 | `…openai.azure.com/openai/v1` |
| `AZURE_OPENAI_GUARD_API_KEY` | `azure-openai-guard-api-key` | B2 | — |
| `ANTHROPIC_FOUNDRY_ENDPOINT` | `anthropic-foundry-endpoint` | B3 | `…services.ai.azure.com/anthropic` |
| `ANTHROPIC_API_KEY` | `anthropic-api-key` | B3 | — |
| `AZURE_LANGUAGE_ENDPOINT` *(opt.)* | `azure-language-endpoint` | B4 | racine `…cognitiveservices.azure.com` (sans `/openai/v1`) |
| `AZURE_LANGUAGE_KEY` *(opt.)* | `azure-language-key` | B4 | — |
| `AZURE_CONTENT_SAFETY_ENDPOINT` *(opt.)* | `azure-content-safety-endpoint` | B4 | racine (sans `/openai/v1`) |
| `AZURE_CONTENT_SAFETY_KEY` *(opt.)* | `azure-content-safety-key` | B4 | — |
| `DB_URL` | `db-url` | C1/C3 | `postgresql+psycopg://velmo_app:…` |
| — (admin, jamais côté app) | `postgres-admin-password` | C1 | — |
| — (rôle app) | `postgres-app-password` | C3 | — |
| `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY` *(opt.)* | `langfuse-public-key`/`-secret-key` | G3 | — |
| `OLLAMA_URL` | *(pas un secret)* | E | `http://<ip-privée>:11434` |

## Checklist de sortie de la Partie 2

- [ ] A — RG créé (`Succeeded`), région UE
- [ ] B1/B2/B3 — 3 déploiements `Succeeded`, **noms exacts** (`Mistral-Large-3`, `gpt-5-mini`, `claude-opus-4-5`)
- [ ] B4 — PII/Shields : chemin court testé (200) **ou** ressources dédiées **ou** volontairement absents
- [ ] C — Postgres `Ready`, `vector` activé, 3 rôles SQL créés, `alembic upgrade head` (head), test de restauration fait puis supprimé
- [ ] D — vault RBAC créé, **tous les secrets posés** (liste D2 complète), 2 vaults si staging+prod
- [ ] E — Ollama `Running`, modèle pullé (logs), réseau privé
- [ ] F — image poussée sur ACR, app ACA déployée (`/health` = 200), identité managée → Key Vault, secrets en `secretref`, `alembic upgrade head` sur le Postgres prod
- [ ] Smoke final : `config OK` + vraie réponse agent

→ **Partie 3 : `tuto_github_actions_release.md`** (brancher la CI sur ces ressources).

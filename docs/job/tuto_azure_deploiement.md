# Tutoriel — Déploiement Azure de Velmo 2.0

> **Portée.** Ce tutoriel instancie concrètement, sur Azure, les décisions prises dans les
> 3 docs de conception (`docs/job/conceptions/conception_chantier{1,2,3}_*.md`). Il ne
> réexplique pas le *pourquoi* de chaque décision (voir les docs de conception) — il donne le
> *comment* : quelles ressources créer, dans quel ordre, avec quels paramètres.
>
> **Deux chemins équivalents.** Chaque étape est documentée **en CLI** (`az`, scriptable,
> reproductible — à préférer pour un vrai déploiement) **et via l'interface graphique**
> (portail Azure — `portal.azure.com` — et/ou le portail **Microsoft Foundry**, anciennement
> Azure AI Foundry, pour les ressources IA). Les deux créent exactement les mêmes ressources ;
> choisis celui qui te convient, ou utilise le portail pour explorer/vérifier visuellement ce
> que la CLI vient de créer. **La navigation exacte du portail évolue régulièrement** — si un
> libellé de menu a changé depuis la rédaction, chercher le nom du service dans la barre de
> recherche du portail reste le point d'entrée fiable.
>
> **Pré-requis** : un abonnement Azure actif. Pour le chemin CLI : `az` CLI installé et
> authentifié (`az login`). Pour le chemin portail : un navigateur, connecté sur
> `portal.azure.com` (ou `ai.azure.com` pour Microsoft Foundry). Dans les deux cas : les
> droits Contributor sur le groupe de ressources cible.

---

## 0. Conventions et variables

Toutes les commandes ci-dessous utilisent ces variables — à adapter une fois, réutilisées
partout :

```bash
export RG="rg-velmo-prod"                    # groupe de ressources
export LOCATION="francecentral"              # région UE — voir §1
export SUFFIX="velmo-prod"                   # suffixe unique pour nommer les ressources
```

```bash
az group create --name "$RG" --location "$LOCATION"
```

**Via le portail** : `portal.azure.com` → barre de recherche en haut → « Resource groups » →
**+ Create** → `Subscription` (la tienne), `Resource group` = `rg-velmo-prod`, `Region` =
`France Central` (ou `West Europe`) → **Review + create** → **Create**.

Vérifie :

```bash
az group show --name "$RG" --query provisioningState -o tsv
# → doit afficher: Succeeded
```

---

## 1. Région : pourquoi `francecentral` (ou toute région UE)

Décision de conception : tout déploiement traitant du contenu client en clair (conversations,
donc PII réelle) doit être en **région UE**. `francecentral` ou `westeurope` conviennent —
Langfuse (Cloud, région EU — voir §10) suit la même logique côté observabilité, même si ce
n'est plus une ressource Azure. Vérifier au moment du déploiement
que le service visé (Azure OpenAI, Azure AI Inference, Content Safety, Language) y est bien
disponible : la disponibilité par service et par région varie et change avec le temps.

```bash
# Vérifier la disponibilité d'Azure OpenAI dans la région choisie
az cognitiveservices account list-skus --kind OpenAI --location "$LOCATION" -o table
```

**Via le portail** : au moment de créer une ressource Azure OpenAI (§2), le sélecteur
`Region` du formulaire de création n'affiche que les régions où le service est réellement
disponible pour ton abonnement — pas besoin de vérifier à part, la liste déroulante fait
déjà le filtre. Microsoft publie aussi une page « Products available by region » (accessible
depuis le site azure.microsoft.com, section Global Infrastructure) qui donne une vue tableau
service × région si tu veux comparer plusieurs services avant de choisir.

---

## 2. Azure OpenAI + Azure AI Foundry — deux déploiements séparés (décision Q1, révisée)

Rappel de la décision : le juge garde-fous (chemin **bloquant synchrone**) ne doit pas
partager son quota avec l'extracteur mémoire et le juge DeepEval (tous deux **asynchrones/
best-effort**). Ces deux usages ne partagent plus seulement un quota isolé mais un **vendor
distinct** depuis la décision révisée (Ch.1) : `claude-opus-4-5` via **Azure AI Foundry**,
pas `gpt-5-mini`/Azure OpenAI. Le juge garde-fous, lui, reste sur Azure OpenAI, inchangé.

### 2.1 Ressource Azure AI Foundry — usages asynchrones (extracteur mémoire + juge DeepEval)

Modèles tiers (Anthropic, Mistral, …) se déploient comme au §2.4 (`kind AIServices`, format
catalogue Foundry), pas `kind OpenAI` — c'est un modèle partenaire, pas un modèle OpenAI natif.

```bash
az cognitiveservices account create \
  --name "aif-${SUFFIX}-async" \
  --resource-group "$RG" \
  --location "$LOCATION" \
  --kind AIServices \
  --sku S0 \
  --custom-domain "aif-${SUFFIX}-async"

# Déploiement du modèle — --model-format/--model-version exacts à confirmer dans le
# catalogue Foundry au moment du déploiement (syntaxe des modèles partenaires Anthropic
# encore jeune côté CLI ; le chemin portail ci-dessous est le plus fiable aujourd'hui).
az cognitiveservices account deployment create \
  --name "aif-${SUFFIX}-async" \
  --resource-group "$RG" \
  --deployment-name "claude-opus-4-5" \
  --model-name "claude-opus-4-5" \
  --model-format "Anthropic" \
  --sku-capacity 10 \
  --sku-name "GlobalStandard"
```

Vérifie :

```bash
az cognitiveservices account show --name "aif-${SUFFIX}-async" --resource-group "$RG" --query provisioningState -o tsv
# → doit afficher: Succeeded

az cognitiveservices account deployment show --name "aif-${SUFFIX}-async" --resource-group "$RG" \
  --deployment-name "claude-opus-4-5" --query provisioningState -o tsv
# → doit afficher: Succeeded
```

**Via le portail (création de la ressource)** :
1. `portal.azure.com` → **Create a resource** → rechercher « Azure AI services » (pas
   « Azure OpenAI ») → **Create**.
2. Onglet **Basics** : `Subscription` (la tienne), `Resource group` = `rg-velmo-prod`,
   `Region` = France Central/West Europe, `Name` = `aif-velmo-prod-async`,
   `Pricing tier` = **Standard S0**.
3. Onglet **Network** : laisser `All networks` par défaut pour un premier déploiement
   (à restreindre plus tard via un VNet privé si nécessaire).
4. **Review + create** → **Create**. Attendre la fin du déploiement (« Go to resource »).

**Via le portail (déploiement du modèle)** :
- Depuis la ressource créée → bouton **Go to Foundry portal** (ou directement
  `ai.azure.com` → projet lié à la ressource) → menu de gauche **Models + endpoints** →
  **+ Deploy model** → **Deploy base model** → chercher `claude-opus-4-5` dans le
  catalogue (catégorie « Partner models », modèles Anthropic) → **Confirm**.
- Dans la boîte de dialogue de déploiement : `Deployment name` = `claude-opus-4-5`,
  vérifier que la ressource connectée est bien `aif-velmo-prod-async` → **Deploy**.
- Une fois déployé, la page atterrit sur le **Playground** du modèle — utile pour tester
  l'appel manuellement avant de brancher le code (`ANTHROPIC_FOUNDRY_ENDPOINT`/
  `ANTHROPIC_API_KEY`, visibles dans l'onglet **Keys and Endpoint** de la ressource côté
  portail Azure classique). L'endpoint prend la forme
  `https://<resource>.services.ai.azure.com/anthropic` — pas le
  `.../openai/v1` des ressources Azure OpenAI/AI Inference.

### 2.2 Ressource Azure OpenAI n°2 — juge garde-fous (chemin bloquant)

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

Vérifie :

```bash
az cognitiveservices account show --name "aoai-${SUFFIX}-guard" --resource-group "$RG" --query provisioningState -o tsv
# → doit afficher: Succeeded

az cognitiveservices account deployment show --name "aoai-${SUFFIX}-guard" --resource-group "$RG" \
  --deployment-name "gpt-5-mini" --query provisioningState -o tsv
# → doit afficher: Succeeded
```

**Via le portail** : mêmes étapes qu'une ressource Azure OpenAI classique (`kind` = **Azure
OpenAI**, pas **Azure AI services** — contrairement à la ressource Foundry du §2.1) :
**Create a resource** → « Azure OpenAI » → `Name` = `aoai-velmo-prod-guard`, `Pricing tier`
= **Standard S0** → **Review + create**. Puis déploiement du modèle via Microsoft Foundry
(`ai.azure.com`, projet lié à la ressource) → **Models + endpoints** → **+ Deploy model** →
**Deploy base model** → chercher `gpt-5-mini` → `Deployment name` = `gpt-5-mini`,
`Deployment type` = **Standard** (pay-as-you-go — PTU réservé au §2.3), vérifier que la
ressource connectée est bien `aoai-velmo-prod-guard` → **Deploy**. Les identifiants
récupérés (`Keys and Endpoint`) alimentent `AZURE_OPENAI_GUARD_ENDPOINT`/
`AZURE_OPENAI_GUARD_API_KEY` — jamais les variables `ANTHROPIC_*` de la ressource Foundry
du §2.1.

> **Pourquoi deux ressources séparées ?** Vendor différent (Azure OpenAI vs Azure AI
> Foundry/Anthropic depuis la décision révisée, Ch.1) : deux ressources distinctes de toute
> façon, pas un choix à trancher. Ça règle aussi, en prime, l'isolation de quota que la
> décision Q1 visait initialement — Azure applique les quotas de rate-limit **par
> déploiement**, mais deux déploiements sur une même ressource restent soumis à la même
> limite globale de throughput (TPM) dans certains tiers ; deux ressources séparées
> garantissent une isolation complète, sans ambiguïté sur le throttling partagé.

### 2.3 Bascule Standard → Provisioned Throughput Unit (PTU)

Ne pas provisionner de PTU par anticipation (décision : mesurer avant d'investir). Une fois en
prod, surveiller le taux de throttling (erreurs 429) sur la ressource `aoai-${SUFFIX}-guard`
via Azure Monitor :

```bash
az monitor metrics list \
  --resource "$(az cognitiveservices account show -n aoai-${SUFFIX}-guard -g $RG --query id -o tsv)" \
  --metric "AzureOpenAIRequests" \
  --filter "ModelDeploymentName eq 'gpt-5-mini'"
```

Si le taux de 429 dépasse un seuil gênant (ex. >1% des requêtes sur une fenêtre d'une heure),
basculer ce déploiement en PTU (achat de capacité réservée dans le portail Azure OpenAI —
pas d'équivalent simple en CLI à ce jour, passage par le portail ou Azure Resource Manager
template).

**Via le portail (les deux étapes — mesure ET bascule)** :
- **Mesure** : `portal.azure.com` → ressource `aoai-velmo-prod-guard` → menu de gauche
  **Monitoring** → **Metrics** → `Metric` = « Azure OpenAI Requests », filtrer par
  `ModelDeploymentName` = `gpt-5-mini`, `Aggregation` = Count, ajouter un filtre sur
  le code de statut HTTP (429) pour isoler les rejets de quota. Alternative plus visuelle :
  onglet **Diagnose and solve problems** → catégorie « Quota/Throttling » de la ressource.
- **Bascule en PTU** : sur Microsoft Foundry (`ai.azure.com`), page **Models + endpoints** du
  déploiement `gpt-5-mini` → **Edit deployment** (ou recréer un nouveau déploiement si
  l'édition en place n'est pas proposée pour ce type de changement) → `Deployment type` =
  **Provisioned-Managed** au lieu de Standard → choisir le nombre de PTU (palier minimal
  affiché par l'interface selon le modèle) → confirmer. C'est un changement de capacité
  facturée en continu (réservée), pas un simple réglage — vérifier le récapitulatif de coût
  affiché avant de confirmer.

### 2.4 Azure AI Inference — modèle de chat principal (Mistral-Large-3)

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

> **Nom de modèle vs déploiement.** `--deployment-name "Mistral-Large-3"` doit correspondre à
> `AZURE_AI_INFERENCE_MODEL` (`.env.example`) — c'est ce que lit l'app. `--model-name` est
> l'identifiant **catalogue** Azure : viser **Mistral Large 3** (pas `Mistral-Large-2411`, qui
> est Mistral Large **2**). Si le catalogue expose un id versionné, reprendre l'identifiant
> exact affiché dans le **Model catalog** du portail.

Vérifie :

```bash
az cognitiveservices account show --name "aoai-${SUFFIX}-chat" --resource-group "$RG" --query provisioningState -o tsv
# → doit afficher: Succeeded

az cognitiveservices account deployment show --name "aoai-${SUFFIX}-chat" --resource-group "$RG" \
  --deployment-name "Mistral-Large-3" --query provisioningState -o tsv
# → doit afficher: Succeeded
```

**Via le portail** : la ressource se crée comme au §2.1 (Azure OpenAI → `Kind` = **AI
Services** au lieu d'Azure OpenAI pur, disponible dans le même sélecteur de type de
ressource lors de la création — chercher « Azure AI services » dans **Create a resource**).
Pour le déploiement du modèle : Microsoft Foundry (`ai.azure.com`) → projet lié à cette
ressource → **Models + endpoints** → **+ Deploy model** → **Deploy base model** → chercher
`Mistral-Large` dans le catalogue (modèles tiers, catégorie « Models sold directly by Azure »
ou « Partner models » selon le libellé affiché) → `Deployment name` = `Mistral-Large-3`,
`Deployment type` = **Global Standard** → **Deploy**. Les modèles tiers du catalogue (Mistral,
Meta, etc.) utilisent le format de facturation « pay-as-you-go » (MaaS) plutôt que les tiers
Standard/Provisioned propres aux modèles OpenAI — le formulaire de déploiement l'indique.

L'endpoint et la clé de cette ressource alimentent `AZURE_AI_INFERENCE_ENDPOINT` /
`AZURE_AI_INFERENCE_API_KEY` — les variables lues par `get_llm()` (`src/velmo/llm.py`,
`src/velmo/config.py`). Récupération via le portail : page de la ressource → menu de gauche
**Resource Management** → **Keys and Endpoint**. Rappel du contrat de démarrage (décision
Q15, Chantier 1) : en production, l'absence de ces variables doit faire échouer le démarrage,
pas basculer sur `EchoLLM` silencieusement — **déjà en place** : `get_llm()`
(`src/velmo/llm.py`) lève en `ENVIRONMENT=production` si l'endpoint est absent, et
`validate_startup()` (`src/velmo/config.py`) rejette tout couple endpoint/clé à moitié
renseigné.

---

## 3. Azure Database for PostgreSQL Flexible Server — `pgvector` + PITR

```bash
# Mot de passe admin généré aléatoirement, jamais écrit en clair : gardé en variable pour
# être poussé dans Key Vault au §4 (openssl rand → ~32 caractères).
PG_ADMIN_PWD=$(openssl rand -base64 24)

az postgres flexible-server create \
  --name "psql-${SUFFIX}" \
  --resource-group "$RG" \
  --location "$LOCATION" \
  --sku-name "Standard_D2ds_v5" \
  --tier "GeneralPurpose" \
  --storage-size 64 \
  --version 16 \
  --admin-user "velmo_admin" \
  --admin-password "$PG_ADMIN_PWD" \
  --high-availability Disabled \
  --backup-retention 35 \
  --geo-redundant-backup Disabled
```

Vérifie :

```bash
az postgres flexible-server show --name "psql-${SUFFIX}" --resource-group "$RG" --query state -o tsv
# → doit afficher: Ready
```

- `--backup-retention 35` : rétention maximale disponible sur Flexible Server (35 jours) pour
  le PITR natif Azure. **Ce n'est pas la rétention métier de 24 mois/90 jours** (celle des
  données mémoire, gérée par les jobs de purge applicatifs, §Chantier 1) — c'est la fenêtre de
  restauration technique en cas d'incident (bug, suppression accidentelle). Les deux
  mécanismes sont complémentaires, pas redondants.

**Via le portail (création du serveur)** : `portal.azure.com` → **Create a resource** →
rechercher « Azure Database for PostgreSQL flexible server » → **Create**. Onglet **Basics** :
`Server name` = `psql-velmo-prod`, `Region`, `PostgreSQL version` = **16**,
`Workload type` = choisir un tier proche de `Standard_D2ds_v5` (le sélecteur du portail
propose des paliers nommés — ex. « Development » pour un tier léger, « Production » pour un
tier dimensionné, avec le détail vCPU/RAM affiché à côté de chaque option),
`Availability zone` = laisser par défaut, `High availability` = **Disabled** (cf. décision de
ne pas sur-provisionner). Onglet **Backup** : `Backup retention period` = **35 days**,
`Geo-redundancy` = **Disabled**. Renseigner `Admin username`/`Password` (le mot de passe
généré et stocké dans Key Vault, jamais collé en clair ailleurs — §4). **Review + create**.

- **Activer `pgvector`** (extension autorisée au niveau serveur, puis créée dans la base) :

```bash
az postgres flexible-server parameter set \
  --resource-group "$RG" \
  --server-name "psql-${SUFFIX}" \
  --name azure.extensions \
  --value "VECTOR"

# Puis, connecté à la base cible :
# CREATE EXTENSION IF NOT EXISTS vector;
```

Vérifie (le paramètre serveur, puis l'extension une fois `CREATE EXTENSION` exécuté) :

```bash
az postgres flexible-server parameter show --resource-group "$RG" --server-name "psql-${SUFFIX}" \
  --name azure.extensions --query value -o tsv
# → doit contenir: VECTOR
```

```sql
-- connecté à la base cible via psql :
SELECT extname FROM pg_extension WHERE extname = 'vector';
-- → doit renvoyer une ligne
```

**Via le portail** : sur la page de la ressource `psql-velmo-prod` → menu de gauche
**Settings** → **Server parameters** → chercher le paramètre `azure.extensions` → dans le
champ valeur (liste à choix multiples), cocher/ajouter **VECTOR** → **Save**. Attention : le
binaire et l'extension PostgreSQL s'appellent `vector` (pas `pgvector`) — c'est ce nom qu'il
faut retrouver dans la liste et utiliser ensuite dans `CREATE EXTENSION vector;` (exécuté via
un client SQL — psql, Azure Data Studio, ou l'onglet **Query editor** du portail s'il est
disponible pour Flexible Server sur ton abonnement).

### 3.1 Test de restauration (obligatoire avant mise en prod, puis annuel)

```bash
# Point de restauration : un instant DANS la fenêtre de rétention (après la création du
# serveur, avant maintenant) — jamais une date en dur qui expire. Exemple = il y a 1 heure :
RESTORE_TIME=$(date -u -d '-1 hour' +%Y-%m-%dT%H:%M:%SZ)   # macOS : date -u -v-1H +%Y-%m-%dT%H:%M:%SZ

az postgres flexible-server restore \
  --resource-group "$RG" \
  --name "psql-${SUFFIX}-restore-test" \
  --source-server "psql-${SUFFIX}" \
  --restore-time "$RESTORE_TIME"

# Vérifie : serveur restauré prêt
az postgres flexible-server show --resource-group "$RG" --name "psql-${SUFFIX}-restore-test" --query state -o tsv
# → doit afficher: Ready
# Puis : connexion possible (psql), données attendues présentes — avant de supprimer la copie de test
az postgres flexible-server delete --resource-group "$RG" --name "psql-${SUFFIX}-restore-test" --yes
```

**Via le portail** : ressource `psql-velmo-prod` → menu de gauche **Overview** ou
**Settings → Backup and restore** → bouton **Restore** en haut de la page → choisir
`Restore type` = **Point-in-time restore**, sélectionner l'horodatage cible, donner un nom
au serveur restauré (`psql-velmo-prod-restore-test`) → **Review + create**. Une fois la
vérification faite, supprimer le serveur de test comme n'importe quelle ressource :
sélectionner la ressource → **Delete** (confirmer en tapant son nom).

Documenter la date du dernier test réussi dans un registre opérationnel (même esprit que le
registre de traitement RGPD) — un backup jamais restauré n'est qu'une hypothèse.

### 3.2 Rôles Postgres (RBAC léger, décision Q18)

```sql
-- Rôle applicatif (utilisé par le code, accès complet aux tables métier)
CREATE ROLE velmo_app WITH LOGIN PASSWORD '<secret, via Key Vault>';
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO velmo_app;

-- Rôle lecture restreinte (support/debug futur — pas de FACT/EPISODE en clair)
CREATE ROLE velmo_support_readonly WITH LOGIN PASSWORD '<secret, via Key Vault>';
GRANT SELECT ON guardrail_audit, eval_run, eval_case_result, agent_version TO velmo_support_readonly;
-- Explicitement PAS de GRANT sur fact / procedure / episode / memory_audit
```

Vérifie :

```sql
\du velmo_app velmo_support_readonly
-- → doit lister les deux rôles avec LOGIN

SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants
WHERE grantee = 'velmo_support_readonly';
-- → doit lister uniquement guardrail_audit, eval_run, eval_case_result, agent_version
```

Ces commandes sont du SQL exécuté **dans** la base, pas une opération Azure — aucun
équivalent portail : elles passent par un client SQL (psql, Azure Data Studio, ou l'onglet
**Query editor** de la ressource si proposé pour ton tier Flexible Server).

### 3.3 Migrations — Alembic, jamais `create_all` (rappel Chantier 3)

```bash
uv run alembic upgrade head
```

Vérifie :

```bash
uv run alembic current
# → doit afficher le hash de la dernière révision (head), sans "(head)" manquant en warning
```

Le rôle applicatif (`velmo_app`) ne doit recevoir que `INSERT`/`SELECT`/`UPDATE`/`DELETE` sur
les tables métier — **jamais** `CREATE`/`ALTER`/`DROP` en production. Les migrations tournent
avec un rôle distinct (`velmo_migrator`, à créer explicitement — `CREATE ROLE velmo_migrator
LOGIN PASSWORD '...'; GRANT CREATE ON DATABASE velmo TO velmo_migrator;`), exécuté **au moment
du déploiement** via `alembic upgrade head`. En CI, les workflows Postgres (`release.yml` job
`gate`, `nightly.yml` jobs `drift-check-eval`/`scheduled-eval`) lancent `alembic upgrade head`
avant toute évaluation — Alembic est l'**unique** source du schéma sur Postgres, l'application
ne fait plus de `create_all` hors SQLite (audit D2-04/D7-17). Jamais l'application en runtime.

---

## 4. Azure Key Vault — secrets

```bash
az keyvault create \
  --name "kv-${SUFFIX}" \
  --resource-group "$RG" \
  --location "$LOCATION" \
  --enable-rbac-authorization true

# Exemple de secret
az keyvault secret set --vault-name "kv-${SUFFIX}" --name "azure-openai-guard-key" --value "<clé>"
az keyvault secret set --vault-name "kv-${SUFFIX}" --name "postgres-app-password" --value "<mdp>"
```

Vérifie :

```bash
az keyvault show --name "kv-${SUFFIX}" --query properties.provisioningState -o tsv
# → doit afficher: Succeeded

az keyvault secret list --vault-name "kv-${SUFFIX}" --query '[].name' -o tsv
# → doit afficher: azure-openai-guard-key, postgres-app-password
```

**Via le portail (création + secrets)** : **Create a resource** → rechercher « Key Vault » →
**Create**. Onglet **Basics** : `Key vault name` = `kv-velmo-prod`, `Region`,
`Pricing tier` = **Standard**. Onglet **Access configuration** : `Permission model` =
**Azure role-based access control** (équivalent de `--enable-rbac-authorization true` —
important, l'ancien modèle « Vault access policy » est déconseillé pour un nouveau vault).
**Review + create**. Une fois créé : page de la ressource → menu de gauche **Objects** →
**Secrets** → **+ Generate/Import** → `Name` = `azure-openai-guard-key`, `Value` = coller la
clé → **Create**. Répéter pour chaque secret.

Accès applicatif via **identité managée** (pas de clé Key Vault stockée dans l'app elle-même).

> ⚠️ **Conditionnel — l'hébergement de l'app n'est pas encore tranché** (cf.
> `tuto_github_actions_release.md` §4.4 : pas de déploiement applicatif décidé). Ce tutoriel ne
> crée **aucun** App Service / Container App : les commandes ci-dessous s'appliquent **une fois
> que tu as créé un hôte** avec une identité managée — remplace `$APP_PRINCIPAL_ID` par le
> `principalId` de cette identité. Ne pas exécuter dans le flux nominal tant que l'hôte n'existe pas.

```bash
APP_PRINCIPAL_ID="<principalId de l'identité managée de ton hôte, une fois créé>"
KV_ID=$(az keyvault show --name "kv-${SUFFIX}" --query id -o tsv)

# Vault en mode RBAC (--enable-rbac-authorization true, plus haut) → role assignment,
# PAS `az keyvault set-policy` (les access policies sont incompatibles avec le mode RBAC).
az role assignment create \
  --assignee "$APP_PRINCIPAL_ID" \
  --role "Key Vault Secrets User" \
  --scope "$KV_ID"
```

Vérifie :

```bash
az role assignment list --assignee "$APP_PRINCIPAL_ID" --scope "$KV_ID" -o table
# → doit lister "Key Vault Secrets User" sur le Key Vault
```

**Via le portail** : sur la ressource hébergeant l'application (App Service, Container Apps,
etc.) → menu de gauche **Settings → Identity** → onglet **System assigned** → basculer
`Status` sur **On** → **Save**. Puis, sur le Key Vault → menu de gauche **Access control
(IAM)** → **+ Add** → **Add role assignment** → rôle **Key Vault Secrets User** →
`Assign access to` = **Managed identity** → sélectionner l'identité de l'application créée à
l'étape précédente → **Review + assign**. (Avec `Permission model` = RBAC choisi plus haut,
c'est **Access control (IAM)** qu'on utilise pour donner l'accès — pas l'onglet legacy
« Access policies », réservé au modèle de permission classique.)

Séparation staging/production : **deux Key Vaults distincts** (`kv-velmo-staging`,
`kv-velmo-prod`), jamais un vault partagé avec des secrets préfixés — un bug de préfixe est un
bug de fuite entre environnements.

---

## 5. Llama Guard 3 (Ollama) — CPU d'abord

Déploiement auto-hébergé, cohérent avec le choix "gratuit". Une instance conteneur simple
suffit pour démarrer (bascule vers une VM dédiée seulement si la latence mesurée l'exige) :

```bash
# Le pull du modèle est intégré à la commande de démarrage du conteneur : `az container exec`
# ne prend pas d'arguments de commande de façon fiable (limitation ACI documentée), donc on ne
# s'appuie pas dessus pour `ollama pull`.
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

Vérifie :

```bash
az container show --resource-group "$RG" --name "ollama-${SUFFIX}" --query instanceView.state -o tsv
# → doit afficher: Running

# Vérifier le pull via les logs (pas `az container exec`, cf. limitation ci-dessus) :
az container logs --resource-group "$RG" --name "ollama-${SUFFIX}" | grep -iE "llama-guard3|success"
# → la sortie doit montrer le pull de llama-guard3:8b terminé
```

**Via le portail** : **Create a resource** → rechercher « Container Instances » → **Create**.
Onglet **Basics** : `Container name` = `ollama-velmo-prod`, `Region`,
`Image source` = **Other registry**, `Image` = `ollama/ollama:latest`, `OS type` = **Linux**,
`Size` = personnaliser à 4 vCPU / 8 Go de mémoire (curseurs ou champs numériques selon la
version d'interface). Onglet **Networking** : `Ports` → ajouter le port **11434** — mais
laisser `Networking type` sur **Private** si un VNet est déjà configuré pour le groupe de
ressources (voir la note réseau ci-dessous, ce conteneur ne doit pas être public). Onglet
**Advanced** : `Restart policy` = **Always**. **Review + create**.

Pour tirer le modèle une fois le conteneur démarré : page de la ressource → menu de gauche
**Settings → Containers** → onglet **Connect** → choisir **Exec** shell (`/bin/bash` ou
équivalent) → dans le terminal intégré au navigateur, taper `ollama pull llama-guard3:8b`.

- **Mesure de latence** : instrumenter chaque appel (décorateur déjà prévu, Chantier 3) et
  suivre le p95 isolé de ce composant. Seuil de bascule vers `llama-guard3:1b` : **p95 > 800ms**
  (décision Ch.2). Bascule = `ollama pull llama-guard3:1b` + changement de la config
  (`LLAMA_GUARD_MODEL`), pas un redéploiement d'infra.
- **GPU** : n'ajouter un `Standard_NC*` (VM avec GPU) que si le 1B lui-même dépasse le seuil —
  jamais en prévention.
- **Réseau** : ce conteneur ne doit **pas** être exposé publiquement — accessible uniquement
  depuis le réseau interne de l'application (VNet + règle de pare-feu), c'est un composant de
  sécurité interne, pas un service public.

---

## 6. `RuleBasedJudge` en shadow mode — implémentation

Rappel de la décision (Ch.2, Q6) : le repli de secours pour G5/G6 tourne **en continu**, pas
seulement pendant une panne, pour être exercé et mesuré avant d'être réellement sollicité.

Principe d'implémentation (indépendant d'Azure, à coder dans `src/velmo/guardrails/`) :

1. Sur chaque message entrant/sortant, appeler **les deux** : le juge cloud (résultat utilisé)
   **et** `RuleBasedJudge` (résultat **jamais** utilisé tant que le juge cloud répond).
2. Logger les deux verdicts dans `guardrail_audit` avec un champ `shadow_verdict` distinct de
   `action` — permet de comparer sans jamais influencer la décision réelle en mode nominal.
3. Un job d'analyse (hebdomadaire, pas dans le chemin critique) calcule le taux de divergence
   entre juge cloud et `RuleBasedJudge` — sert à durcir les motifs du repli avant qu'une vraie
   panne ne le sollicite en conditions réelles.
4. Bascule réelle (juge cloud indisponible détecté par timeout) : le pipeline utilise alors le
   verdict `RuleBasedJudge` comme décision — code déjà exercé, pas un chemin froid.

---

## 7. Escalade humaine — canal gratuit (pas d'outil de ticketing pour l'instant)

Décision Ch.2 (Q7) : deux canaux séparés (support G2, sécurité G7/récidive G6), sans outil
dédié tant que l'équipe est solo. Option gratuite et simple : **Azure Logic Apps** (tier
gratuit, quota mensuel d'exécutions) déclenché par un webhook, envoyant un e-mail :

**Via le portail (recommandé — la définition JSON complète n'est pas triviale à écrire à la
main)** : `portal.azure.com` → **Create a resource** → « Logic App » → **Consumption** (tier à
quota gratuit) → `Name` = `escalade-guardrails`, `$RG`, `$LOCATION` → **Create**. Puis **Logic
app designer** → trigger **When a HTTP request is received** → action **Send an email (V2)**
(connecteur Outlook/Gmail gratuit) → **Save**. L'URL de webhook générée est celle que le canal
d'escalade appelle.

Pour une définition **versionnée** (plutôt que dessinée à la main, non reproductible),
l'exporter une fois puis la recréer depuis le fichier :

```bash
# Export (après l'avoir créée au portail) :
az logic workflow show --resource-group "$RG" --name "escalade-guardrails" \
  --query definition > deploy/logic-app/escalade.json
# Recréation reproductible depuis le fichier versionné :
az logic workflow create --resource-group "$RG" --name "escalade-guardrails" \
  --location "$LOCATION" --definition @deploy/logic-app/escalade.json
```

Vérifie :

```bash
az logic workflow show --resource-group "$RG" --name "escalade-guardrails" --query state -o tsv
# → doit afficher: Enabled
```

**Via le portail (recommandé pour ce composant — le designer visuel est plus simple que
d'écrire la définition JSON à la main)** : **Create a resource** → rechercher « Logic App » →
choisir **Consumption** (tier gratuit dans la limite du quota mensuel, pas **Standard** qui
facture en continu) → **Create**. `Name` = `escalade-guardrails`, `Region`. Une fois créée :
page de la ressource → **Logic app designer** s'ouvre automatiquement (ou menu de gauche
**Development Tools → Logic app designer**) → choisir le déclencheur **When a HTTP request
is received** (donne une URL de webhook une fois sauvegardé) → **+ New step** → chercher le
connecteur **Office 365 Outlook** (ou **Gmail**) → action **Send an email (V2)** →
renseigner `To`/`Subject`/`Body` (peut référencer les champs du corps JSON reçu par le
webhook, injectés dynamiquement par le designer) → **Save**. L'URL générée par le
déclencheur HTTP (visible en cliquant dessus dans le designer) est celle que le code
applicatif appelle en effet de bord sur `INSERT guardrail_audit(action='block_escalate')`.

Le code applicatif appelle ce webhook en effet de bord sur `INSERT guardrail_audit(action=
'block_escalate')` — voir le trigger décrit dans le doc de conception. Alternative encore plus
simple si Logic Apps semble excessif : SMTP direct depuis l'app vers une boîte mail dédiée
(zéro coût, zéro service Azure supplémentaire) — suffisant pour un volume d'escalades faible.

---

## 8. CI/CD — GitHub Actions ↔ Azure sans secrets statiques (OIDC)

Éviter de stocker une clé Azure en clair dans les secrets GitHub — utiliser la fédération
d'identité OIDC :

> **Procédure de référence : `tuto_github_actions_release.md` §2.3.** L'app registration, le
> service principal, la federated credential et la pose des 3 secrets y sont détaillés une
> seule fois (nom d'app `velmo-v2-github-actions`, rôle **Reader** sur le groupe de ressources).
> Ci-dessous, la variante côté Azure — **utilise les mêmes noms** pour éviter la divergence.
>
> **Réconciliation des noms (important).** Ce tutoriel crée des ressources sous des noms
> d'exemple (`$RG`, `aif-${SUFFIX}-async`, …). Les noms que **tu** choisis doivent être
> reportés dans les **variables GitHub Actions** consommées par `nightly.yml`
> (`AZURE_RESOURCE_GROUP`, `AZURE_AI_INFERENCE_ACCOUNT`, `AZURE_OPENAI_GUARD_ACCOUNT`,
> `AZURE_FOUNDRY_ACCOUNT` — cf. `tuto_github_actions_release.md` §2.4). Les valeurs `sconan*`
> qui y figurent sont l'infra réelle de ce repo, pas un nom imposé : remplace-les par les tiens.

```bash
az ad app create --display-name "velmo-github-actions"
az ad app federated-credential create \
  --id "<app-id>" \
  --parameters '{
    "name": "velmo-main-branch",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:sofiane-git/velmo-v2:ref:refs/heads/main",
    "audiences": ["api://AzureADTokenExchange"]
  }'
```

Vérifie :

```bash
az ad app federated-credential list --id "<app-id>" --query '[].name' -o tsv
# → doit afficher: velmo-main-branch
```

**Via le portail** : `portal.azure.com` → rechercher « Microsoft Entra ID » (ex-Azure Active
Directory) → menu de gauche **App registrations** → **+ New registration** → `Name` =
`velmo-github-actions`, laisser les autres champs par défaut → **Register**. Sur la page de
l'application créée → menu de gauche **Certificates & secrets** → onglet **Federated
credentials** → **+ Add credential** → `Federated credential scenario` = **GitHub Actions
deploying Azure resources** → renseigner `Organization`/`Repository`/`Entity type` = **Branch**,
`GitHub branch name` = `main` → **Add**. Noter ensuite, sur la page **Overview** de
l'application, `Application (client) ID` et `Directory (tenant) ID` — ce sont les valeurs
`client-id`/`tenant-id` attendues par `azure/login@v2` côté GitHub Actions. Il reste à donner
les droits nécessaires à cette identité sur le groupe de ressources : `rg-velmo-prod` →
**Access control (IAM)** → **+ Add role assignment** → rôle **Contributor** →
`Assign access to` = **User, group, or service principal** → sélectionner
`velmo-github-actions` → **Review + assign**.

Vérifie :

```bash
az role assignment list --assignee "<app-id>" --resource-group "$RG" -o table
# → doit lister Contributor sur rg-velmo-prod
```

Puis dans `nightly.yml` (job `check-model-drift` — le **seul** workflow qui appelle Azure, pas
`quality.yml`) : `azure/login@v2` avec `client-id`/`tenant-id`/`subscription-id` **en secrets**
(`AZURE_CLIENT_ID`/`AZURE_TENANT_ID`/`AZURE_SUBSCRIPTION_ID`), `federated-token` géré
automatiquement par GitHub Actions — aucune clé Azure à faire tourner/rotater manuellement.

---

## 9. Tarification — vérification périodique (décision Ch.3)

La table de tarifs (config versionnée, `velmo.config` — `token_pricing`) doit être vérifiée
contre les prix réels Azure à intervalle régulier, **pour chacun des deux vendors** (Azure
OpenAI pour le juge garde-fous, Azure AI Foundry pour l'extracteur/juge DeepEval — tarifs
distincts, pas de raison qu'ils dérivent au même rythme) :

**Via le portail (seul chemin pertinent — pas d'équivalent CLI simple pour une consultation
visuelle)** : `portal.azure.com` → rechercher « Cost Management » → **Cost analysis** →
`Scope` = le groupe de ressources `rg-velmo-prod` → filtrer (`Add filter`) par `Resource`
en tapant `aoai-${SUFFIX}` (juge garde-fous) ou `aif-${SUFFIX}` (extracteur/DeepEval) →
ajuster la période (`Granularity` = mensuel) → comparer le coût affiché au coût calculé par
l'instrumentation (`eval_run.cost_per_conv` agrégé, cf. Chantier 3) — c'est cette comparaison
qui révèle une dérive de tarif à corriger dans `token_pricing`.

- Trimestriel, ou dès qu'un écart notable apparaît entre facture réelle et coût recalculé.

---

## 10. Langfuse Cloud (décision Ch.3, révisée)

Décision révisée : projet pédagogique, pas de vraies conversations client en prod → **Langfuse
Cloud, région EU** plutôt que self-host. Aucune ressource Azure à provisionner ici — voir
`deploy/langfuse/README.md` pour la procédure (compte, projet, clés API). Self-host (module
Terraform officiel `langfuse/langfuse-terraform-azure`, région UE) resterait la bonne pratique
si ce projet traitait un jour de vraies données client — voir conception §Gouvernance RGPD.

Les clés API du projet Langfuse Cloud (`langfuse-public-key`, `langfuse-secret-key`) peuvent
être stockées dans Key Vault (§4) comme les autres secrets. Alimentent
`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/`LANGFUSE_BASE_URL` côté application.

---

## 11. Azure AI Language + Azure AI Content Safety — garde-fous Chantier 2

Deux ressources distinctes, lues respectivement par `pii_redaction.py`
(`TextAnalyticsClient`) et `prompt_shields.py` (appel REST `text:shieldPrompt`) —
voir `src/velmo/guardrails/`.

```bash
# PII redaction en texte libre — Azure AI Language
az cognitiveservices account create \
  --name "lang-${SUFFIX}" \
  --resource-group "$RG" \
  --location "$LOCATION" \
  --kind TextAnalytics \
  --sku S \
  --custom-domain "lang-${SUFFIX}"

# Prompt Shields — Azure AI Content Safety
az cognitiveservices account create \
  --name "cs-${SUFFIX}" \
  --resource-group "$RG" \
  --location "$LOCATION" \
  --kind ContentSafety \
  --sku S0 \
  --custom-domain "cs-${SUFFIX}"

# Récupération clé + endpoint (identique pour les deux ressources)
az cognitiveservices account keys list --name "lang-${SUFFIX}" --resource-group "$RG"
az cognitiveservices account show --name "lang-${SUFFIX}" --resource-group "$RG" --query properties.endpoint -o tsv
```

Vérifie :

```bash
az cognitiveservices account show --name "lang-${SUFFIX}" --resource-group "$RG" --query provisioningState -o tsv
az cognitiveservices account show --name "cs-${SUFFIX}" --resource-group "$RG" --query provisioningState -o tsv
# → doit afficher: Succeeded (les deux)
```

**Via le portail** : **Create a resource** → rechercher « Language service » (pour PII) ou
« Content Safety » → `Name` = `lang-velmo-prod` / `cs-velmo-prod`, `Pricing tier` = **S**
(Language) / **S0** (Content Safety) → **Review + create**. Une fois créée : page de la
ressource → menu de gauche **Resource Management** → **Keys and Endpoint**.

Les valeurs récupérées alimentent `AZURE_LANGUAGE_ENDPOINT`/`AZURE_LANGUAGE_KEY` et
`AZURE_CONTENT_SAFETY_ENDPOINT`/`AZURE_CONTENT_SAFETY_KEY` (voir `.env.example`,
`src/velmo/config.py`). Contrairement à `AZURE_AI_INFERENCE_*` (§2.4), ces deux intégrations
restent **optionnelles par conception** — absence des deux variables d'un couple = repli
gracieux (`pii_redaction.py`/`prompt_shields.py` en no-op), y compris en production.
`validate_startup()` (`src/velmo/config.py`) échoue seulement sur un couple à moitié
renseigné (typo, oubli d'une des deux variables) — pas sur l'absence complète.

---

## Vérification finale — smoke test bout-en-bout

Une fois toutes les ressources créées et les secrets dans Key Vault, valider la chaîne
complète **app → LLM → DB → garde-fous** avant de considérer le déploiement prêt :

```bash
# 1. Renseigner .env depuis Key Vault (adapter les noms de secrets créés au §4) :
export AZURE_AI_INFERENCE_ENDPOINT=$(az keyvault secret show --vault-name "kv-${SUFFIX}" --name azure-ai-inference-endpoint --query value -o tsv)
export AZURE_AI_INFERENCE_API_KEY=$(az keyvault secret show --vault-name "kv-${SUFFIX}" --name azure-ai-inference-api-key --query value -o tsv)
# ... idem pour les autres couples endpoint/clé (guard, foundry, language, content-safety).

# 2. Config cohérente — échoue si un couple endpoint/clé est à moitié renseigné :
ENVIRONMENT=production uv run python -c "from velmo.config import validate_startup; validate_startup(); print('config OK')"

# 3. Un échange agent réel (LLM principal + mémoire + garde-fous entrée/sortie) :
uv run python -m velmo.cli --user smoke-test <<< "Quel est le statut de ma commande O-2024-0101 ?"
```

Attendu : l'étape 2 affiche `config OK` (aucune variable Azure manquante ni à moitié posée) ;
l'étape 3 renvoie une **vraie réponse de l'agent** (pas le repli `EchoLLM`, pas un refus
garde-fou sur une question légitime). Un échec ici = une variable ou une ressource mal câblée,
à corriger **avant** la mise en ligne.

---

## Récapitulatif des ressources créées

| Ressource                     | Rôle                                                    |
| ------------------------------ | -------------------------------------------------------- |
| `aif-${SUFFIX}-async`         | Extracteur mémoire + juge DeepEval (`claude-opus-4-5`, Azure AI Foundry, partagé, async) |
| `aoai-${SUFFIX}-guard`        | Juge garde-fous (`gpt-5-mini`, Azure OpenAI, dédié, chemin bloquant) |
| `aoai-${SUFFIX}-chat`         | Agent principal (Mistral-Large-3, Azure AI Inference)    |
| `psql-${SUFFIX}`              | PostgreSQL + `pgvector` — mémoire, audit, éval, PITR     |
| `kv-${SUFFIX}`                | Secrets (par environnement : staging/prod séparés)       |
| `ollama-${SUFFIX}`            | Llama Guard 3 auto-hébergé (CPU, 8B par défaut)          |
| `lang-${SUFFIX}`              | PII redaction texte libre (Azure AI Language)            |
| `cs-${SUFFIX}`                | Prompt Shields (Azure AI Content Safety)                 |
| Logic App `escalade-guardrails` | Notification d'escalade (canal gratuit)                |

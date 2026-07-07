# Prouver qu'on ne régresse jamais

## Ce que ce schéma raconte

À chaque modification de l'agent, une batterie de tests rejoue les mêmes scénarios et produit des notes. Si une seule dimension baisse trop, la livraison est bloquée automatiquement. `develop` intègre en continu et alimente seule l'environnement de staging ; `main` ne reçoit que des versions déjà validées en conditions réelles ; un correctif prod urgent passe par un chemin court-circuit dédié.

```mermaid
flowchart TB
    subgraph MAIN[" "]
        FIX["📋 Fixtures figées<br/>(cases/*.jsonl)"]

        N1["① Code sur feature/*<br/>(part de develop)"] --> N2["② PR vers develop<br/>lint + unitaires + revue"]
        N2 -->|"vert + approuvée"| N3["③ Merge dans develop<br/>→ redéploie staging automatiquement"]
        N3 --> N4["④ PR develop → main<br/>(quand prêt à livrer)"]
        N4 --> N5["⑤ CI : rejoue les 3 suites<br/>(mémoire · garde-fous · qualité)<br/>contre staging"]
        FIX -.-> N5
        N5 -->|"une note sous le seuil"| C1["✗ Correctif requis<br/>(retour en ①, via develop)"]
        C1 -.-> N2
        N5 -->|"tout vert"| N6["⑥ Validation manuelle<br/>sur staging"]
        N6 -->|"non"| C1
        N6 -->|"oui"| N7["⑦ Merge develop → main"]
        N7 --> N8["⑧ Retag production<br/>(pas de rebuild — même artefact)"]
        N8 --> N9["⑨ Monitoring continu<br/>+ suites rejouées en nightly"]
        N9 -->|"régression détectée"| N10["⑩ Rollback :<br/>réétiquette la version précédente"]
        N7 --> REP["📊 mlops/report.md"]
        N9 --> REP

        MBUG(["Régression détectée en prod"]) --> HF["hotfix/* part de main<br/>(pas d'attente sur develop)"]
        HF --> HFC["Suites réduites<br/>(garde-fous + mémoire, skip qualité)"]
        HFC --> HFV{"Validation allégée<br/>(1 reviewer)"}
        HFV -->|"oui"| HFM["Merge hotfix/* → main ET develop<br/>(sync obligatoire)"]
        HFM --> N8
    end

    MAIN --- LG1

    subgraph LEGEND["Légende"]
        direction LR
        LG1["① Développement"]
        LG2["② ③ PR + merge vers develop"]
        LG3["④ ⑤ ⑥ Release : develop → main"]
        LG4["⑦ ⑧ ⑨ Production"]
        LG5["Correctif / rollback"]
        LG6["Hotfix prod urgent"]
        LG7["Fixtures / rapport"]
    end

    classDef dev fill:#b2dfdb,stroke:#00796b,color:#004d40;
    classDef ci fill:#e3f2fd,stroke:#1565c0,color:#0d47a1;
    classDef stg fill:#fff9c4,stroke:#f9a825,color:#5c4400;
    classDef prod fill:#ffe0b2,stroke:#e65100,color:#4e2600;
    classDef fail fill:#ffcdd2,stroke:#c62828,color:#5c0000;
    classDef fixture fill:#eeeeee,stroke:#616161,color:#212121;
    classDef hotfix fill:#f8bbd0,stroke:#ad1457,color:#880e4f;
    class N1,LG1 dev;
    class N2,N3,LG2 ci;
    class N4,N5,N6,LG3 stg;
    class N7,N8,N9,LG4 prod;
    class C1,N10,LG5 fail;
    class FIX,REP,LG7 fixture;
    class MBUG,HF,HFC,HFV,HFM,LG6 hotfix;
    style MAIN fill:none,stroke:none;
    %% connecteur invisible (force la légende en bas) — index = ordre des flèches, recalculer si le flux change
    linkStyle 21 stroke:none;
```

## Les dix étapes, en une phrase chacune

1. **Code** sur une branche `feature/*` — part de `develop`, libre, aucune contrainte.
2. **PR ouverte** vers `develop` — palier gratuit : lint, tests unitaires, revue de code, pas de suites LLM.
3. **Merge dans `develop`** — redéploie automatiquement l'environnement de staging. Pas de file d'attente : les merges se sérialisent déjà via Git.
4. **PR `develop` → `main`** — ouverte quand l'équipe juge prêt à livrer, pas à chaque feature. C'est le gate de release.
5. **CI de release** : rejoue les 3 batteries de tests contre les fixtures figées, sur staging déjà déployé. Une note sous le seuil → bloqué, correctif via `develop`, retour à l'étape 1.
6. **Validation manuelle sur staging** — le seul geste humain du cycle normal. Refusé → correctif, retour à l'étape 1.
7. **Merge `develop` → `main`** — autorisé seulement après le vert de l'étape 6.
8. **Retag production** — pas un nouveau déploiement, juste une étiquette qui change (même artefact déjà validé).
9. **Monitoring continu** en prod + les 3 suites rejouées chaque nuit.
10. **Rollback** si régression détectée : réétiquette la version précédente, pas de redéploiement.

**Hotfix (hors cycle normal)** : régression détectée en prod → `hotfix/*` part de `main` directement → suites réduites (garde-fous + mémoire, la qualité étant bruitée et non bloquante en urgence) → validation allégée → merge dans `main` **et** `develop` (sync obligatoire, sinon la régression revient à la prochaine release).

## Les points traités dans ce document

- **Trois batteries de tests, une par chantier** : la mémoire (l'info du début ressort-elle après 30 tours ? le client revenu est-il reconnu ? l'oubli est-il effectif ?), les garde-fous (chaque type d'attaque est-il bloqué ? les messages légitimes passent-ils ?), et la qualité générale des réponses (pertinence, ton, exactitude — notée par des métriques spécialisées).
- **Des scénarios figés, jamais générés à la volée** : si les cas de test changeaient à chaque exécution, impossible de savoir si une note a bougé à cause de l'agent ou à cause des cas. Ici, seul l'agent change entre deux exécutions — tout écart lui est imputable.
- **Qu'est-ce qu'une « version » de l'agent** : le prompt + les réglages de mémoire + les réglages de garde-fous, identifiés par une empreinte calculée automatiquement. Impossible d'oublier de « changer le numéro de version » : toute modification change l'empreinte.
- **Le verdict se joue dimension par dimension, jamais sur la moyenne** — le piège classique : si les garde-fous chutent de 20 points mais que la qualité gagne 10, une moyenne pourrait rester au vert… alors qu'un garde-fou a disparu. Chaque dimension a son propre seuil ; une seule qui flanche suffit à bloquer.
- **Ne pas bloquer pour du bruit** : les notes mémoire et garde-fous sont vérifiables exactement (l'info ressort ou non), donc seuil ferme avec une petite marge. La note de qualité, elle, est un jugement d'IA, naturellement fluctuant : on la compare à la version précédente (« pas de baisse de plus de X % ») plutôt qu'à un seuil absolu.
- **Ce qu'on surveille en continu** dans le rapport de suivi : la note mémoire, le taux de blocage des garde-fous, le taux de faux positifs, la durée de réponse et le coût par conversation — décomposés par composant, car chaque garde-fou et chaque extracteur ajoute un appel qui coûte et qui prend du temps.
- **Deux outils, deux responsabilités** : PostgreSQL garde les verdicts (rapide à interroger, c'est lui qui décide), Langfuse garde le détail de chaque appel (coût, durée) — aucune donnée dupliquée.
- **`develop` comme source unique de staging** : plus besoin de file d'attente ni de verrou de concurrence — les PR se sérialisent naturellement au merge dans `develop`, staging reflète toujours son état courant.
- **Merge `develop → main` = feu vert prod, sans nouveau déploiement** (étapes 7–8) : le geste humain a eu lieu une fois, sur staging ; il n'est pas dupliqué après le merge.
- **`hotfix/*` : le chemin qu'un modèle plus simple oublierait** : une régression en prod ne doit pas attendre le prochain cycle de release complet — le hotfix part de `main`, passe par une validation allégée, et se propage obligatoirement vers `develop` pour ne pas être perdu à la prochaine livraison normale.

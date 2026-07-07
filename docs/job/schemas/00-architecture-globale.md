# Architecture globale de Velmo 2.0

## Ce que ce schéma raconte

C'est le trajet d'un message client, de son arrivée jusqu'à la réponse. Chaque message traverse cinq étapes dans un ordre précis, et cet ordre n'est pas un hasard : on vérifie **avant** de réfléchir, et on vérifie **encore** avant de répondre.

```mermaid
flowchart TB
    subgraph MAIN[" "]
        direction LR
        U["👤 Client Velmo<br/>(identifié par sa session)"] --> GIN{"🛡️ Contrôle du message<br/>AVANT l'agent"}
        GIN -->|"message dangereux"| REF["Refus poli<br/>+ trace dans le journal"]
        GIN -->|"message sain"| MEMR["🧠 L'agent RELIT sa mémoire<br/>ce qu'il sait déjà de ce client"]
        MEMR --> LLM["🤖 L'agent réfléchit et répond<br/>consulter : librement<br/>agir : seulement après confirmation"]
        LLM --> GOUT{"🛡️ Contrôle de la réponse<br/>AVANT le client"}
        GOUT -->|"réponse à risque"| REF
        GOUT -->|"réponse saine"| MEMW["🧠 L'agent RETIENT<br/>ce qui mérite d'être gardé"]
        MEMW --> RESP["💬 Réponse au client"]

        LLM -.->|"cas trop sensibles :<br/>gros remboursement, colis déjà parti,<br/>soupçon de contrefaçon"| ESC["🙋 Un humain prend le relais"]
    end

    MAIN --- LG1

    subgraph LEGEND["Légende"]
        direction LR
        LG1["🛡️ Garde-fou"]
        LG2["🧠 Mémoire"]
        LG3["🤖 Agent / flux"]
        LG4["⛔ Refus / escalade"]
    end

    classDef gate fill:#ffe0b2,stroke:#e65100,color:#4e2600;
    classDef mem fill:#c8e6c9,stroke:#2e7d32,color:#1b5e20;
    classDef flow fill:#e3f2fd,stroke:#1565c0,color:#0d47a1;
    classDef block fill:#ffcdd2,stroke:#c62828,color:#5c0000;
    class GIN,GOUT,LG1 gate;
    class MEMR,MEMW,LG2 mem;
    class U,LLM,RESP,LG3 flow;
    class REF,ESC,LG4 block;
    style MAIN fill:none,stroke:none;
    %% connecteur invisible (force la légende en bas) — index = ordre des flèches, recalculer si le flux change
    linkStyle 9 stroke:none;
```

## Les points traités dans ce document

- **Le contexte métier** : Velmo vend des maillots de foot collector, souvent une seule pièce par taille. Les clients sont des passionnés qui reviennent — un client qui a déjà donné sa pointure déteste la redonner.
- **Le rôle de l'agent** : traiter seul les demandes simples (suivi, taille, adresse, annulation, retour, remboursement), en gardant le contexte de chaque client dans le temps.
- **La règle d'or de l'agent** : il peut _consulter_ librement (commandes, stock, livraison), mais il n'_agit_ jamais sans confirmation du client, et il passe la main à un humain dès qu'un cas est sensible.
- **Pourquoi tout reconstruire** : l'ancien agent a été rafistolé trop de fois ; un audit externe impose de repartir de zéro sur trois exigences.
- **Les trois exigences de l'audit**, qui deviennent les trois chantiers :
  1. une **mémoire exemplaire** (chantier 1),
  2. des **garde-fous sérieux** (chantier 2),
  3. une **qualité mesurée en continu**, qui prouve qu'aucune version ne régresse (chantier 3).

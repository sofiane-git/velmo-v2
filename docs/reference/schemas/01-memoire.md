# Comment l'agent se souvient

## Ce que ce schéma raconte

L'agent n'a pas _une_ mémoire mais **trois**, comme un humain : ce qu'il a en tête à l'instant, le fil de la conversation en cours, et ce qu'il retient durablement d'une personne. Le schéma montre comment ces trois mémoires alimentent chaque réponse, et qui a le droit d'y écrire.

```mermaid
flowchart TB
    subgraph MAIN[" "]
        subgraph WK["⬛ Mémoire de TRAVAIL — ce que l'agent a en tête, jamais stockée"]
            PROMPT["Le contexte du tour, assemblé à la volée :<br/>consignes + faits connus + souvenirs pertinents + derniers échanges"]
        end

        subgraph ST["🟦 MÉMOIRE COURTE — la conversation en cours"]
            MSG["Fil complet des messages<br/>+ résumé automatique quand ça devient trop long<br/><i>stocké dans PostgreSQL</i>"]
        end

        subgraph LT["🟩 MÉMOIRE LONGUE — ce qui survit d'une visite à l'autre"]
            FACT["LES FAITS — le « quoi »<br/>« il fait du 42 », « client pro »<br/><i>PostgreSQL</i>"]
            PROC["LES HABITUDES — le « comment »<br/>« préfère un avoir à un remboursement »<br/><i>PostgreSQL</i>"]
            EPI["LES ÉVÉNEMENTS — le « quand »<br/>« litige contrefaçon le 12 juin, escaladé »<br/><i>PostgreSQL + ChromaDB</i>"]
        end

        MSG -->|"la conversation entière<br/>(ou son résumé si trop longue)"| PROMPT
        FACT -->|"tous, à chaque tour<br/>(ils sont peu nombreux)"| PROMPT
        PROC -->|"comme des consignes<br/>de comportement"| PROMPT
        EPI -->|"seulement les plus proches<br/>du sujet du moment"| PROMPT
        PROMPT --> AG["🤖 L'agent répond"]
        AG -->|"automatique, chaque message"| MSG
        AG -->|"un second cerveau, l'EXTRACTEUR,<br/>décide ce qui mérite d'être retenu<br/>(seulement s'il est assez sûr)"| FACT
        AG -->|"idem"| PROC
        AG -->|"en fin de conversation :<br/>résumé de l'échange"| EPI
    end

    MAIN --- LG1

    subgraph LEGEND["Légende"]
        direction LR
        LG1["Mémoire courte<br/>(session)"]
        LG2["Mémoire longue<br/>(persistante)"]
        LG3["Mémoire de travail<br/>(non stockée)"]
    end

    classDef ct fill:#bbdefb,stroke:#1565c0,color:#0d47a1;
    classDef lt fill:#c8e6c9,stroke:#2e7d32,color:#1b5e20;
    classDef wk fill:#eeeeee,stroke:#616161,color:#212121;
    class MSG,LG1 ct;
    class FACT,PROC,EPI,LG2 lt;
    class PROMPT,AG,LG3 wk;
    style MAIN fill:none,stroke:none;
    %% connecteur invisible (force la légende en bas) — index = ordre des flèches, recalculer si le flux change
    linkStyle 9 stroke:none;
```

## Les points traités dans ce document

- **Tenir une longue conversation sans rien perdre** : même après 30 tours, une information donnée au tout début doit ressortir. Astuce clé : les faits importants sont extraits et rangés à part _avant_ que la conversation ne soit résumée — ils ne sont jamais noyés dans un résumé.
- **Se souvenir d'une visite à l'autre** : la pointure, le statut « client pro », un litige en cours — rechargés automatiquement quand le client revient, même des jours plus tard.
- **Trois natures de souvenirs durables** : les _faits_ (ce qui est vrai sur le client), les _habitudes_ (comment se comporter avec lui), les _événements_ (ce qui s'est passé). Trois natures, trois rangements — parce qu'on ne les utilise pas pareil au moment de répondre.
- **Qui décide de retenir ?** Pas l'agent qui répond : un **second composant dédié**, l'extracteur, analyse l'échange après coup. Il ne retient que ce qui sera encore utile demain, et seulement s'il est suffisamment sûr de lui (un score de confiance avec une barre à franchir — au-dessous, on jette par prudence).
- **Cloisonnement strict** : chaque souvenir est étiqueté avec l'identifiant du client, et toute lecture est filtrée par cet identifiant. La mémoire d'un client n'est jamais visible d'un autre. L'identifiant vient de la session authentifiée, jamais du texte du message (sinon on pourrait se faire passer pour un autre).
- **Droit à l'oubli (RGPD)** : « oublie mon numéro de commande » déclenche une suppression **physique** et réelle — pas un simple marquage — dans les deux bases à la fois, avec une trace prouvant la suppression.
- **Transparence** : on peut à tout moment lister tout ce que l'agent a retenu d'un client, avec l'origine et la date de chaque souvenir (un journal inaltérable note chaque écriture et chaque effacement).
- **Deux outils de stockage, deux rôles** : PostgreSQL est _le classeur_ (la vérité exacte, facile à supprimer proprement) ; ChromaDB est _le moteur de recherche_ (retrouver les événements passés qui ressemblent à la question du moment).

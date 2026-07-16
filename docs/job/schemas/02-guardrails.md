# Les garde-fous

## Ce que ce schéma raconte

Deux portes de sécurité encadrent l'agent : une à l'entrée (le message du client), une à la sortie (la réponse de l'agent). Chaque porte utilise les mêmes trois techniques de détection, de la moins chère à la plus fine.

```mermaid
flowchart LR
    subgraph MAIN[" "]
        subgraph PORTES["Deux portes autour de l'agent"]
            direction LR
            GIN["🛡️ PORTE D'ENTRÉE<br/>arrête haine, violence, contenu sexuel,<br/>tentatives de manipulation, données bancaires<br/>→ couper le plus tôt possible"] --> AGENT["🤖 Agent"] --> GOUT["🛡️ PORTE DE SORTIE<br/>revérifie tout + retient données bancaires,<br/>secrets internes, réponses hors sujet<br/>→ le filet de sécurité"]
        end

        subgraph PIPE["Les trois techniques, à chaque porte"]
            E1["1️⃣ MOTIFS EXACTS (regex)<br/>gratuit, instantané, infaillible sur son domaine :<br/>numéros de carte, mots de passe, phrases<br/>de manipulation connues"]
            E2["2️⃣ CLASSIFIEUR DE MODÉRATION<br/>service spécialisé, entraîné sur des millions<br/>de textes : haine, violence, contenu sexuel"]
            E3["3️⃣ SECOND MODÈLE « JUGE »<br/>comprend le contexte : manipulation reformulée,<br/>réponse qui sort du rôle de support,<br/>fuite déguisée"]
            E1 -->|"manipulation flagrante : stop net<br/>donnée sensible repérée : on la masque<br/>et le reste continue"| PAR(["2 et 3 en parallèle :<br/>ils ne cherchent pas<br/>les mêmes choses"])
            PAR --> E2
            PAR --> E3
            E2 --> AGG{"Verdict selon<br/>le niveau d'alerte"}
            E3 --> AGG
            AGG -->|"alerte forte"| BLK["Blocage — refus poli,<br/>sans révéler la règle déclenchée"]
            AGG -->|"cas limite"| FLG["Ça passe, mais on le note<br/>pour ajuster les réglages"]
            AGG -->|"rien à signaler"| OK["Ça passe"]
        end

        PORTES --> PIPE
        BLK --> LOG[("Journal de sécurité<br/>chaque blocage tracé ·<br/>un humain alerté pour les cas graves")]
        FLG --> LOG
    end

    MAIN --- LG1

    subgraph LEGEND["Légende"]
        direction LR
        LG1["🛡️ Porte de contrôle"]
        LG2["Étape de détection"]
        LG3["Bloqué"]
        LG4["Cas limite, noté"]
        LG5["OK / journal"]
    end

    classDef gate fill:#ffe0b2,stroke:#e65100,color:#4e2600;
    classDef stage fill:#e3f2fd,stroke:#1565c0,color:#0d47a1;
    classDef bad fill:#ffcdd2,stroke:#c62828,color:#5c0000;
    classDef warn fill:#fff9c4,stroke:#f9a825,color:#5c4400;
    classDef good fill:#c8e6c9,stroke:#2e7d32,color:#1b5e20;
    class GIN,GOUT,LG1 gate;
    class E1,E2,E3,AGG,PAR,AGENT,LG2 stage;
    class BLK,LG3 bad;
    class FLG,LG4 warn;
    class OK,LOG,LG5 good;
    style MAIN fill:none,stroke:none;
    %% connecteur invisible (force la légende en bas) — index = ordre des flèches, recalculer si le flux change
    linkStyle 13 stroke:none;
```

## Les points traités dans ce document

- **Ce qu'on bloque** (à l'entrée comme à la sortie) : la haine et le harcèlement ; la violence et les menaces ; le contenu sexuel ; les données personnelles sensibles, structurées (numéros de carte, IBAN, mots de passe — bloquées dès l'entrée) ou en texte libre dans les réponses (données d'autres clients) ; les réponses hors du rôle de support (conseil juridique ou médical, promesses engageant Velmo) ; les tentatives de manipulation de l'agent (« ignore tes instructions… ») ; les fuites de secrets techniques internes.
- **Pourquoi deux portes et pas une seule** : si on ne contrôlait qu'à la sortie, l'agent aurait déjà _lu et raisonné_ sur un contenu toxique ou manipulateur — le mal serait fait. La porte d'entrée coupe tôt ; la sortie rattrape ce qui a échappé.
- **Pourquoi trois techniques et pas une** : chaque risque appelle l'outil le moins cher qui suffit. Un numéro de carte se repère à coup sûr par un motif — payer un appel d'IA pour ça serait absurde. Une manipulation reformulée, à l'inverse, échappe à tout motif fixe et exige un jugement contextuel.
- **L'équilibre sécurité / utilité** : un client furieux (« ce maillot est un scandale ! ») n'est pas une menace. Plutôt que de bloquer au moindre doute, les cas limites passent mais sont notés — et ces notes servent ensuite à ajuster les seuils sur des cas réels, pas au jugé.
- **Que se passe-t-il lors d'un blocage** : refus poli et générique (jamais « j'ai détecté votre manipulation » — ce serait donner à l'attaquant de quoi ajuster son attaque), trace dans un journal de sécurité, et alerte humaine pour les cas graves (menace ciblée, attaques répétées).
- **Résister à la manipulation** — l'argument le plus fort du chantier : les garde-fous ne sont **pas des instructions dans le prompt** de l'agent, mais du **code qui s'exécute en dehors** de lui. Un texte injecté peut influencer l'agent ; il ne peut pas désactiver un programme qui tourne avant et après lui. Et le modèle « juge » a son propre contexte isolé : une manipulation qui a piégé l'agent n'a aucune prise sur lui.
- **Deux journaux séparés** : le journal de sécurité est distinct du journal de mémoire, car ils n'ont pas le même régime de conservation — un client peut faire effacer sa mémoire (RGPD), mais les traces d'une tentative d'attaque peuvent être légitimement conservées pour investigation.

# Note de recommandations — audit technique Velmo 2.0

**Auteur : expert technique externe · Destinataire : équipe de réalisation**

L'agent actuel a été rapiécé une fois de trop : mémoire fragile, garde-fous posés au cas par cas, aucune mesure de qualité reproductible. Ma recommandation est sans appel : on repart de zéro sur des bases saines. Cette note fixe la stack et les exigences. L'architecture, elle, est à votre main.

## Stack imposée

- **LLM via API** : Azure AI Inference, modèle Kimi-K2.6. Aucun modèle local.
- **État conversationnel & préférences** : base relationnelle (Postgres). C'est la source de vérité des faits durables par utilisateur.
- **Mémoire long terme épisodique** : base vectorielle (Chroma) pour retrouver les souvenirs pertinents par similarité.
- **Intégration continue** : GitHub Actions, avec blocage de livraison sous seuil de qualité.

## Trois exigences non négociables

1. **Mémoire exemplaire.** Six exigences détaillées (R1 à R6 du cahier des charges) : tenue d'une longue conversation, persistance multi-session, isolation par utilisateur, tenue de la fenêtre de contexte, droit à l'oubli, traçabilité.
2. **Garde-fous sérieux.** Contrôle en entrée *et* en sortie. Aucune des catégories interdites ne doit passer dans un sens comme dans l'autre.
3. **Qualité mesurée en continu.** Chaque version doit prouver sa non-régression : suites d'évaluation, note globale, seuil bloquant en CI.

## Principes directeurs

- **Isolation stricte** par `user_id` : aucune fuite mémoire entre utilisateurs.
- **Observabilité** des décisions des garde-fous : toute décision de blocage est journalisée.
- **Traçabilité** des écritures mémoire : on doit pouvoir inspecter ce qui a été retenu.
- **Blocage CI** dès que la note passe sous le seuil, sans bloquer pour du bruit.

Tenez ces exigences et l'agent sera fiable. Bâclez-en une et on reproduira les erreurs du passé.

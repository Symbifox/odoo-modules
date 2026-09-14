{
    "name": "Registre de formation : mes formations au téléphone",
    "version": "18.0.1.0.0",
    "category": "Human Resources",
    "summary": "L'écran « mes formations » de Symbifox Mobile : ce que je dois, "
               "pour quand, et ce que j'ai déjà prouvé",
    "description": """
Registre de formation : mes formations au téléphone
===================================================

La surface mobile du registre, au patron maison : une API REST/JSON sous
``/bf_training/mobile/v1/``, jeton porteur, et **la collecte déléguée aux modèles
pour qu'elle passe par les droits de l'appelant**.

Ce que l'écran montre
---------------------

* **Ce que je dois** : mes obligations, avec leur échéance et leur état.
* **Ce que j'ai prouvé** : mes réalisations, avec leur date d'expiration.
* **Ce qu'on m'a demandé** : mes assignations en cours.

Trois refus
-----------

1. **L'API ne sert que les siennes.** Aucune route ne prend d'identifiant
   d'employé : la personne est déduite du jeton, jamais de ce que le client
   envoie. Un paramètre qu'on n'accepte pas est un paramètre qu'on ne peut pas
   forger.
2. **Rien ne s'écrit au registre depuis le téléphone.** Une preuve de formation
   se dépose, elle ne se déclare pas. L'API est en lecture seule, et la seule
   écriture offerte est l'accusé de lecture d'une assignation.
3. **Un compte sans fiche d'employé rend une liste vide, pas une erreur.**
   Quelqu'un qui n'est pas au registre n'a rien à y voir, et ce n'est pas une
   panne.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_training"],
    "installable": True,
    "application": False,
}

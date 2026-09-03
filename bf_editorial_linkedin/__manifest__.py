# -*- coding: utf-8 -*-
{
    "name": "Atelier éditorial — LinkedIn",
    "version": "18.0.1.0.1",
    "category": "Marketing",
    "summary": "Diffuser sur LinkedIn depuis l'atelier éditorial, par l'API"
               " versionnée et un jeton de membre",
    "description": """
Atelier éditorial — LinkedIn
============================

Le canal manuel fonctionne et reste installé : le texte se relit dans Odoo, on
le colle sur le réseau, un bouton relève l'URL obtenue. Ce module-ci ajoute le
canal automatique, pour qui accepte ce que LinkedIn demande en échange.

⚠️ Trois choses à savoir avant de le brancher
---------------------------------------------

1. **Il faut une application LinkedIn.** Elle se crée sur
   https://www.linkedin.com/developers/apps, se rattache à une page
   d'entreprise, et il faut lui accorder le produit « Share on LinkedIn »
   (portées `w_member_social`, `openid`, `profile`). L'approbation n'est pas
   instantanée.

2. **Le jeton expire au bout de 60 jours.** LinkedIn ne délivre de jeton de
   rafraîchissement qu'aux programmes approuvés. En pratique, quelqu'un
   recolle un jeton tous les deux mois. Le canal porte donc une date
   d'expiration, la vérification des identifiants la rappelle, et un travail
   quotidien prévient une semaine d'avance plutôt que de laisser une
   diffusion échouer un dimanche.

3. **Aucune mesure ne revient.** Les statistiques d'une publication de MEMBRE
   ne sont pas exposées par l'API : elles demandent une page d'organisation et
   d'autres portées. `_fetch_metrics` rend donc un dictionnaire vide, ce que
   le cadre lit comme « ce réseau ne les donne pas », et non comme zéro.

⚠️ La version d'API se périme
-----------------------------
L'API versionnée exige un en-tête `LinkedIn-Version` au format `AAAAMM`, et
LinkedIn retire les versions au bout d'environ un an. Elle se règle par le
paramètre `bf_editorial_linkedin.api_version` pour qu'un changement de version
ne demande pas un déploiement.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_editorial_social"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "data/bf_editorial_linkedin_data.xml",
        "views/bf_social_channel_views.xml",
    ],
    "installable": True,
    "auto_install": False,
    "application": False,
}

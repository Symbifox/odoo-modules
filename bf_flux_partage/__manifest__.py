# -*- coding: utf-8 -*-
{
    "name": "Flux RSS : partage externe",
    "summary": "Un lien secret, sans connexion, pour montrer la veille d'une liste "
               "à un client ou un partenaire : une page, et le même flux en RSS",
    "version": "18.0.1.0.3",
    "category": "Productivity",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ « Other proprietary » est la valeur que le schéma de manifeste d'Odoo
    # offre pour dire BUSL-1.1 : il n'a pas de valeur BUSL. C'est le fichier
    # LICENSE qui gouverne, pas cette ligne.
    "license": "Other proprietary",
    "installable": True,
    "depends": ["bf_flux"],
    "data": [
        "security/ir.model.access.csv",
        "views/partage_page.xml",
        "views/flux_liste_views.xml",
    ],
    "description": """
Flux RSS : partage externe
==========================

La veille d'un projet sert souvent au client autant qu'à l'équipe, et un
client n'a pas de compte. Ce module donne à une liste des liens de partage :

* **une page publique** aux couleurs de la société, qui montre les derniers
  éléments retenus en cartes ;
* **le même contenu en RSS** (le lien suivi de ``/rss``), pour s'abonner dans
  son propre lecteur ;
* **un lien par destinataire**, avec une échéance (90 jours par défaut), le
  compte des visites et la date du dernier accès. Révoquer un lien coupe une
  personne sans toucher aux autres.

Ce qui ne sort jamais
---------------------

* Le texte complet des articles : ce serait republier le contenu d'autrui sur
  une page publique. Titre, chapeau et lien, comme tout agrégateur.
* L'interne : description de la liste, motifs de rétention, notes et
  raisons du jugement, fil des projets, lectures de chacun.
* La page n'est pas indexée (``noindex``) et ne transmet pas son adresse aux
  sites visités (``no-referrer``). Un lien inconnu, échu ou révoqué répond
  la même chose : introuvable.
""",
}

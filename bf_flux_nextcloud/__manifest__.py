# -*- coding: utf-8 -*-
{
    "name": "Flux RSS : corpus Nextcloud",
    "summary": "Dépose ce qu'une liste de flux retient dans un dossier Nextcloud, "
               "en Markdown par source et par mois, pour une base de connaissances",
    "version": "18.0.1.0.2",
    "category": "Productivity",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ « Other proprietary » est la valeur que le schéma de manifeste d'Odoo
    # offre pour dire BUSL-1.1 : il n'a pas de valeur BUSL. C'est le fichier
    # LICENSE qui gouverne, pas cette ligne.
    "license": "Other proprietary",
    "installable": True,
    "auto_install": False,
    "depends": ["bf_flux", "bf_document_nextcloud_sync"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/flux_liste_views.xml",
    ],
    "description": """
Flux RSS : corpus Nextcloud
===========================

Une base de connaissances lue par un agent n'a que faire d'un canal Discuss :
elle veut des fichiers. Ce module dépose ce qu'une liste retient dans un
dossier Nextcloud, par la connexion WebDAV déjà configurée pour la
synchronisation des documents.

* **Un dossier par source, un fichier par mois.** Chaque élément y est écrit
  en Markdown : titre, émetteur, date, langue, sujets, motif de rétention,
  lien, puis le texte complet (ou le chapeau, avec la raison, quand la page
  n'a pas pu être lue).
* **Un mois qui grossit ajoute des suites**, sans jamais renommer le premier
  fichier. Le dépôt n'efface rien : un fichier renommé laisserait l'ancien à
  côté du nouveau, avec le même contenu en double sous les yeux de l'agent.
* **Un index** tient le compte, la présentation rédigée pour la liste et la
  liste des fichiers.
* **Rien n'est redéposé pour rien.** Le registre garde l'empreinte de chaque
  fichier déposé. Un dépôt qui échoue n'y entre pas, et il est repris au
  passage suivant.
""",
}

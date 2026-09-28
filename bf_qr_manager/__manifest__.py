# -*- coding: utf-8 -*-
{
    "name": "Codes QR gérés",
    "version": "18.0.1.0.2",
    "category": "Productivity",
    "summary": "Des planches de codes QR imprimées d'avance, associées au premier scan, "
               "et dont Odoo garde la destination",
    "description": """
Codes QR gérés
==============

Imprimer 500 étiquettes QR avant de savoir où chacune ira. Chaque code mène à
une adresse que le système contrôle : on l'associe au premier scan, on la
change sans réimprimer, on la réinitialise quand l'objet part.

Le module prolonge les Pastilles : un code QR est une pastille sans puce. Il
hérite donc du registre, des gestes (ouvrir une fiche, ouvrir une adresse,
consigner un passage, menu), du journal et des gardes du socle.

* **Lots d'étiquettes vierges**, numérotées à la suite par préfixe.
* **Association au premier scan** par la gestion des pastilles et les groupes
  choisis par l'organisation. Un visiteur sans compte voit une page neutre.
* **Réinitialisation** : l'étiquette redevient vierge, son journal reste.
* **Association en lot par tableur** (numéros, plages, codes), avec
  vérification ligne par ligne avant d'écrire.
* **Planches aux formats du commerce**, en Lettre et en A4, tracées au
  millimètre, avec un départ décalé pour finir une planche entamée.
* **QR à la marque** : logo au centre en correction H, contraste contrôlé.
* **Porte publique** : une étiquette qui mène à une adresse, à une page de
  liens publiée ou à un événement publié s'ouvre sans compte.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ « Other proprietary » est la valeur que le schéma de manifeste d'Odoo
    # offre pour dire BUSL-1.1. C'est le fichier LICENSE qui gouverne.
    "license": "Other proprietary",
    "depends": ["bf_nfc"],
    "external_dependencies": {"python": ["qrcode", "reportlab", "openpyxl"]},
    "data": [
        "security/bf_qr_security.xml",
        "security/ir.model.access.csv",
        "data/bf_qr_gesture_data.xml",
        "data/bf_qr_label_format_data.xml",
        "views/bf_qr_label_format_views.xml",
        "views/bf_qr_batch_views.xml",
        "views/bf_nfc_tag_views.xml",
        "views/bf_qr_wizard_views.xml",
        "views/bf_nfc_config_views.xml",
        "views/bf_qr_menus.xml",
        "views/portail_templates.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
    "auto_install": False,
}

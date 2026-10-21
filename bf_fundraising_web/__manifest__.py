# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
{
    "name": "Levée de fonds — Web & Portail donateur",
    # 18.0.1.1.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    "version": "18.0.1.1.0",
    "category": "Accounting/Donation",
    "summary": "Formulaire de don public sur le site web + portail donateur "
    "(historique des dons et téléchargement des reçus officiels)",
    "description": """
Levée de fonds — Web & Portail donateur
=======================================

Deux fonctionnalités de service en ligne pour la suite de levée de fonds :

* **Formulaire de don public** (``/don``) : un visiteur saisit son nom, courriel,
  montant et (optionnellement) le fonds / la campagne. Le don est créé dans Odoo
  (fiche donateur appariée ou créée par courriel). À la validation par le
  personnel, le **reçu officiel** est émis et **envoyé par courriel**
  automatiquement.
* **Portail donateur** : le donateur connecté voit son **historique de dons** et
  peut **télécharger ses reçus officiels** (PDF conforme ARC + Revenu Québec).

S'appuie sur ``bf_receipt_ca`` pour les reçus conformes.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "AGPL-3",
    "depends": [
        "bf_receipt_ca",
        "website",
        "portal",
    ],
    "data": [
        "views/donation_web_templates.xml",
        "views/portal_templates.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}

# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
{
    "name": "Levée de fonds — Cœur",
    "version": "18.0.1.0.0",
    "category": "Accounting/Donation",
    "summary": "Gestion des donateurs et structure de collecte (Fonds / Campagnes / "
    "Sollicitations / Trousses) par-dessus le module Dons — comparable à Raiser's Edge",
    "description": """
Levée de fonds — Cœur
=====================

Transforme le module Dons (OCA) en une véritable plateforme de gestion des
donateurs pour organismes de bienfaisance (comparable à Raiser's Edge /
Blackbaud), en français.

Ajoute par-dessus ``donation`` :

* La structure de collecte à quatre niveaux **Fonds → Campagne → Sollicitation →
  Trousse** (Fund / Campaign / Appeal / Package), avec objectifs et montants
  amassés calculés.
* Le **Fonds** pilote la ventilation analytique (GL) des dons.
* La fiche **constituant** enrichie : type (individu, foyer, organisation,
  fondation), sommaire des dons (total, premier don, dernier don, plus grand
  don), capacité et cote de richesse, codes de sollicitation (ne pas solliciter,
  ne pas appeler…), regroupement par foyer.
* Un rapport **donateurs inactifs (LYBUNT/SYBUNT)** via filtres enregistrés.

Le reçu officiel canadien conforme (ARC + Revenu Québec) est fourni par le module
séparé ``bf_receipt_ca``.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "AGPL-3",
    "depends": [
        "donation",
        "analytic",
        "bf_onboarding_base",
    ],
    "data": [
        "security/bf_fundraising_security.xml",
        "security/ir.model.access.csv",
        "data/bf_fundraising_data.xml",
        "data/bf_onboarding.xml",
        "views/bf_fund_views.xml",
        "views/bf_appeal_views.xml",
        "views/bf_solicit_code_views.xml",
        "views/donation_campaign_views.xml",
        "views/donation_views.xml",
        "views/res_partner_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}

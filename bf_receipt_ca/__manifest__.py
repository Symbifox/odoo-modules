# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
{
    "name": "Reçus de dons — Canada (ARC + Revenu Québec)",
    "version": "18.0.1.0.4",
    "category": "Accounting/Donation",
    "summary": "Reçus officiels de dons conformes ARC + Revenu Québec, en français — "
    "montant admissible, avantage, dons en nature, annulation/réémission",
    "description": """
Reçus de dons — Canada (ARC + Revenu Québec)
============================================

Rend les reçus du module Dons **conformes aux exigences canadiennes** pour un
organisme de bienfaisance enregistré, **en français** (Charte de la langue
française / Loi 96). C'est le différenciateur clé face à Raiser's Edge, dont les
reçus sont pensés pour les règles américaines.

Le reçu officiel porte tous les éléments obligatoires de l'ARC :

* mention « Reçu officiel aux fins de l'impôt sur le revenu » ;
* nom légal et adresse de l'organisme + **numéro d'enregistrement (BN/RR)** ;
* **numéro de série unique** (séquence annuelle sans trou) et date de délivrance ;
* année/date du don, nom et adresse du donateur ;
* **montant du don**, **montant de l'avantage** (le cas échéant) et
  **montant admissible** ;
* signature autorisée ;
* nom de l'ARC et adresse `canada.ca/organismesdebienfaisance`.

Pour les **dons en nature** : description du bien, **juste valeur marchande** et
coordonnées de l'évaluateur.

Comme un organisme enregistré auprès de l'ARC est automatiquement reconnu au
Québec depuis 2016, un seul reçu français portant le numéro BN/RR satisfait le
fédéral et le Québec. Les champs « lieu de délivrance » et « évaluateur » sont
configurables pour suivre la modernisation ARC 2024.

Ajoute aussi l'**annulation** et la **réémission** de reçus avec chaîne de
remplacement (les reçus annulés sont conservés, comme l'exige l'ARC).
""",
    "author": "Les services de consultation Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "AGPL-3",
    "depends": [
        "bf_fundraising_core",
        "bf_lexend",
    ],
    # Optional (soft) dependency, resolved at runtime, not declared here:
    #   bluefox_branding — when installed, the receipt PDF uses the branded
    #   document layout and the receipt email uses the branded transactional
    #   mail layout (bluefox_branding.bf_mail_layout). Without it, both fall
    #   back to Odoo's stock layouts. See DonationTaxReceipt.bf_receipt_email_layout.
    "data": [
        "data/receipt_sequence.xml",
        "data/mail_template.xml",
        "report/bf_receipt_ca_report.xml",
        "report/bf_receipt_ca_templates.xml",
        "views/res_company_views.xml",
        "views/donation_tax_receipt_views.xml",
        "views/donation_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}

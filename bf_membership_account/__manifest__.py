{
    "name": "Membres : facturation et reçus fiscaux",
    "summary": "Facture la cotisation dans Odoo, suit son paiement sur l'adhésion, et délivre "
               "le reçu fiscal d'un organisme de bienfaisance (reçu partiel, CSP-M05)",
    "version": "18.0.1.0.2",
    "category": "Association",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "description": """
Membres : facturation et reçus fiscaux
======================================

Le socle `bf_membership` NOTE le paiement d'une cotisation, quelle que soit sa
source. Quand la comptabilité de l'organisme vit dans Odoo, ce greffon fait de
la facture la source du paiement.

Ce qu'il ajoute
---------------

* **L'article de cotisation** sur la catégorie, créé à la première facture
  s'il manque.
* **Le bouton « Facturer »** sur l'adhésion : une facture client validée, au
  membre lui-même, personne ou organisation, jamais à un contact enfant, libellée
  « Cotisation <catégorie> <période> ».
* **Le paiement suit la facture** : payée, l'adhésion passe « en règle » ;
  renversée par un avoir, annulée ou remise en brouillon, elle redevient « à
  payer ». Le paiement en ligne du portail passe par le même chemin.
* **Le reçu fiscal de cotisation** pour un organisme de bienfaisance
  enregistré : montant admissible selon la politique CSP-M05 de l'ARC (reçu
  partiel, seuil de minimis, plafond de 80 %), numéro de série sans trou par
  société, mentions de l'article 3501 du Règlement de l'impôt sur le revenu,
  duplicata marqué comme tel, annulation et remplacement. Facturée, la
  cotisation se reçoit pour ce que la facture a encaissé ; payée ailleurs,
  son reçu se délivre par la personne responsable ou par la comptabilité.

Ce qu'il ne fait pas
--------------------

* **Il ne dépend pas de la levée de fonds.** Les reçus de dons
  (`bf_receipt_ca`) sont un autre module, sous une autre licence ; aucun nom
  de champ n'est partagé.
* **Il n'envoie rien.** La facture et le reçu se produisent ; leur envoi reste
  un geste de l'organisme.
""",
    "depends": [
        "bf_membership",
        "account",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/receipt_security.xml",
        "data/ir_cron.xml",
        "report/receipt_report.xml",
        "views/membership_type_views.xml",
        "views/membership_views.xml",
        "views/receipt_views.xml",
        "views/res_config_settings_views.xml",
        "wizard/receipt_cancel_views.xml",
        "views/menuitems.xml",
    ],
}

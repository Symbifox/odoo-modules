{
    "name": "Membres",
    "summary": "Registre des membres d'une association, d'un OBNL ou d'un "
               "regroupement : catégories, adhésions, renouvellements, délégués",
    "version": "18.0.1.0.4",
    "category": "Association",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": True,
    "installable": True,
    "description": """
Membres
=======

Une association sait qui elle sert. Elle sait rarement, sans ouvrir trois
fichiers, qui est membre en règle aujourd'hui, depuis quand, qui la représente
quand le membre est une organisation, et qui n'a pas renouvelé. Ce module tient
ce registre-là.

Ce qu'il ajoute
---------------

* **La catégorie d'adhésion** (`bf.membership.type`) : régulier, soutien,
  honoraire, famille, organisation ; son prix ; sa période, fixe (un exercice,
  par exemple du 1er avril au 31 mars), glissante (douze mois depuis
  l'adhésion) ou à vie ; le droit de vote ; l'admission d'office ou sur
  décision.
* **L'adhésion** (`bf.membership`), une par membre et par période, avec deux
  états volontairement séparés : l'adhésion (demande, à payer, en règle,
  échue, retirée, refusée) et le paiement (à payer, payé, exempté). Le
  paiement se note quelle que soit sa source : facture, Zeffy, Stripe, chèque,
  virement, plateforme externe.
* **Le numéro de membre**, stable d'une période à l'autre, et le statut
  courant du contact.
* **Les délégués** (`bf.membership.delegate`) d'une organisation membre : la
  personne qui la représente et qui vote en son nom.
* **Le registre** : les membres actuels ET anciens, avec adresse et
  profession, comme l'exige l'article 104 de la Loi sur les compagnies (partie
  III), et la liste annuelle des membres (article 223).
* **Le renouvellement et les rappels**, éteints d'office : rien ne part tant
  que l'organisme ne l'a pas allumé dans les réglages.
* **L'import** d'une liste (CSV de Zeffy, d'une plateforme nationale, d'un
  Excel), avec rapprochement des doublons.

Ce qu'il ne fait pas
--------------------

* **Il ne dépend pas d'une facture.** Le module `membership` d'Odoo calcule
  l'état d'un membre depuis une facture Odoo : sans facture, pas de membre.
  La plupart des associations encaissent ailleurs. La facture vient d'un
  greffon (`bf_membership_account`), quand la comptabilité vit dans Odoo.
* **Il ne publie rien.** Le répertoire des membres et le portail sont un
  greffon, et un membre n'y paraît que s'il y a consenti.
* **Il n'est pas un registre d'ordre professionnel.** Le tableau de l'ordre,
  la formation continue et la discipline sont un greffon planifié.
""",
    "depends": [
        "contacts",
        "mail",
        # La mise en page des courriels maison (bf_mail_layout), que les
        # gabarits du socle et des greffons désignent.
        "bf_onboarding_base",
    ],
    "data": [
        "security/membership_security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence.xml",
        "data/ir_cron.xml",
        "data/mail_template.xml",
        "views/membership_type_views.xml",
        "views/membership_views.xml",
        "views/res_partner_views.xml",
        "views/res_config_settings_views.xml",
        "wizard/withdraw_views.xml",
        "wizard/import_views.xml",
        "report/member_list_report.xml",
        "views/menuitems.xml",
    ],
}

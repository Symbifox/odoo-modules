{
    "name": "Membres : portail, adhésion en ligne et répertoire",
    "summary": "Le membre voit son adhésion, renouvelle et paie en ligne, imprime sa carte ; "
               "formulaire public d'adhésion ; répertoire des membres sur consentement",
    "version": "18.0.1.2.1",
    "category": "Association",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "description": """
Membres : portail, adhésion en ligne et répertoire
==================================================

Le registre des membres (`bf_membership`) vit au bureau. Ce greffon en ouvre
la part qui revient au membre, et seulement celle-là.

Ce qu'il ajoute
---------------

* **Mon adhésion** (`/my/membership`) : le statut, le numéro, la période,
  l'historique et les factures ; les consentements (répertoire, avis par
  courriel), que le membre change lui-même ; le bouton **Renouveler**, qui
  prépare le renouvellement et sa facture, puis mène au paiement en ligne de
  la facture ; la **carte de membre** en PDF. Une personne déléguée d'une
  organisation membre voit, en lecture, l'adhésion de l'organisation qu'elle
  représente.
* **Le formulaire public d'adhésion** (`/membres/adhesion`), pour les
  catégories que l'organisme y ouvre. Il crée une demande et un contact neuf,
  ne touche jamais un contact existant, et ne dit jamais si une adresse
  courriel est déjà connue. La facture naît au clic « Payer en ligne », pas à
  l'envoi ; impayée après quatorze jours, elle est annulée et la demande
  refusée. Une demande dont le courriel était connu se rattache au contact
  existant par un bouton, jamais par une fusion de contacts.
* **Le répertoire des membres** (`/membres/repertoire`), fermé d'office ; une
  fois ouvert, il ne montre que les membres en règle qui y ont consenti, et
  seulement leur nom et leur ville.

Ce qu'il ne fait pas
--------------------

* **Il n'envoie que deux courriels**, tous deux à la réception d'une demande
  publique : un accusé de réception sobre à l'adresse saisie (le même que
  l'adresse soit connue ou non, sans aucun texte saisi), et une notification
  aux responsables des membres. Ni facture, ni rappel, ni carte ne partent
  par courriel. Le paiement en ligne est celui d'Odoo, sur la page de la
  facture, avec les fournisseurs de paiement de l'organisme.
* **Il ne publie rien sans consentement.** Le répertoire est fermé tant que
  l'organisme ne l'a pas ouvert, et chaque membre y paraît s'il l'a choisi.
""",
    "depends": [
        "bf_membership_account",
        "portal",
        "account_payment",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/portal_security.xml",
        "data/ir_cron.xml",
        "data/mail_template.xml",
        "report/membership_card.xml",
        "views/membership_type_views.xml",
        "views/membership_views.xml",
        "views/res_config_settings_views.xml",
        "views/portal_templates.xml",
        "views/public_templates.xml",
        "wizard/attach_views.xml",
    ],
}

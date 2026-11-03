{
    "name": "Relances de factures",
    "version": "18.0.1.3.1",
    "category": "Accounting/Accounting",
    "summary": "Suivi et relances des factures clients en retard "
               "(statut manuel, activités automatiques, digest hebdomadaire)",
    "description": """
Relances de factures
====================

Ajoute, sur la facture client (``account.move``), un suivi de relance par
facture, là où les relances d'Odoo travaillent au niveau du client :

* **Statut de relance manuel** par facture (Aucune / 1er rappel / 2e rappel /
  Mise en demeure), modifiable d'un clic depuis l'en-tête de la facture.
* **Cron aux deux semaines** qui crée une activité de relance 7 jours après l'échéance
  d'une facture impayée, assignée au vendeur (ou au créateur). Idempotent.
  Il ne modifie jamais le statut — c'est vous qui le faites évoluer.
* **Activité à chaque palier** lors d'un changement manuel de statut.
* **Bouton « Envoyer relance »** qui ouvre le compositeur de courriel
  pré-chargé avec le gabarit de relance, **éditable** et **jamais auto-envoyé**.
* **Auto-clôture** des activités de relance lorsque la facture est payée.
* **Digest hebdomadaire** interne (courriel + ntfy) des factures en retard.
* **Vue « Factures en retard »** : filtres, paliers d'âge, regroupement par
  vendeur, décorations.

Module additif et réversible : il ne touche à aucune donnée d'Odoo et ne
dépend que du cœur (``account``, ``mail``, ``base``). Une seule migration
existe, de la 18.0.1.1.0, et elle ne fait que reporter l'ancien abonnement
unique au récapitulatif vers le réglage par usager.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": [
        "base",
        "mail",
        "account",
    ],
    "data": [
        # Data
        "data/followup_activity_type.xml",
        "data/followup_cron.xml",
        "data/followup_mail_template.xml",
        # Views
        "views/account_move_views.xml",
        "views/res_users_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
    "auto_install": False,
}

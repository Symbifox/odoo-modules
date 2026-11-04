{
    "name": "Budgets opérationnels — bons de commande",
    "version": "18.0.1.0.0",
    "category": "Accounting/Accounting",
    "summary": "Les bons de commande confirmés non facturés entrent dans l'engagé",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "depends": ["bf_budget", "purchase"],
    "description": """
Budgets opérationnels — bons de commande
========================================

Un bon de commande confirmé est une dépense décidée : le fournisseur a la
commande, l'argent sortira. Tant que la facture n'est pas comptabilisée, le
socle ne le voit pas. Ce satellite ajoute à l'engagé du poste la part de chaque
bon de commande qu'aucune facture comptabilisée ne porte encore.

**Le garde est l'écriture comptabilisée, jamais le « facturé » d'Odoo**
  Odoo compte dans « Qté facturée » les factures au brouillon autant que les
  comptabilisées. S'y fier ferait disparaître l'engagement dès la saisie du
  brouillon, avant que le réalisé ne le reprenne : le montant sortirait du
  budget le temps de la comptabilisation. Le module soustrait donc les seules
  écritures COMPTABILISÉES : le total engagé ne bouge pas, il change de case.

**Le théorique suit la date prévue**
  Un bon de commande porte une date de réception prévue : c'est un engagement
  daté, comme un renouvellement d'abonnement. Quand un poste en a, le théorique
  devient « ce qui était dû à ce jour » au lieu d'une fraction du temps écoulé,
  et se combine avec les autres calendriers du poste sans compter deux fois.

⚠️ **Un renouvellement commandé par bon de commande compte deux fois**
  Rien ne relie un bon de commande à l'abonnement qu'il renouvelle. Si un même
  renouvellement est à la fois au registre des abonnements et sur un bon de
  commande confirmé du même poste, l'engagé porte les deux jusqu'à la facture.
  Un calendrier par dépense : le registre OU les achats, jamais les deux.

**Le poste est celui où la facture tombera**
  La ligne de commande est rattachée au poste par le compte de charge de son
  produit, position fiscale comprise : le même compte que la facture utilisera.
  Un avoir comptabilisé rend sa part à l'engagé ; une facture au-delà du bon de
  commande ne crée pas d'engagement négatif.
""",
    "data": [
        "views/bf_budget_line_views.xml",
    ],
}

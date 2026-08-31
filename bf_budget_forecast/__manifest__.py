{
    "name": "Budgets opérationnels — prévision glissante",
    "version": "18.0.1.0.0",
    "category": "Accounting/Accounting",
    "summary": "Prévision refaite chaque mois sur 12 à 18 mois, et comparable à ce qu'on croyait avant",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "auto_install": False,
    "depends": ["bf_budget"],
    "description": """
Budgets opérationnels — prévision glissante
===========================================

Un budget est arrêté une fois et sert de référence. Une prévision se REFAIT,
tous les mois, sur un horizon qui avance avec le calendrier.

**Ce qu'on stocke, et ce qu'on ne stocke pas**
  La règle du socle dit d'enregistrer les DÉCISIONS et de calculer les FAITS.
  Une prévision est une décision : elle se stocke. Le réel reste un fait : il
  continue de se relire dans les livres à chaque affichage, jamais d'être
  recopié. C'est ce qui permet de répondre à la seule question qui compte dans
  une prévision glissante : « qu'est-ce qu'on croyait en mars, et qu'est-ce qui
  s'est passé ? »

**Le millésime**
  Chaque passe mensuelle est un enregistrement à part, numéroté, qui garde ses
  chiffres pour toujours. Publier un millésime le gèle. On ne corrige pas une
  prévision passée : on en fait une nouvelle.

**La ligne d'arrêt du réel**
  Les mois jusqu'à la date de clôture sont du réel, relu dans la comptabilité.
  Les mois au-delà sont de la prévision. Un mois clos ne se re-prévoit jamais,
  et la prévision qu'on en avait faite reste lisible à côté de ce qui est
  arrivé.

⚠️ **Le risque n'est pas technique**
  Une prévision que personne ne refait est pire que pas de prévision : elle
  continue d'avoir l'air autorisée pendant qu'elle pourrit. Tout ici est réglé
  pour que la passe mensuelle tienne en quelques minutes — un bouton qui roule
  d'un mois, des mois ouverts pré-remplis, et des postes en petit nombre.

**Sans inducteurs, volontairement**
  Pas de formules, pas de quantités × taux. Une prévision par poste et par mois,
  amorcée automatiquement, corrigée à la main là où on sait mieux. Les
  inducteurs pourront se greffer plus tard sans rien casser.
""",
    "data": [
        "security/ir.model.access.csv",
        "security/bf_budget_forecast_security.xml",
        "views/bf_budget_forecast_views.xml",
        "views/bf_budget_forecast_menus.xml",
    ],
}

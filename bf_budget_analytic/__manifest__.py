{
    "name": "Budgets opérationnels — second axe analytique",
    "version": "18.0.1.0.0",
    "category": "Accounting/Accounting",
    "summary": "Déplier une ligne budgétaire dans les lignes analytiques qu'elle lit, chacun sous ses droits",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "depends": ["bf_budget", "hr_timesheet"],
    "description": """
Budgets opérationnels — second axe analytique
=============================================

Le socle lit déjà le second axe : une ligne comptable peut se restreindre à des
comptes analytiques, et une ligne de coût interne lit les feuilles de temps et
les écritures analytiques saisies à la main. Ce satellite ouvre le DÉTAIL de
cette lecture, sans jamais contourner les droits.

**Déplier, c'est lire des salaires — alors chacun sous ses droits**
  Une ligne de main-d'œuvre se déplie en feuilles de temps, valorisées au taux
  horaire de l'employé. L'action de dépliage ne transmet qu'un domaine, jamais
  des enregistrements relevés avec des droits élevés : les ACL et les règles
  d'enregistrement s'appliquent au rendu, sous le compte de la personne. Deux
  personnes qui déplient la même ligne ne voient pas les mêmes
  enregistrements, et c'est voulu. Les montants de la ligne restent des sommes,
  stables quel que soit le rôle.

**Ce que le dépliage montre dépend du rôle — à dire par rôle**
  Budget + feuilles de temps : la règle d'Odoo restreint à SES propres heures.
  Budget + comptabilité analytique sans les feuilles de temps : aucune règle
  d'enregistrement n'est attachée à ce groupe, donc ce rôle déplie TOUTES les
  feuilles de temps valorisées de la société. Le module n'élargit aucun droit —
  ce rôle lit déjà la même chose par le menu des écritures analytiques d'Odoo —
  mais accorder la comptabilité analytique, c'est accorder la lecture des
  salaires : la décision se prend sur le groupe, pas sur ce bouton. Un lecteur
  de budget sans aucun de ces groupes garde les sommes; le dépliage refuse.

**Le dépliage montre exactement ce que la mesure compte**
  Le domaine de l'action est celui de la mesure (`_actual_source()` du socle) :
  un seul énoncé, aucun écart possible entre le chiffre et son détail.

**Les heures à côté des dollars**
  Une ligne de coût interne affiche les heures de feuilles de temps de la
  fenêtre. Un taux horaire manquant laisse les dollars à zéro — les heures,
  elles, restent visibles, et le socle signale déjà les heures non valorisées.
""",
    "data": [
        "views/bf_budget_line_views.xml",
    ],
}

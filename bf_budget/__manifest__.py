{
    "name": "Budgets opérationnels",
    "version": "18.0.1.0.1",
    "category": "Accounting/Accounting",
    "summary": "Budget d'exploitation par poste du grand livre, comparé au réel et à l'engagé",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "application": True,
    "installable": True,
    "auto_install": False,
    "depends": ["account", "mail"],
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "description": """
Budgets opérationnels
=====================

Budget d'exploitation posé sur le **plan comptable**, comparé au réel sans aucune
saisie préalable.

**Le poste budgétaire est un regroupement de comptes**
  Pas un compte analytique. L'analytique reste disponible en second axe, ligne par
  ligne, quand elle est renseignée. Un budget fonctionne donc dès le premier jour,
  même si aucune facture ne porte de distribution analytique.

**Quatre montants, le vocabulaire d'Odoo**
  - *Prévu* : saisi, réparti par mois
  - *Réalisé* : écritures comptabilisées sur les comptes du poste
  - *Engagé* : le réalisé plus les engagements connus mais non encore comptabilisés
  - *Théorique* : ce qui aurait dû être dépensé à ce jour

**Le théorique suit le calendrier, pas le chronomètre**
  Une dépense annuelle ne se lisse pas sur douze mois. La répartition mensuelle est
  saisissable, et le théorique s'appuie dessus. Quand un module satellite fournit un
  calendrier d'engagements datés, le théorique s'y appuie et le dit.

**Un budget ouvert ne se modifie plus**
  Il se révise : la révision est un nouvel enregistrement numéroté, l'original reste
  consultable. Un budget qu'on retouche en douce ne mesure plus rien.

**Contrôle de couverture**
  Le module signale les comptes de charge qu'aucun poste ne couvre et ceux que deux
  postes couvrent en double, sans quoi un budget peut paraître respecté parce qu'un
  poste manque.
""",
    "data": [
        "security/bf_budget_security.xml",
        "security/ir.model.access.csv",
        "views/bf_budget_position_views.xml",
        "views/bf_budget_views.xml",
        "views/bf_budget_line_views.xml",
        "wizard/bf_budget_position_wizard_views.xml",
        "views/res_config_settings_views.xml",
        "report/bf_budget_report.xml",
        "views/bf_budget_menus.xml",
    ],
}

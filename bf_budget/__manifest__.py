{
    "name": "Budgets opérationnels",
    # 18.0.1.5.0: une ligne comptable filtrée sur l'analytique lit la PART
    #   distribuée (les lignes analytiques adossées aux écritures comptabilisées
    #   du poste), plus jamais le plein solde des écritures : une facture
    #   ventilée A:50/B:50 comptait 100 % sur la ligne qui surveille A ET 100 %
    #   sur celle qui surveille B. La fenêtre reste la date de l'écriture, le
    #   signe celui de l'analytique. Au passage, plus aucune recherche
    #   analytique hors sudo dans le calcul : un lecteur de budget sans droits
    #   comptables lisait une AccessError sur une ligne filtrée, et le chiffre
    #   dépendait du rôle. La mesure s'énonce en un point (`_actual_source`),
    #   que le satellite analytique réutilise pour déplier exactement ce que la
    #   ligne lit.
    # 18.0.1.4.0: le théorique de calendrier devient compositionnel. Les
    #   satellites n'implantent plus chacun leur théorique : ils déclarent
    #   leur couple (dû à ce jour, total de la période) par
    #   `_calendar_commitment_components`, et le socle combine, parce que le
    #   reliquat au prorata se mesure contre le plan non couvert par
    #   l'ENSEMBLE des calendriers. Deux satellites qui chacun prorateraient
    #   « le plan moins mon total » compteraient deux fois la part de l'autre.
    #   Et le reliquat non couvert suit désormais la VENTILATION saisie
    #   (l'ancien prorata linéaire de l'exercice masquait la dérive d'un
    #   poste saisonnier dès qu'un engagement daté existait). Les dépendances
    #   de `_compute_amounts` déclarent aussi `budget_type`, `state` et les
    #   seuils, que `_sign` et `_evaluate_alert` lisent.
    # 18.0.1.3.0: le gel du plan côté serveur, fermé de bout en bout. Une
    #   ligne ne se crée, ne se duplique, ne se supprime ni ne se déplace plus
    #   vers un budget sorti du brouillon ; une période ne se crée, ne se
    #   supprime, ne se redate ni ne se reparente plus ; glisser `state` dans
    #   le même write n'exempte la garde que vers le brouillon, et seulement
    #   depuis les états qu'action_reset_draft accepte. Les gardes de create
    #   s'évaluent sur les enregistrements créés (les défauts du contexte ne
    #   les sautent pas). Redater un brouillon transporte sa ventilation sur
    #   le nouvel exercice (note au fil si elle est relissée ; redater et
    #   ouvrir dans le même write est refusé). Un poste d'une autre société
    #   que le budget est refusé, quel que soit le sens du geste. Limites
    #   assumées au README : les comptes d'un poste restent modifiables, et
    #   `state` nu n'est pas gardé (tracé au fil).
    # 18.0.1.2.1: une révision garde la ventilation mensuelle de ses lignes.
    #   `amount_planned` est un calculé stocké modifiable, donc copié par
    #   défaut : la copie écrasait les périodes recopiées en les relissant.
    # 18.0.1.2.0: les alertes. Un responsable par budget, prévenu une fois au
    #   franchissement du seuil (note et activité), réarmé quand la ligne cesse
    #   d'être signalée. Seuils par défaut réglables par instance.
    # 18.0.1.1.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    "version": "18.0.1.5.0",
    "category": "Accounting/Accounting",
    "summary": "Budget d'exploitation par poste du grand livre, comparé au réel et à l'engagé",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
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

**Prévenir au franchissement, une fois**
  Une ligne qui dépasse le seuil (pourcentage ET plancher en dollars) est notée au
  fil du budget, et son responsable reçoit une activité. Elle ne le refait pas tant
  qu'elle reste signalée. Un dépassement assumé, avec son motif, la fait taire et
  ferme l'activité.

**Contrôle de couverture**
  Le module signale les comptes de charge qu'aucun poste ne couvre et ceux que deux
  postes couvrent en double, sans quoi un budget peut paraître respecté parce qu'un
  poste manque.
""",
    "data": [
        "security/bf_budget_security.xml",
        "security/ir.model.access.csv",
        "data/bf_budget_alert_data.xml",
        "views/bf_budget_position_views.xml",
        "views/bf_budget_views.xml",
        "views/bf_budget_line_views.xml",
        "wizard/bf_budget_position_wizard_views.xml",
        "views/res_config_settings_views.xml",
        "report/bf_budget_report.xml",
        "views/bf_budget_menus.xml",
    ],
}

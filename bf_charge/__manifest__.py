{
    "name": "Plan de charge",
    "version": "18.0.1.1.2",
    "category": "Services/Project",
    "summary": "Ce qui est vraiment à faire, posé sur des semaines, contre une capacité déclarée plutôt que devinée",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": False,
    "depends": ["project", "hr_timesheet"],
    "description": """
Plan de charge
==============

Un carnet de tâches n'est pas un plan de charge. Entre les deux il manque trois
choses, et ce module ne fait que celles-là.

**Une capacité déclarée, jamais déduite**
  Les heures saisies dans une feuille de temps ne sont pas du temps d'horloge :
  un travail assisté produit plusieurs heures dans la même heure. En diviser le
  carnet donnerait une réponse fausse avec deux décimales. La capacité se
  déclare donc dans un contrat, à la main. Le module mesure et affiche les
  candidats (médiane sur douze mois, moyenne, calendrier de travail, médiane
  récente plafonnée au temps d'horloge) et laisse la personne trancher.

**Une charge qui dit d'où elle vient**
  Le champ des heures allouées d'Odoo a plusieurs sources : une estimation
  humaine, ou une quantité vendue convertie par l'unité de mesure. Une ligne
  d'abonnement annuel écrit des milliers d'heures sans que personne les ait
  estimées. Chaque tâche porte donc ici sa source, et une heure venue d'une
  unité de vente qui n'est pas l'heure est écartée du plan, visiblement, avec
  son motif.

**Un non plaçable qui ne se cache pas**
  Une tâche sans date ne se pose sur aucune semaine. Un calendrier qui n'affiche
  que ce qu'il sait placer ment par omission. Le plan affiche donc toujours,
  à côté des semaines, le total des heures qu'il n'a pas su placer et le nombre
  de tâches concernées. C'est souvent ce chiffre-là, et non le calendrier, qui
  répond à la question posée.

**Les projets se classent tout seuls**
  Vivant, dormant, ou gabarit. La dernière ligne de temps du projet suffit à
  séparer ce qui bouge de ce qui dort, sans lire un seul message. Les gabarits,
  eux, portent une étiquette : ce sont des patrons à cloner, pas du travail qui
  attend, et leurs heures n'entrent jamais dans un plan.

**Cinq signaux de saturation**
  Clients simultanés contre le plafond déclaré, part interne contre la part
  visée, banques d'heures sous zéro, arrivées contre clôtures, et le carnet non
  plaçable exprimé en semaines de capacité. Chacun affiche sa mesure, pas
  seulement sa couleur.

⚠️ **Ce que le module ne fait pas**
  Il ne réclame pas de dater les tâches, il chiffre ce qui n'est pas daté. Il ne
  calcule pas une capacité, il en réclame une. Il n'estime rien à la place de
  personne : une tâche sans estimé reste sans estimé, et c'est compté.
""",
    "data": [
        "security/bf_charge_groups.xml",
        "security/ir.model.access.csv",
        "security/bf_charge_security.xml",
        "data/bf_charge_cron.xml",
        "views/bf_charge_contract_views.xml",
        "views/bf_charge_plan_views.xml",
        "views/bf_charge_plan_line_views.xml",
        "views/bf_charge_snapshot_views.xml",
        "views/project_views.xml",
        "views/bf_charge_menus.xml",
    ],
}

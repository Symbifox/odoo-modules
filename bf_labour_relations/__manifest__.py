{
    "name": "Relations de travail",
    "summary": "Accréditation, convention collective, ancienneté, grief et "
               "cotisations syndicales, du côté employeur comme du côté syndical",
    "version": "18.0.1.0.0",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": True,
    "installable": True,
    "description": """
Relations de travail
====================

Odoo sait qui travaille ici. Il ne sait pas quelle convention couvre cette
personne, depuis quand elle a de l'ancienneté dans son unité, ni quel délai
court sur le grief déposé la semaine dernière. Ce module ajoute cette
moitié-là.

Rien dans Odoo Community ne touche aux relations de travail : `hr` connaît la
personne et le poste, jamais l'unité qui la couvre.

Ce qu'il ajoute
---------------

* **Le syndicat** (`bf.labour.union`), adossé à un partenaire : centrale,
  section locale, personnes-ressources.
* **L'unité de négociation** (`bf.labour.unit`), portée par une société, avec
  son accréditation et le groupe de salariés qu'elle vise. Un groupe dont
  chaque établissement est une société distincte a autant d'unités que
  d'établissements.
* **La convention** (`bf.labour.agreement`) et ses **articles**
  (`bf.labour.agreement.article`), chaque article portant un *sujet normalisé*
  (salaire, horaire, congés, ancienneté, discipline, grief, affichage,
  cotisations). C'est ce sujet qui rendra l'automatisation possible le jour où
  une paie existera.
* **L'appartenance** (`bf.labour.membership`), qui porte **la date
  d'ancienneté** de l'unité, et deux états volontairement séparés : *couverte
  par l'unité* et *membre du syndicat*. Ce ne sont pas les mêmes choses, et
  l'article 47 du Code du travail est la raison.
* **Le grief** (`bf.labour.grievance`) et ses **étapes**
  (`bf.labour.grievance.step`), chaque étape portant son délai conventionnel et
  son échéance calculée. Un grief se perd sur le calendrier, pas sur le fond.
* **Les cotisations** : une règle par convention (`bf.labour.dues.rule`) qui
  porte le montant fixe et le pourcentage ensemble, et une remise déclarée par
  période (`bf.labour.dues.remittance`).

Ce qu'il ne fait pas
--------------------

* **Il ne calcule pas la paie.** Aucune paie n'est installée, et le module n'en
  suppose aucune. La clause de salaire se lit, la cotisation se déclare et se
  remet. Le jour où une paie arrive, les sujets normalisés sont déjà là pour
  l'alimenter.
* **Il ne gère pas la négociation** : rondes, mandats, offres patronales. Autre
  chantier.
* **Il n'a rien à voir avec le syndicat de copropriété**, que la suite
  `bf_property` sert déjà, ni avec le registre corporatif, que
  `bf_corporate_governance` couvre.
* **Il ne prend pas parti.** Le socle décrit la relation ; son vocabulaire reste
  neutre, plaignant et intimé, parce qu'un grief patronal existe aussi. Les deux
  greffons, employeur et syndical, ajoutent des champs et des vues au même
  enregistrement, jamais un second modèle de grief.
""",
    "depends": [
        "hr",
        "mail",
    ],
    "data": [
        "security/labour_relations_security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence.xml",
        "views/union_views.xml",
        "views/unit_views.xml",
        "views/agreement_views.xml",
        "views/membership_views.xml",
        "views/grievance_views.xml",
        "views/dues_views.xml",
        "views/hr_employee_views.xml",
        "views/menuitems.xml",
    ],
}

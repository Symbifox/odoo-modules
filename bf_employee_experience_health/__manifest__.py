{
    "name": "Expérience employé — allergies",
    "summary": "Allergies et allergies alimentaires, lisibles seulement par la "
               "personne et par qui organise",
    "version": "18.0.1.1.0",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "description": """
Allergies et allergies alimentaires
===================================

Satellite de l'expérience employé, parce qu'il pose
exactement la même question, et qu'elle y est déjà tranchée : qui a le droit
de lire un renseignement de santé sur un collègue.

Pourquoi ce n'est pas un simple champ texte
-------------------------------------------

Une allergie est un renseignement de santé, donc un renseignement personnel
**sensible** au sens de la Loi 25. Un champ visible de toute l'entreprise et un
champ réservé à qui organise les repas ne sont pas la même fonctionnalité.

Ce module reprend la réponse du registre d'usage : la personne concernée et
l'administration (`hr.group_hr_user`) lisent la fiche, personne d'autre. Le
gestionnaire direct n'y a pas accès.

Ce qu'il ajoute
---------------

* Un catalogue d'allergènes (`bf.ex.allergen`), avec les allergènes
  prioritaires reconnus au Canada chargés d'office.
* Une déclaration par personne (`bf.ex.allergy`) : l'allergène, la gravité, et
  une note. La gravité « anaphylaxie » se voit dans la liste.
* Une **liste de service** (`action_catering_list`) : pour un groupe de
  personnes, les contraintes alimentaires **sans les noms**. C'est ce qu'on
  transmet à un traiteur, et ça évite de faire circuler un dossier médical
  pour commander des sandwichs.

Ce qu'il ne fait pas
--------------------

Il ne remplace pas un plan d'urgence et ne prétend pas être un dossier
médical. Il ne porte pas de règle de conservation : c'est le pont vie privée.
""",
    "depends": [
        "bf_employee_experience",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/health_security.xml",
        "data/allergen_data.xml",
        "views/health_views.xml",
    ],
}

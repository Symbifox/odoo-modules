{
    "name": "Expérience employé — allergies, pont vie privée (Loi 25)",
    "summary": "Consentement exprès, conservation liée au lien d'emploi, et "
               "destruction au départ",
    "version": "18.0.1.0.0",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Allergies — pont vie privée (Loi 25)
====================================

Une allergie est un renseignement concernant la santé, donc un renseignement
personnel **sensible** au sens de l'art. 59 LPRPSP. Ce n'est pas le même régime
que le registre d'usage des avantages, et c'est pour ça que ce pont existe à
part.

Ce qu'il change par rapport au pont des avantages
-------------------------------------------------

* **Consentement exprès requis.** Déclarer une allergie est volontaire :
  personne n'est obligé de dire à son employeur ce qui l'envoie à l'hôpital.
  L'art. 12 al. 2 exige un consentement manifeste, libre, éclairé et donné à
  des fins spécifiques ; l'art. 59 y ajoute le caractère exprès pour un
  renseignement sensible. Le pont pose donc `requires_express_opt_in`.

* **La durée est le lien d'emploi, pas une horloge.** La fin poursuivie est
  accomplie le jour où la personne part : plus de repas d'équipe, plus de
  trousse à prévoir. L'art. 23 commande alors la destruction. Une règle en
  années serait une fausse précision.

🔴 Le cron de purge est livré DÉSACTIVÉ
---------------------------------------

Il détruit les déclarations des personnes dont le départ est passé, après un
délai de grâce. Une purge irréversible ne doit jamais être un effet de bord
d'une installation : quelqu'un doit décider de l'allumer, et choisir le délai.

⚠️ Aucun agrégat ici, contrairement aux usages
----------------------------------------------

Le module `_health` sait produire une liste de service anonyme à la demande. La
garder après destruction serait garder une statistique de santé sur un effectif
qui n'existe plus, sans qu'aucune décision n'en dépende. On ne conserve pas une
donnée de santé « au cas où ».
""",
    "depends": [
        "bf_employee_experience_health",
        "privacy_consent",
    ],
    "data": [
        "data/privacy_purpose_data.xml",
        "data/privacy_retention_calendar_data.xml",
    ],
}

{
    "name": "Expérience employé : types de profil, pont vie privée (Loi 25)",
    "summary": "Consentement exprès, profilage nommé, conservation liée au lien "
               "d'emploi et destruction au départ",
    "version": "18.0.1.0.0",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Types de profil : pont vie privée (Loi 25)
==========================================

Un type de profil décrit la personne et non le poste. Ce pont existe à part
parce que le régime n'est pas celui du registre d'usage des avantages.

Ce qu'il déclare
----------------

* **Une finalité à consentement exprès.** Déclarer un type de profil est
  volontaire, et personne n'est tenu de remettre à son employeur une mesure de
  sa personnalité. L'art. 12 al. 2 LPRPSP exige un consentement manifeste,
  libre et éclairé, donné à des fins spécifiques.

* **Le profilage, nommé.** La définition de l'art. 8.1 LPRPSP couvre la
  collecte et l'utilisation de renseignements personnels « afin d'évaluer
  certaines caractéristiques d'une personne physique, notamment à des fins
  d'analyse du rendement au travail ». Un type de profil y entre de plein
  droit, et le gestionnaire direct le lit. Le résumé en langage clair le dit
  avant qu'on demande quoi que ce soit, et dit aussi à quoi le profil ne sert
  pas.

* **Une durée qui est le lien d'emploi, pas une horloge.** La fin poursuivie
  est accomplie le jour du départ : plus d'équipe à faire travailler ensemble.
  L'art. 23 commande alors la destruction. Écrire « 3 ans » donnerait une
  fausse précision.

🔴 Le cron de purge est livré DESACTIVE
---------------------------------------

Il détruit les déclarations des personnes parties depuis plus que le délai de
grâce. Une purge irréversible ne doit jamais être un effet de bord d'une
installation : quelqu'un doit décider de l'allumer, et choisir le délai.

⚠️ Aucun agrégat, et aucune statistique gardée
----------------------------------------------

Le pont des avantages garde un agrégat anonyme avant de détruire, parce que
l'entreprise a besoin de la mesure et non des noms. Ici, non : la répartition
des types de personnalité d'un effectif parti ne sert aucune décision, et la
garder ferait de la donnée détruite une statistique conservée pour rien.

⚠️ Le registre de destruction ne nomme jamais le type
-----------------------------------------------------

L'inscrire reviendrait à conserver pour toujours, dans un registre immuable, le
renseignement qu'on vient de détruire. Le registre dit qu'il y avait une
déclaration et combien ; il ne dit pas laquelle.
""",
    "depends": [
        "bf_employee_experience_profile",
        "privacy_consent",
    ],
    "data": [
        "data/privacy_purpose_data.xml",
        "data/privacy_retention_calendar_data.xml",
    ],
}

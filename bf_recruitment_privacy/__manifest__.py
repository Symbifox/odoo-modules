{
    "name": "Recrutement : pont vie privée (Loi 25)",
    "summary": "Déclarer ce que le dossier de candidature collecte, poser une "
               "durée de conservation, détruire pour de vrai, et garder la "
               "mesure des grilles quand les notations s'en vont",
    "version": "18.0.1.2.0",
    "category": "Human Resources/Recruitment",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    # Pont, sur le patron des sept `*_privacy` en service : il s'installe seul
    # quand les deux côtés sont là, et le cahier d'entrevues fonctionne
    # parfaitement sans lui.
    "auto_install": True,
    "description": """
Recrutement : pont vie privée (Loi 25)
======================================

Le cahier d'entrevues sait qui a évalué qui, sur quelle grille, et ce qui a été
écrit. Il ne sait pas combien de temps il a le droit de le savoir. C'est ce pont
qui le lui dit, et qui fait en sorte que la destruction annoncée ait vraiment
lieu.

Ce qu'il déclare
----------------

* Une finalité, « Évaluation d'une candidature ». Sans consentement à demander :
  évaluer quelqu'un qui postule est l'exécution de la démarche qu'il a lui-même
  entreprise. Un consentement qui, refusé, arrêterait l'examen de la candidature
  n'en est pas un.
* Une règle de conservation, RH-REC-1, alignée sur RH-001 « Dossiers
  d'employés » : 3 ans actifs, 2 semi-actifs. Un seul régime de durée pour tout
  le dossier de recrutement, plutôt que deux calendriers à tenir d'accord.
* Trois modèles deviennent classifiables : la personne (`hr.candidate`), la
  candidature (`hr.applicant`) et la séance d'entrevue (`bf.interview`). Ni la
  notation ni l'agrégat n'en font partie, et c'est voulu.
* La bascule : quand la candidature devient une embauche, la classification
  change de rattachement et passe sous RH-001. Un seul régime à la fois.

L'agrégat, écrit AVANT la destruction
-------------------------------------

Les notations sont ce qui permet de dire si une grille sépare les candidats ou
si un critère ne discrimine rien. Détruites, elles ne se reconstituent pas, et
l'entreprise perd la mesure de ses propres outils en même temps que la donnée
personnelle. Or elle n'a besoin que de la mesure.

`bf.interview.aggregate` garde, par grille, par poste et par année : le nombre
de séances, le nombre de personnes évaluées, le score moyen et son écart type,
et le détail par critère (moyenne, écart type, écart entre évaluateurs).
**Aucun nom, ni de candidat, ni d'évaluateur.** L'agrégat survit à la
destruction des séances.

🔴 Et le pont l'impose : une campagne qui tente de détruire une candidature dont
l'année d'entrevue n'a pas encore été agrégée **lève**. Elle ne détruit pas, et
elle n'inscrit rien au registre. C'est le seul ordre qui marche : agréger
d'abord, détruire ensuite.

Trois pièges corrigés
---------------------

🔴 **La campagne générique ARCHIVERAIT au lieu de détruire.** Dans
`privacy_consent`, `privacy.destruction.campaign.line._execute_destruction()`
traite la méthode « Suppression » ainsi : si le modèle porte un champ `active`,
il archive. `hr.applicant` porte `active`, `hr.candidate` aussi. Une campagne
aurait donc archivé la candidature, CV intact, puis écrit au registre immuable
une entrée certifiant sa suppression. Le registre refuse `write` et `unlink` :
la certification fausse aurait été définitive. Ce pont fait l'opération réelle,
ou il lève. Une ligne qui lève n'est pas certifiée.

🔴 **La personne n'est pas dans la candidature.** Odoo 18 a séparé
`hr.candidate` (la personne) de `hr.applicant` (une candidature). Le nom, le
courriel, le téléphone et le profil LinkedIn sont des champs **related** portés
par `hr.candidate`. Détruire la candidature seule ne détruit donc à peu près
aucun renseignement personnel. Le pont emporte la personne avec sa dernière
candidature.

🔴 **La cascade SQL saute l'ORM, et laisse les fichiers derrière.**
`bf_interview.applicant_id` est `ON DELETE CASCADE` au niveau de la base :
supprimer la candidature efface les séances et les notations **sans** que
`unlink()` soit appelé. Or c'est `unlink()` qui balaie les pièces jointes et les
messages de l'enregistrement. Les CV et les fils de discussion resteraient donc
en base et au dépôt de fichiers, orphelins et introuvables, pendant que le
registre attesterait leur destruction. Le pont supprime les séances par l'ORM
d'abord, puis la candidature, puis la personne.
""",
    "depends": [
        "bf_recruitment",
        "privacy_consent",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/privacy_purpose_data.xml",
        "data/privacy_retention_calendar_data.xml",
        "views/interview_aggregate_views.xml",
    ],
}

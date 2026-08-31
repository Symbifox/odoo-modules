{
    "name": "Expérience employé — pont vie privée (Loi 25)",
    "summary": "Déclarer ce que le registre d'usage collecte, poser une règle de "
               "conservation, et garder la mesure quand les lignes s'en vont",
    "version": "18.0.1.1.0",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Expérience employé — pont vie privée (Loi 25)
=============================================

Le socle sait qui a droit à quoi et qui s'en sert. Il ne sait pas combien de
temps il a le droit de le savoir. C'est ce pont qui le lui dit.

Ce qu'il déclare
----------------

* Une finalité, « Gestion des avantages sociaux ». Sans consentement à demander :
  administrer un avantage prévu aux conditions de travail est l'exécution du lien
  d'emploi. Demander un consentement laisserait croire qu'il peut être refusé
  sans que l'avantage s'arrête, ce qui est faux.
* Une règle de conservation, RH-EX-1, alignée sur RH-001 « Dossiers d'employés » :
  3 ans actifs, 2 semi-actifs : un seul régime pour tout le dossier.
* Les trois modèles porteurs de renseignements personnels deviennent
  classifiables : le droit, l'usage et la demande.

L'agrégat, écrit AVANT la destruction
-------------------------------------

Le registre d'usage est ce qui permet de dire « on paie pour ça, personne ne le
prend ». Détruit, il ne se reconstitue pas, et l'entreprise perd la mesure en
même temps que la donnée personnelle. Or elle n'a besoin que de la mesure.

`bf.ex.usage.aggregate` garde, par avantage et par année, le nombre de personnes
distinctes, le nombre d'usages, le coût, et le taux d'adhésion. **Aucun nom,
aucun identifiant de personne.** L'agrégat survit à la destruction des lignes.

🔴 Et le pont l'impose : une campagne qui tente de détruire une ligne d'usage
dont l'année n'a pas encore été agrégée **lève**. Elle ne détruit pas, et elle
n'inscrit rien au registre. C'est le seul ordre qui marche : agréger d'abord,
détruire ensuite.

Les pièces jointes
------------------

⚠️ `mail.thread.unlink` supprime les messages et les abonnés, pas les pièces
jointes rattachées directement à l'enregistrement. Un reçu déposé sur une ligne
d'usage survivrait donc à la destruction de la ligne. Le pont les supprime
explicitement.
""",
    "depends": [
        "bf_employee_experience",
        "privacy_consent",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/privacy_purpose_data.xml",
        "data/privacy_retention_calendar_data.xml",
        "views/usage_aggregate_views.xml",
    ],
}

{
    "name": "Registre de formation : la séance et sa feuille signée",
    "version": "18.0.1.0.1",
    "category": "Human Resources",
    "summary": "La séance en salle tenue par Événements, et la feuille de présence "
               "signée qui en fait une preuve",
    "description": """
Registre de formation : la séance et sa feuille signée
======================================================

Odoo **Événements** sait déjà tenir une séance : la date, le lieu, les places, les
inscriptions, le pointage au code-barres. Ce pont ne le refait pas. Il apporte les
deux choses qu'une séance de formation exige et qu'un événement ne porte pas :
**la feuille de présence signée**, et **l'écriture au registre**.

* **Une séance s'adosse à une activité.** Une activité a autant de séances qu'on
  veut : trois secourismes en neuf ans, c'est trois séances et trois lignes.
* **La présence écrit la réalisation**, datée du **jour de la séance**.
* **La feuille se signe, personne par personne**, et se produit en PDF à joindre
  au dossier.

Deux faux amis d'Événements, mesurés dans le code
-------------------------------------------------

1. **« Date de présence » n'est pas la date de la formation.** ``date_closed``
   vaut l'instant où quelqu'un a coché, pas le jour où la séance a eu lieu. Une
   séance de mars pointée en septembre serait consignée en septembre. Le registre
   prend la date de la **séance**.
2. **Une inscription annulée garde sa date de présence.** Le calcul entier est
   gardé par ``if not date_closed`` : une fois la valeur posée, rien ne l'efface.
   Lire ce champ seul fait compter des présents qui ne sont pas venus. Le registre
   ne lit que l'état ``Présent``. ⚠️ Le champ étant un calcul *stocké*, la valeur
   n'est posée que si le calcul a tourné avant l'annulation — ce qui est le cas
   dès que les deux gestes sont séparés par une transaction, donc toujours en
   usage réel.

Ce que le pont refuse
---------------------

**Une présence non signée ne vaut pas zéro : elle vaut « on ne sait pas ».** La
réalisation est écrite — la personne était là — mais elle est marquée incomplète,
avec « la signature de l'apprenant » dans ce qui lui manque. Perdre la présence
parce que la signature manque serait pire que de la consigner imparfaitement.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_training", "event"],
    "data": [
        "report/attendance_sheet_report.xml",
        "views/event_event_views.xml",
        "views/event_registration_views.xml",
        "views/training_activity_views.xml",
    ],
    "installable": True,
    "application": False,
}

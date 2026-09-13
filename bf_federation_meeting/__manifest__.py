{
    "name": "Fédération : ordres du jour",
    "version": "18.0.1.0.0",
    "category": "Project",
    "summary": "L'ordre du jour d'une rencontre paraît chez le pair, qui peut y proposer un sujet",
    "description": """
Fédération : ordres du jour
===========================

Avec la tâche, l'ordre du jour est le seul objet de la fédération qui vive
vraiment des deux côtés : il se construit avant la rencontre, et les deux
équipes ont des choses à y mettre.

* **Il voyage en lecture.** Le pair voit le titre, la date, les objectifs, le
  contexte et les sujets publiés, dans l'ordre. Il ne les réécrit pas : la passe
  de raffinage repasse dessus chez l'émetteur, et deux plumes sur le même objet
  fabriquent des conflits.
* **Un seul geste revient : proposer un sujet.** Le sujet arrive chez l'émetteur
  en « Proposé par un destinataire », à examiner, et il n'entre ni dans le PDF ni
  dans le courriel tant que personne ne l'a accepté. C'est exactement la porte que
  `bf_meeting` ouvre déjà par lien public, servie là où le pair travaille.
* **Ce qui ne traverse jamais** : les notes en direct, le verbatim, l'état du
  raffinage, la banque d'heures et les feuilles de temps.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_federation", "bf_meeting"],
    "data": [
        "security/ir.model.access.csv",
        "views/meeting_agenda_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}

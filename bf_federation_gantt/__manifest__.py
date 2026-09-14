{
    "name": "Fédération : échéanciers",
    "version": "18.0.1.0.1",
    "category": "Project",
    "summary": "L'échéancier d'un chantier commun vit chez les deux firmes, et la date qui bouge bouge des deux côtés",
    "description": """
Fédération : échéanciers
========================

Deux firmes sur un chantier commun partagent un échéancier : l'entrepreneur
général et son sous-traitant, l'agence et son client, le consultant et l'équipe
qui implante. Aujourd'hui l'échéancier part en PDF, il est périmé le lendemain,
et chacun recopie les dates dans son propre outil.

Ce module fait traverser l'échéancier autonome de `bf_gantt`.

* **Le miroir est un vrai échéancier chez le pair** : ses lignes, ses couloirs,
  ses jalons, ses dépendances, son avancement, et le composant Gantt pour le lire.
* **Ce qui bouge suit.** Déplacer une ligne, marquer un jalon atteint ou changer
  l'avancement renvoie l'échéancier, et une empreinte évite de renvoyer ce qui n'a
  pas changé.
* **Le miroir se lit.** Un échéancier reçu ne se retouche pas, et il ne se publie
  pas au portail du pair : l'émetteur l'a partagé avec une entreprise, pas avec
  les clients de celle-ci.
* **Ce qui ne traverse pas** : les heures prévues de chaque ligne. Un échéancier
  partagé porte des dates, pas l'effort que chacun y met.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_federation", "bf_gantt"],
    "data": ["views/bf_gantt_plan_views.xml"],
    "installable": True,
    "application": False,
    "auto_install": False,
}

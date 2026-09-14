{
    "name": "Fédération : suivis de démarchage",
    "version": "18.0.1.0.0",
    "category": "Sales",
    "summary": "L'agence qui démarche pour son client lui montre ses cibles et ses touches, et le client peut en écarter",
    "description": """
Fédération : suivis de démarchage
=================================

Une agence démarche pour le compte de son client, et les deux sont sur Symbifox.
Le client veut savoir qui a été joint, qui a répondu, où en est chaque cible, et
surtout pouvoir dire « pas celui-là, c'est déjà notre client » avant que l'agence
n'appelle.

Ce module fait traverser un **suivi** de la campagne, pas la campagne.

* **Un suivi, pas une campagne.** Chez le client, le miroir est un rapport en
  lecture, jamais une campagne de démarchage : la campagne porte une cadence et
  un cron qui crée des activités, et un miroir ne doit rien déclencher chez celui
  qui le reçoit. Le client n'a donc pas besoin de l'application Démarchage.
* **Aucune donnée personnelle par défaut.** L'entreprise ciblée, son étape, ses
  touches datées et leur issue traversent. Le nom de la personne-ressource, ses
  coordonnées et le résumé des touches ne traversent que si l'agence coche la case,
  pour un mandat où le client en est le responsable.
* **Le client peut écarter une cible.** Elle repart chez l'agence et y devient
  « ne pas contacter », avec le motif. C'est la seule chose qui revient, et elle ne
  sait qu'écarter : jamais ajouter une cible, jamais changer une étape.
* **Le suivi se rafraîchit chaque jour**, et à la demande.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_federation"],
    "data": [
        "security/ir.model.access.csv",
        "security/federation_outreach_security.xml",
        "data/federation_outreach_cron.xml",
        "views/federation_outreach_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}

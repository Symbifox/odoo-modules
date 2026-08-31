{
    "name": "Prévision glissante — amorce par les engagements datés",
    "version": "18.0.1.0.0",
    "category": "Accounting/Accounting",
    "summary": "La prévision s'amorce sur le calendrier des abonnements, pas sur une moyenne plate",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "auto_install": True,
    "depends": ["bf_budget_forecast", "bf_budget_subscription"],
    "description": """
Prévision glissante — amorce par les engagements datés
======================================================

Le socle de prévision amorce les mois ouverts avec la moyenne du réel des mois
clos. C'est honnête et ça ne suppose rien, mais c'est plat : un renouvellement
annuel se retrouve étalé sur douze mois, et le mois où il tombe est sous-estimé
de tout son montant.

Ce pont remplace cette moyenne par une décomposition en deux morceaux :

**Ce qui est daté** se pose au mois où il tombe, lu dans le calendrier des
abonnements du poste.

**Le reste** est moyenné, mais seulement le reste : la moyenne se calcule sur le
réel des mois clos DIMINUÉ de ce que les engagements datés y expliquaient déjà.
Sans cette soustraction, le récurrent serait compté deux fois — une fois dans sa
date, une fois dans la moyenne.

⚠️ Un abonnement à la demande n'a pas de calendrier. Il tombe donc dans « le
reste », où la moyenne le rattrape, ce qui est le comportement souhaitable :
mieux vaut l'étaler que de le perdre.
""",
    "data": [],
}

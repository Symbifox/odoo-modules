{
    "name": "Membres : AGA et registre corporatif",
    "summary": "Une proposition adoptée en assemblée des membres s'inscrit au "
               "registre des résolutions de la gouvernance corporative",
    "version": "18.0.1.0.2",
    "category": "Association",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Membres : AGA et registre corporatif
====================================

Le pont entre l'assemblée des membres (`bf_membership_assembly`) et le livre
des minutes (`bf_corporate_governance`). Installé d'office quand les deux sont
là.

Ce qu'il ajoute
---------------

* Sur une proposition adoptée, une fois l'assemblée close et si elle a
  atteint le quorum, le bouton **Inscrire au registre corporatif** crée la
  résolution (`corporate.resolution`) : titre, texte, totaux du vote, séance
  d'AGA si l'assemblée est annuelle, extraordinaire sinon, et la date de
  l'assemblée. Le registre ne reçoit pas qui a proposé ni qui a appuyé : il se
  lit sans le rôle Membres, et ces noms restent à l'assemblée.
* La résolution inscrite garde ce que l'assemblée a adopté : son titre, son
  texte, ses totaux et sa séance ne se réécrivent pas au registre, et elle ne
  s'y supprime pas.
* Le lien dans les deux sens : la résolution nomme sa proposition, la
  proposition montre sa résolution.
* **Pas de doublon** : un second clic rouvre la résolution existante, et une
  contrainte d'unicité en base tient même devant deux clics simultanés.

Ce qu'il ne fait pas
--------------------

* Il n'inscrit rien tout seul, et rien avant la clôture : une résolution
  inscrite pendant que les totaux bougent encore ne dirait plus ce que
  l'assemblée a décidé.
* Il ne contourne pas les droits du registre : seule une personne
  gestionnaire de la gouvernance corporative y inscrit une résolution.
""",
    "depends": [
        "bf_membership_assembly",
        "bf_corporate_governance",
    ],
    "data": [
        "views/proposal_views.xml",
        "views/assembly_views.xml",
        "views/corporate_resolution_views.xml",
    ],
}

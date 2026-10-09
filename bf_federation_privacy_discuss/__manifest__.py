{
    "name": "Fédération : pas de canal pour un avis de violation",
    "version": "18.0.1.0.0",
    "category": "Privacy/Compliance",
    "summary": "Un avis de violation fédéré n'a pas de canal de discussion : le fil de l'avis et le "
               "courriel font foi",
    "description": """
Fédération : pas de canal pour un avis de violation
===================================================

Installé de lui-même là où le canal fédéré (`bf_federation_discuss`) et l'avis fédéré
(`bf_federation_privacy`) se côtoient.

Le canal d'un objet fédéré admet quiconque peut LIRE l'objet, et ce qui s'y écrit repart au
fil de l'objet puis chez le pair. Pour un avis de violation, un lecteur parlerait ainsi au
client au nom du mandataire, dans le fil qui fait preuve. Un avis n'est pas une conversation :
il n'a pas de canal, et personne n'y est admis.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_federation_privacy", "bf_federation_discuss"],
    "data": [],
    "installable": True,
    "application": False,
    "auto_install": True,
}

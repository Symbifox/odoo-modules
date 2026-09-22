# -*- coding: utf-8 -*-
{
    "name": "Babillard : sondage de disponibilités",
    "summary": "Annoncer au fil qu'un sondage de dates cherche des réponses",
    "version": "18.0.1.0.0",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_babillard", "bf_appointment_poll"],
    "data": ["views/appointment_poll_views.xml"],
    "auto_install": True,
    "installable": True,
    "description": """
Pont Babillard ↔ Sondage de disponibilités
==========================================

S'auto-installe quand `bf_babillard` et le sondage de disponibilités sont tous
deux là.

Un sondage de disponibilités s'adresse à des gens qui n'ont pas de compte :
chacun y répond par un lien qui lui est propre. Le babillard, lui, s'adresse à
la maison. Ce pont fait la seule chose qui traverse honnêtement cette frontière,
c'est-à-dire annoncer au fil qu'un sondage cherche des réponses.

🔴 La carte ne porte JAMAIS le lien de vote d'un participant : ce lien est
nominatif, et le poser sur une carte lue par toute la maison reviendrait à
prêter à tout le monde la voix d'une seule personne. Quand le sondage offre
l'inscription libre, c'est ce lien-là, public par construction, que la carte
porte. Sinon, elle annonce sans lien, et c'est l'organisateur qui invite.
""",
}

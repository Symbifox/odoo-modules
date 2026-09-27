{
    "name": "Copropriété : satisfaction des occupants",
    "summary": "Demander à l'occupant comment s'est passée sa demande d'entretien, une fois réglée",
    "version": "18.0.1.4.0",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — voir la note du manifeste de bf_property_core. Le fichier
    # LICENSE fait foi, et sa Change Date se retamponne à la publication.
    "license": "Other proprietary",
    "images": ["static/description/satisfaction_en.png"],
    "application": False,
    "installable": True,
    # ⚠️ Pont, sur le patron de bf_cx : il s'installe SEUL quand les deux côtés
    # sont là, et la suite de copropriété fonctionne parfaitement sans lui.
    "auto_install": True,
    "description": """
Copropriété : satisfaction des occupants
========================================

Le pont entre les demandes d'entretien du portail et le programme d'écoute de
bf_cx. Il fait une seule chose : quand une demande est **réglée**, il demande à
la personne qui l'avait déposée comment ça s'est passé, en trois émojis.

⚠️ **Une demande réglée, jamais une demande refusée.** Refuser, c'est dire que
la demande sort de l'objet du syndicat (art. 1039 C.c.Q.). Demander sa
satisfaction juste après mesurerait le refus, pas le service, et personne n'a
besoin d'un logiciel pour deviner la réponse. Le module se tait.

⚠️ **L'interrupteur est au syndicat, et il est à l'arrêt.** Rien ne part vers un
occupant sans que le syndicat ait ouvert la mesure. Le garde-fou
anti-sursollicitation de bf_cx s'applique ensuite, et chaque courriel porte le
lien de désabonnement de bf_cx : l'occupant garde la main.

⚠️ **Le courriel ne porte aucune marque d'éditeur.** Comme l'attestation de
l'art. 1068.1, il vient du syndicat et de personne d'autre : ni logo, ni
couleur, ni nom d'outil. Un syndicat qui demande l'avis de ses occupants ne fait
pas la publicité de son logiciel.

⚠️ **Une demande de feedback qui échoue ne referme jamais la demande.** Elle
tient dans un point de reprise : le concierge vient de dire ce qui a été fait,
et ce geste-là ne se perd pas parce qu'un courriel n'est pas parti.
""",
    "depends": [
        "bf_property_portal",
        "bf_cx",
    ],
    "data": [
        "data/mail_template_data.xml",
        "views/bf_property_cx_views.xml",
    ],
}

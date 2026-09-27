# -*- coding: utf-8 -*-
{
    "name": "Visites de propriétés — pont Immeubles",
    # 18.0.1.0.0: l'inscription à visiter peut désigner un logement du socle
    #   Immeubles. L'adresse, l'occupant et le régime d'occupation en
    #   descendent, et le logement gagne un bouton « Faire visiter ».
    "version": "18.0.1.0.0",
    "category": "Appointments",
    "summary": "Faire visiter un logement du parc, sans ressaisir son adresse",
    "description": """
Pont entre les visites et le socle Immeubles
============================================

Un courtier n'a pas de parc immobilier : le module de visites se tient donc
seul, et ce pont n'existe que pour qui gère des immeubles. Installé, il permet
de désigner un logement plutôt que de retaper une adresse, et il en tire ce qui
change les règles de visite : un logement loué impose le préavis de 24 heures et
les heures de 9 h à 21 h, et son occupant reçoit l'avis.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["bf_appointment_visit", "bf_property_core"],
    "data": [
        "views/bf_visit_listing_property_views.xml",
        "views/bf_property_unit_views.xml",
    ],
    "installable": True,
    "application": False,
}

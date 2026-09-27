{
    "name": "Copropriété : avis par texto",
    "summary": "Prévenir l'occupant d'un colis ou d'un avis urgent, avec son consentement exprès",
    "version": "18.0.2.2.0",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — voir la note du manifeste de bf_property_core. Le fichier
    # LICENSE fait foi, et sa Change Date se retamponne à la publication.
    "license": "Other proprietary",
    "images": ["static/description/sms_consent_en.png"],
    "application": False,
    "installable": True,
    # ⚠️ Pont, sur le patron de bf_cx : il s'installe SEUL quand les deux côtés
    # sont là, et la suite de copropriété fonctionne parfaitement sans lui. Un
    # syndicat qui n'a pas de ligne de texto n'a rien à désinstaller.
    "auto_install": True,
    "description": """
Copropriété : avis par texto
============================

Le pont entre le portail de l'occupant et la messagerie texto. Il fait une
seule chose : prévenir quelqu'un que son colis est arrivé, ou qu'un avis urgent
le concerne. Il ne reçoit rien, il ne converse pas.

⚠️ **Le numéro de téléphone n'est PAS au registre de droit.** L'art. 1070 al. 1
C.c.Q. y met le nom et l'adresse de chaque copropriétaire et de chaque
occupant, et n'y met les **autres** renseignements personnels que **si la
personne y consent expressément**. Un numéro de téléphone en est un. Le module
porte donc un consentement par personne, daté, et **aucun texto ne part sans
lui**, quel que soit le réglage du syndicat.

⚠️ **Deux interrupteurs, pas un.** Le syndicat active le canal, la personne
consent à être jointe. Les deux sont à l'arrêt par défaut. Le premier sans le
second n'envoie rien.

⚠️ **Ce que le transporteur garde ne suit pas la purge.** Le journal des colis
et des visiteurs se purge selon la durée que le syndicat a fixée. Le
fournisseur de texto, lui, garde ce qu'il garde, et l'appareil du destinataire
aussi. Le module le dit à l'écran plutôt que de laisser croire à une politique
de conservation qui ne vaudrait que chez lui.

⚠️ **Le message en dit le moins possible.** « Un colis vous attend » et le nom
du syndicat. Pas de numéro de porte, pas de transporteur, pas de référence de
suivi : ce sont des renseignements qui n'ont pas à traverser un réseau de
télécommunication pour que la personne descende chercher son colis.
""",
    "depends": [
        "bf_property_portal",
        "bf_sms_archive",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/bf_property_sms_views.xml",
    ],
}

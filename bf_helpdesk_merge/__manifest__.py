{
    "name": "Helpdesk — Doublons, fusion et incidents",
    "summary": "Fusion complète des billets (feuilles de temps, pièces jointes, abonnés, notes internes), suggestion de doublons à la création, incidents parents avec réponse groupée",
    "version": "18.0.1.3.2",
    "category": "After-Sales",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "AGPL-3",
    "depends": ["bf_helpdesk", "helpdesk_mgmt_merge", "helpdesk_ticket_related"],
    "data": [
        "security/ir.model.access.csv",
        "security/helpdesk_merge_security.xml",
        "views/helpdesk_ticket_views.xml",
    ],
    "installable": True,
    "auto_install": False,
}

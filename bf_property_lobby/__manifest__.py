{
    "name": "Immeubles : portier sur tablette",
    "summary": "Une tablette à l'entrée : l'annuaire des résidents qui y consentent, "
               "un appel au résident par la téléphonie Symbifox, la livraison annoncée "
               "au concierge",
    # 18.0.1.0.0 : premier jet (lot 6).
    "version": "18.0.1.0.3",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — le fichier LICENSE fait foi (voir bf_property_access).
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": False,
    "description": """
Immeubles : portier sur tablette
================================

Une tablette (ou un écran tactile) à l'entrée de l'immeuble tient lieu
d'interphone : le visiteur cherche un nom ou un numéro de logement, touche
« Appeler », et le téléphone du résident sonne par la téléphonie Symbifox
(``bf_softphone``) quand elle est configurée ; sinon, le concierge est prévenu.
Un bouton « Livraison » prévient le concierge.

* **L'annuaire ne montre que ceux qui y consentent**, avec le libellé qu'ils
  choisissent (Loi 25 : par défaut, personne n'y paraît). Le consentement se
  donne et se retire sur la page « Mes accès » du portail.
* **Aucun numéro n'est affiché** : la tablette ne voit que des libellés.
* **La porte ne s'ouvre pas d'ici** : la tablette appelle, le résident ouvre
  par le portier existant. Aucune biométrie, aucune caméra.
* **Une page par appareil**, protégée par un jeton dont seule l'empreinte est
  conservée ; appels plafonnés par minute et par résident.
* Journal des appels purgé au délai de l'organisation.
""",
    "depends": ["bf_property_access_portal"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_property_lobby_security.xml",
        "data/ir_cron_data.xml",
        "data/mail_activity_data.xml",
        "data/mail_template_lobby.xml",
        "views/bf_property_lobby_views.xml",
        "views/lobby_templates.xml",
    ],
}

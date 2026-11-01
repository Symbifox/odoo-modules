{
    "name": "Immeubles : mes accès au portail",
    "summary": "Le résident voit au portail les portes qu'il peut ouvrir, avec quel "
               "support, et ce qui est en préparation ou à retirer",
    # 18.0.1.0.0 : premier jet (lot 1 bis).
    # 18.0.1.1.0 : accès temporaire d'un visiteur annoncé au portail (lot 4).
    "version": "18.0.1.1.3",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — le fichier LICENSE fait foi (voir bf_property_access).
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Immeubles : mes accès au portail
================================

Pont entre les accès des résidents (``bf_property_access``) et le portail de
l'immeuble (``bf_property_portal``). Une page **Mes accès** et une carte sur
l'accueil du portail : chaque point d'accès que la personne peut ouvrir, le
support (entrée d'annuaire, carte, code, identifiant mobile, clé), sa
référence et son état, y compris ce qui attend des consentements et ce qui
doit être retiré.

Une règle d'accès borne chacun à ses propres droits. Aucun code n'est
affiché : le module n'en conserve aucun.
""",
    "depends": ["bf_property_access", "bf_property_portal"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_property_access_portal_security.xml",
        "views/portal_templates.xml",
    ],
}

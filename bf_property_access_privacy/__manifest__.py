{
    "name": "Immeubles : accès et vie privée (Loi 25)",
    "summary": "Le contrôle d'accès inscrit au registre des traitements, la durée "
               "déclarée comparée à celle appliquée, et un gabarit d'EFVP prérempli",
    # 18.0.1.0.0 : premier jet (lot 1 bis).
    # 18.0.1.0.3 : l'avis, la finalité et les catégories nomment ce qu'une boîte
    #   à colis reliée reçoit en plus : un moyen de joindre le résident
    #   (bf_property_access_expedibox, lot 5).
    "version": "18.0.1.0.3",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — le fichier LICENSE fait foi (voir bf_property_access).
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Immeubles : accès et vie privée (Loi 25)
========================================

Pont entre les accès des résidents (``bf_property_access``) et le registre
Loi 25 (``privacy_consent``).

* **Finalité « Contrôle d'accès à l'immeuble »** au registre des activités de
  traitement, avec son avis d'information (art. 8 LPRPSP) et sa politique de
  conservation (90 jours après le départ, suppression).
* **Ce qui est déclaré contre ce qui est fait** : l'onglet Accès de
  l'organisation compare la durée déclarée à celle que l'organisation
  applique, et le dit quand elles divergent.
* **Gabarit d'EFVP** (art. 3.3) : un document imprimable, prérempli avec les
  points d'accès, les supports, les connecteurs, les résidents et la
  conservation de l'organisation, et des cases à compléter par le
  responsable de la protection des renseignements personnels.

Ni biométrie ni code conservé : le gabarit le consigne.
""",
    "depends": ["bf_property_access", "privacy_consent"],
    "data": [
        "data/privacy_purpose_data.xml",
        "report/efvp_report.xml",
        "views/bf_property_organisation_views.xml",
    ],
}

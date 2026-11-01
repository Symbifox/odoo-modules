{
    "name": "Locatif : accès des locataires",
    "summary": "Les signataires d'un bail reçoivent leurs accès au début du bail ; "
               "la fin du loyer, la résiliation ou le déguerpissement mettent leur "
               "départ à confirmer",
    # 18.0.1.0.0 : premier jet (lot 1).
    # 18.0.1.0.1 : migration 0.0.0 qui recharge les catalogues à chaque version.
    # 18.0.1.0.2 : aide du champ Bail traduite en anglais.
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
Locatif : accès des locataires
==============================

Pont entre le bail (``bf_rental``) et les accès (``bf_property_access``).

* Chaque signataire d'un bail devient résident de son logement le jour du
  début du bail (« à venir » avant), ce que le gestionnaire peut avancer.
* Ce qui met le départ **à confirmer**, sans rien retirer : signataire
  retiré du bail, bail archivé, bail plus récent commencé sur le même
  logement, déguerpissement constaté, « Loyer dû jusqu'au » dépassé (avec
  ``bf_rental_rent``), résiliation par le locataire effective (avec
  ``bf_rental_notice``).
* La date de fin du bail ne compte pas : le bail se reconduit de plein
  droit (art. 1941 C.c.Q.).
""",
    "depends": ["bf_property_access", "bf_rental"],
    "data": [
        "views/bf_property_access_resident_views.xml",
    ],
}

{
    "name": "Démarchage — pont Vie privée (Loi 25)",
    "summary": "Finalité de prospection au registre, conservation des cibles "
               "démarchées, liste d'opposition par empreinte et destruction "
               "inscrite au registre",
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    # ⚠️ PAS d'auto_install, comme les autres ponts de rétention : ce pont mène à
    # des destructions, et une durée de conservation est une décision
    # d'entreprise, prise par le responsable de la vie privée de CETTE
    # entreprise-là, inscrite à SON registre. Installation à la main, base par
    # base (décision du 2026-10-18).
    "depends": [
        "bf_outreach",
        "privacy_consent",
    ],
    "description": """
Démarchage — pont Vie privée (Loi 25)
=====================================

`bf_outreach` conserve des personnes qui n'ont rien demandé : nom, fonction,
courriel, téléphone, source de la cible, notes libres, et l'historique complet
des interactions avec leurs propres notes. Le module ne déclarait ni finalité,
ni base légale, ni durée, et n'avait aucune purge.

Ce que ce pont fait
-------------------

1. **Il déclare la finalité** de prospection au registre des traitements.
2. **Il pose deux règles** au calendrier de conservation : DEM-001 (cible sans
   refus, trois ans après le dernier contact utile) et DEM-002 (fiche d'une
   personne qui a refusé : proposée à la destruction sans délai, la preuve du
   refus vivant ailleurs).
3. **Il tient une liste d'opposition par empreinte.** Au moment même du refus,
   le SHA-256 du courriel et des numéros normalisés est inscrit avec la date,
   l'auteur de la note et le motif. Cette inscription n'a pas d'échéance et
   bloque la réimportation de la personne par ses coordonnées.
4. **Il propose, il ne détruit pas de lui-même.** Un cron quotidien dépose les
   cibles échues dans une campagne de destruction laissée en révision ; le
   responsable de la vie privée approuve puis exécute, et c'est la campagne
   qui écrit au registre.
5. **Il rend les cibles et les interactions classifiables.**

Voir le README pour les durées, leurs raisons, et ce que la destruction ne
couvre pas.
""",
    "data": [
        "security/ir.model.access.csv",
        "data/privacy_purpose_data.xml",
        "data/privacy_retention_calendar_data.xml",
        "data/bf_outreach_privacy_cron.xml",
        "views/outreach_optout_views.xml",
    ],
    "post_init_hook": "post_init_hook",
}

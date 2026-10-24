{
    "name": "Persona des contacts — pont Vie privée (Loi 25)",
    "summary": "Finalité du profil relationnel et de la cote de payeur au registre, "
               "conservation à partir de la fin de la relation, destruction inscrite "
               "au registre, personas classifiables",
    "version": "18.0.1.0.0",
    "category": "Sales/CRM",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    # ⚠️ PAS d'auto_install, sur le patron des autres ponts : ce pont mène à des
    # destructions, et une durée de conservation est une décision d'entreprise,
    # prise par le responsable de la vie privée de CETTE entreprise-là, inscrite
    # à SON registre. Installation à la main, base par base.
    "depends": [
        "bf_persona",
        "privacy_consent",
    ],
    "description": """
Persona des contacts — pont Vie privée (Loi 25)
===============================================

`bf_persona` conserve, sur des personnes nommées, un profil relationnel (ton,
registre, salutation, ce qu'il faut savoir avant d'écrire, règles de copie,
relation mesurée) et une cote de payeur (qualité, notes, délai moyen de
paiement, indicateurs comptables). Le module ne déclarait ni finalité, ni base
légale, ni durée, et n'avait aucune purge.

Ce que ce pont fait
-------------------

1. **Il déclare la finalité** « Profil relationnel des contacts et cote de
   payeur » au registre des traitements.
2. **Il pose la règle PER-001** au calendrier de conservation : trois ans après
   la fin de la relation d'affaires, c'est-à-dire après le dernier courriel
   reçu de la personne ou envoyé à elle (pour un persona de société : de
   n'importe qui de la société, ou sa dernière facture). Jamais tant qu'une
   facture qui lui est adressée reste impayée.
3. **Il propose, il ne détruit pas de lui-même.** Un cron quotidien dépose les
   personas échus dans une campagne de destruction laissée en révision ; le
   responsable de la vie privée approuve puis exécute, et c'est la campagne qui
   écrit au registre.
4. **Il rend le persona, ses indicateurs et ses règles de copie classifiables.**

Voir le README pour la durée, l'ancrage, et ce que la destruction ne couvre pas.
""",
    "data": [
        "data/privacy_purpose_data.xml",
        "data/privacy_retention_calendar_data.xml",
        "data/bf_persona_privacy_cron.xml",
        "views/privacy_destruction_campaign_views.xml",
    ],
}

{
    "name": "Rencontres — Pont vie privée (Loi 25)",
    "summary": "Déclarer au registre des traitements ce que les comptes rendus "
               "collectent, rendre les rencontres classifiables, et poser une "
               "règle de conservation sur les transcriptions et les enregistrements",
    "version": "18.0.1.2.0",
    "category": "Services/Privacy",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    # Pont, sur le patron de bf_cx_privacy, bf_property_privacy et
    # bf_securetransfer_privacy : il s'installe seul quand les deux côtés sont
    # là, et les rencontres fonctionnent parfaitement sans lui.
    "auto_install": True,
    "description": """
Rencontres — Pont vie privée (Loi 25)
=====================================

`meeting.record` contient des verbatims : ce que des personnes nommées ont dit
en rencontre, transcrit par Whisper. S'y ajoutent les présences
(`meeting.attendance` — qui était là, qui manquait), les décisions attribuées à
un décideur, et les pointeurs vers l'audio déposé sur Nextcloud. Sur une base
réelle, la quasi-totalité des comptes rendus portent une transcription brute, et
les présences nomment des dizaines de personnes.

Rien de tout cela n'était déclaré. `bf_meeting` ne dépend pas de
`privacy_consent`, le modèle n'était pas classifiable, et aucune règle de
conservation ne s'y appliquait.

Ce que ce pont fait
-------------------

1. **Il déclare la finalité du compte rendu** au registre des traitements. Les
   finalités « enregistrement » et « transcription » existaient déjà dans
   `privacy_consent` ; ce qui manquait, c'est le compte rendu lui-même, qui
   survit à l'enregistrement et qui est le document réellement conservé.
2. **Il rattache une politique de conservation** aux finalités d'enregistrement
   et de transcription : ce qui arrive à la matière première quand le
   consentement est retiré ou expire.
3. **Il rend `meeting.record` et `meeting.agenda` classifiables**, via le point
   d'extension `_privacy_classifiable_models()`.
4. **Il pose deux règles de conservation** : le compte rendu d'un côté, la
   matière première (transcription brute et pointeurs vers l'audio) de l'autre,
   avec une durée plus courte.
5. **Il purge pour vrai** la matière première, et consigne au registre ce qu'il
   vient d'effacer.
6. **Il montre le consentement à l'écran** : sur une rencontre enregistrée, les
   participants externes sans consentement d'enregistrement accordé sont nommés.

⚠️ **Deux régimes, deux durées.** L'enregistrement et la transcription brute
sont la matière première ; leur fin est accomplie quand le compte rendu est
écrit. Le compte rendu, lui, documente le mandat et se conserve avec le dossier
de projet. Les garder ensemble reviendrait à conserver six ans un verbatim mot
à mot dont on n'a plus besoin après un an.

🔴 **La campagne générique de destruction ne convient PAS à ces modèles, et le
pont la corrige.** Dans `privacy_consent`,
`privacy.destruction.campaign.line._execute_destruction()` traite la méthode
« Suppression » ainsi : si le modèle porte un champ `active`, il ARCHIVE au lieu
de supprimer. `meeting.record` porte `active`. Une campagne aurait donc archivé
le compte rendu — verbatim intact, lisible — puis écrit au registre immuable une
entrée certifiant sa suppression. Ce pont surcharge la méthode pour ses propres
modèles : il fait l'opération réelle, ou il lève. Une ligne qui lève n'est pas
certifiée.

⚠️ **Ce pont ne touche pas aux enregistrements d'appels.**
`bf_meeting_call_archive` relie un compte rendu à un appel archivé, mais
`call.archive.call` appartient à `bf_sms_archive`, donc à son propre régime.
Déclarer ici une durée sur des enregistrements qu'un autre module détient
ferait deux régimes concurrents sur la même donnée.

⚠️ **L'audio sur Nextcloud n'est pas détruit par ce pont, et il ne prétend pas
l'être.** `source_nc_path` est un chemin, pas un fichier : Odoo n'a pas la main
dessus. La purge efface le pointeur et le dit — elle ne certifie pas la
destruction du fichier au bout du chemin.
""",
    "depends": [
        "bf_meeting",
        "privacy_consent",
    ],
    "data": [
        "data/privacy_purpose_data.xml",
        "data/privacy_retention_calendar_data.xml",
        "views/meeting_record_views.xml",
    ],
}

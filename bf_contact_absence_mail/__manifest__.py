# -*- coding: utf-8 -*-
{
    "name": "Symbifox Absences des contacts : lecture des répondeurs",
    # 18.0.1.0.0: premier jet. Reconnaît un répondeur d'absence dans la boîte
    #   unifiée, en lit la période, et pose une SUGGESTION sur la fiche du
    #   contact. Jamais une absence directement : sur les 28 répondeurs
    #   mesurés sur un corpus réel, 9 ne portent aucune date
    #   lisible et le filet attrape des MAILER-DAEMON.
    "version": "18.0.1.0.0",
    "category": "Sales/CRM",
    "summary": "Reconnaître les répondeurs d'absence reçus et proposer la "
               "période au lieu de l'écrire",
    "description": """
Lecture des répondeurs d'absence
================================

Quand un contact répond « je serai en vacances jusqu'au 15 », l'information
arrive déjà chez nous. Ce module la ramasse.

Ce qu'il fait
-------------

* **Reconnaître** un répondeur d'absence parmi le courrier entrant. La
  reconnaissance est déterministe : en-têtes normalisés d'abord, objets
  ensuite, et ce sont les objets qui font le gros du travail.
* **Lire la période** : un lecteur de dates en français et en anglais, et,
  quand Gen est joignable, une seconde lecture qui rattrape la prose que les
  expressions régulières ratent.
* **Proposer**, jamais écrire. Une suggestion s'accepte d'un clic et devient
  une absence ; elle se refuse d'un clic et ne revient pas.

Ce qu'il ne fait pas
--------------------

Il ne recopie **jamais** le texte du répondeur sur la fiche du contact : une
période, une nature, et au plus une relève. Le courriel d'origine reste à sa
place, dans la boîte unifiée, sous ses propres règles de conservation.

Gen est facultatif
------------------

Le module s'installe et fonctionne sur un locataire **sans** Gen : la
reconnaissance et la lecture des dates sont écrites en clair, et l'appel au
pont n'a lieu que si la socket répond. Rien ne quitte le serveur autrement.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    # ⚠️ `bf_ai_bridge` est une feuille nue, installable sur un locataire qui
    # n'a pas Gen : c'est ce qui permet de l'exiger en dur sans imposer Gen.
    "depends": ["bf_contact_absence", "bf_email_management", "bf_ai_bridge"],
    "data": [
        "security/ir.model.access.csv",
        "data/bf_absence_scan_cron.xml",
        "views/bf_absence_suggestion_views.xml",
        "views/bf_absence_backfill_wizard_views.xml",
        "views/bf_email_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
}

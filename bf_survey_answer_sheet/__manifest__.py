{
    "name": "Survey Answer Sheet",
    # 18.0.1.0.0: une réponse terminée se classe d'elle-même : une feuille de
    #   réponses brandée, chiffrée en AES-256, déposée dans le dossier
    #   Nextcloud de l'entreprise qui a répondu. Le mot de passe ne voyage pas
    #   avec le fichier : il se remet par un autre canal.
    # 18.0.1.0.1: les essais ne nomment plus d'entreprises réelles.
    # 18.0.1.0.2: 🔴 les réglages qui adressent le nuage sont refusés au
    #   `create()` à qui n'est pas des Paramètres. Mesuré : Odoo vérifie le
    #   `groups=` d'un champ en lecture, en écriture et en recherche, PAS à la
    #   création - et tout usager du groupe Sondages crée un sondage. La carte
    #   d'etags d'une réponse est fermée pour la même raison, le bouton de
    #   classement manuel demande le groupe des propriétaires du sondage, et
    #   une panne de Nextcloud ne se lit plus comme « le fichier d'un autre ».
    # 18.0.1.1.0: quand bf_survey_numbering numérote le sondage, la feuille
    #   reprend les mêmes numéros (« 2. Votre entreprise », « 2.1 … »), lus
    #   seulement si le champ existe, sans dépendance.
    # 18.0.1.1.1: les tableaux de la feuille se soustraient au style de tableau
    #   de la mise en page du rapport (`o_ignore_layout_styling`) : la mise en
    #   page standard leur ajoutait des bordures de cellule sans marge, et le
    #   premier caractère de chaque ligne collait au trait.
    # 18.0.1.1.2: la 1.1.1 ne suffisait pas, mesuré au rendu : le trait venait
    #   de wkhtmltopdf, qui dessine les bordures des cellules dès que leur style
    #   est « solid » (Bootstrap le pose sur toute cellule), même à largeur
    #   nulle. Chaque cellule pose `border-style:none` ; le filet gris du bas,
    #   écrit après, reste.
    "version": "18.0.1.1.2",
    "category": "Marketing/Surveys",
    "summary": "Each completed response filed by itself: a branded PDF of the "
               "answers, password-protected, in the respondent's own folder",
    "description": """
Survey answer sheet
===================

A completed response is a document somebody will have to read, keep and share.
Odoo offers two ways to get it out: the statistics page, which is about the
survey and not about one respondent, and the printable page, which the reader
has to print by hand. Both leave the filing to a person, and neither carries
the day the response was completed.

This module files the sheet by itself, from a single trigger: the submission.

- **A branded answer sheet.** One PDF per response: the respondent's own
  answers, section by section, in the company's colours, with the completion
  date in the header. Every question type is rendered, the percentage
  allocations and the matrices included.
- **Password-protected, AES-256.** The PDF is encrypted before it leaves Odoo.
  The password is the survey's, common to a cohort of readers, so that a file
  that strays is useless to whoever finds it. The owner password is random and
  kept nowhere: nobody can lift the restrictions of a filed sheet.
- **Filed in the respondent's own folder.** The company name the respondent
  declared is matched against the folders under a root you name; the sheet is
  deposited in the subfolders you list. A company with no folder yet gets one,
  copied from the template you name, at the name it declared.
- **It never overwrites somebody else's file.** A deposit is conditional: it
  creates the file, or replaces the one this very response wrote earlier -
  recognised by a marker inside the encrypted PDF, which only the password
  reveals. Another response's sheet of the same day is left alone and this one
  is filed beside it, under a numbered name. Anything else is a file this
  module did not write: nothing is touched, and the response says so.
- **The response keeps its copy.** The encrypted PDF stays attached to the
  response in Odoo, so it is found again without Nextcloud.
- **A courier that survives a bad night.** The filing runs in a scheduled
  action, never in the respondent's own request: a Nextcloud outage delays the
  sheet, it never breaks a submission. Each failure is written on the response,
  with what failed, and taken up again at the next round - five times, then it
  waits for a person.

Nothing is sent to anyone: this module files, it does not notify.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["survey", "bf_document_nextcloud_sync"],
    "external_dependencies": {"python": ["pyhanko"]},
    "data": [
        "report/answer_sheet_report.xml",
        "report/answer_sheet_templates.xml",
        "views/survey_survey_views.xml",
        "views/survey_user_input_views.xml",
        "data/ir_cron.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}

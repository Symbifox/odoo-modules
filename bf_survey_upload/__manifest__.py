{
    "name": "BF Survey Upload",
    "version": "18.0.1.2.0",
    "category": "Marketing/Surveys",
    "summary": "Type de question Téléversement de fichiers pour les sondages Odoo",
    "description": """
Ajoute un type de question "Téléversement de fichiers" aux sondages Odoo.
Les répondants peuvent joindre un ou plusieurs fichiers à leur réponse.
Les fichiers sont stockés comme pièces jointes (ir.attachment) liées à la
réponse au sondage. Option pour copier automatiquement les pièces jointes
sur le projet du répondant à la complétion du sondage.
""",
    'author': 'Les services de consultation Les services de consultation Blue Fox, Inc.',
    "website": "https://symbifox.com",
    'license': 'LGPL-3',
    "depends": ["survey", "project", "bf_onboarding_base"],
    "data": [
        "security/ir.model.access.csv",
        "data/bf_onboarding.xml",
        "views/survey_question_views.xml",
        "views/survey_views.xml",
        "views/survey_templates.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "bf_survey_upload/static/src/js/survey_form_file_upload.js",
            "bf_survey_upload/static/src/scss/file_upload.scss",
        ],
    },
    "installable": True,
    "application": False,
}

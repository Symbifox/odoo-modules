{
    "name": "BF Survey Upload",
    # 18.0.1.3.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    # 18.0.1.4.0: le type de question n'était injecté que dans le conteneur du
    #   formulaire, jamais dans la page imprimable : une question
    #   « Téléversement » y imprimait son titre et rien d'autre, et les fichiers
    #   reçus manquaient au PDF d'une réponse remplie, sans un mot.
    # 18.0.1.5.0: le JS était déclaré dans web.assets_frontend, où
    #   @survey/js/survey_form n'existe pas : il ne s'est jamais chargé (erreur
    #   JavaScript sur chaque page publique, connexion comprise), et aucun
    #   fichier ne pouvait être joint. Déplacé dans survey.survey_assets, et ses
    #   événements délégués depuis le formulaire, qui remplace le HTML de chaque
    #   page (un widget à sélecteur ne démarrait pas après la première).
    "version": "18.0.1.5.0",
    "category": "Marketing/Surveys",
    "summary": "Type de question Téléversement de fichiers pour les sondages Odoo",
    "description": """
Ajoute un type de question "Téléversement de fichiers" aux sondages Odoo.
Les répondants peuvent joindre un ou plusieurs fichiers à leur réponse.
Les fichiers sont stockés comme pièces jointes (ir.attachment) liées à la
réponse au sondage. Option pour copier automatiquement les pièces jointes
sur le projet du répondant à la complétion du sondage.
""",
    'author': 'Les services de consultation Blue Fox, Inc.',
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
        # The survey pages' own bundle, where survey_form lives.
        "survey.survey_assets": [
            "bf_survey_upload/static/src/js/survey_form_file_upload.js",
            "bf_survey_upload/static/src/scss/file_upload.scss",
        ],
    },
    "installable": True,
    "application": False,
}

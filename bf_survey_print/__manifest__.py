{
    "name": "BF Survey Print",
    # 18.0.1.0.0: La page imprimable d'un sondage (/survey/print/...)
    #   ne porte AUCUNE règle de saut de page : le cœur d'Odoo les garde pour la
    #   page des résultats seulement (`survey_templates_results.scss`). Mesuré sur
    #   un questionnaire de 41 questions en 8 sections : 12 questions coupées entre
    #   deux feuilles, dont la matrice de consentement de la dernière page.
    #   La feuille de style porte aussi la pleine largeur à l'impression, et le
    #   gabarit corrige une dépendance du cœur : à vide, une section sans
    #   description ne s'imprimait pas du tout, donc rien ne pouvait casser là.
    "version": "18.0.1.0.0",
    "category": "Marketing/Surveys",
    "summary": "Printing a survey breaks at the right places: a section starts a "
               "new sheet and a question is kept in one piece",
    "description": """
Survey print page breaks
========================

Odoo's printable survey page carries no page-break rule at all. The browser
breaks wherever the text happens to fall, so a question's title lands at the
bottom of a sheet and its choices on the next one, and a matrix table loses its
header row on the way across. A questionnaire printed to be filled in on paper
and handed back is then answered twice, or not at all.

This module adds the print rules the page is missing, plus one condition fixed
in the core print template. Nothing else: no field, no setting, nothing new on
screen.

- **A section starts a new sheet.** The sections of a survey laid out one page
  per section are the places the author already chose; printing now respects
  them.
- **A question is kept in one piece** between two sheets: title, help text,
  choices and matrix travel together. A question *taller* than a sheet is
  broken anyway — the constraint cannot be met — and on one real questionnaire
  of nine sections, four questions out of sixteen are that tall.
- **A section header is never left alone** at the foot of a sheet.
- **A matrix too tall for one sheet repeats its header row**, and no row of it
  is ever cut.
- **The text column uses the width of the sheet.** On screen the questionnaire
  is a narrow column, which reads well; on paper that column left 40 mm of
  margin on either side, which split questions that would have fitted and made
  the printout longer than it had to be.
- **A section with no description is printed too.** On a blank page the core
  shows a section only if it is described, so an undescribed one printed
  neither its title nor anything to break on.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["survey"],
    "data": [
        "views/survey_templates_print.xml",
    ],
    "assets": {
        # Le paquet propre aux pages de sondage. `web.assets_frontend` EST
        # servi sur la page imprimable (mesuré : `survey.layout` est une vue
        # primaire sur `web.frontend_layout`) ; ce qui le disqualifie, c'est sa
        # portée — ces règles n'ont rien à faire sur le site web.
        "survey.survey_assets": [
            "bf_survey_print/static/src/scss/survey_print.scss",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}

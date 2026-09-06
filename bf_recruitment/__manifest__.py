{
    "name": "Recrutement : cahier d'entrevues",
    "summary": "Grilles d'entrevue par rôle, entrevues multiples, notation en panel "
               "à l'aveugle et cahier consolidé, greffés sur le recrutement d'Odoo",
    "version": "18.0.1.2.1",
    "category": "Human Resources/Recruitment",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "description": """
Recrutement : cahier d'entrevues
================================

Ce que le recrutement d'Odoo donne, il le donne bien : le poste, la personne,
la candidature, les étapes, les motifs de refus. Là où il s'arrête, c'est sur
l'évaluation elle-même. `hr.applicant` porte un seul champ de notes libre, et
`hr_recruitment_survey` ajoute un questionnaire par poste, sans pondération,
sans tour, sans panel et sans comparaison entre candidats.

Ce module ajoute la partie manquante, et rien d'autre. Aucun champ du coeur
n'est recopié : le pipeline reste la vérité d'Odoo.

Ce qu'il ajoute
---------------

* **Des grilles par rôle et par tour** (`bf.interview.guide`), avec des critères
  pondérés, des critères éliminatoires, et des **ancrages** qui décrivent en
  comportements observables ce que vaut un 1, un 3 ou un 5.
* **Des séances** (`bf.interview`), plusieurs par candidature, chacune tenue par
  un panel.
* **Une notation par personne et par critère** (`bf.interview.rating`), pas une
  note collective négociée à voix haute.
* **Le dépôt à l'aveugle** : tant que je n'ai pas déposé ma notation, je ne vois
  pas celle des autres. C'est ce qui distingue un panel d'une chambre d'écho.
* **Deux rapports** : le cahier d'un candidat (toutes ses séances) et la grille
  comparative d'un poste (les candidats en colonnes).
* **Un catalogue de 32 modèles de grilles** prêts à servir, tirés des pratiques
  d'entrevue structurée : cinq grilles transversales (présélection, culture et
  valeurs, entrevue finale, travail à distance, intégrité), dix-sept familles de
  rôles et dix grilles sectorielles. Chaque modèle porte ses critères pondérés,
  la question posée mot pour mot, et ce que vaut chaque note en comportements
  observables.

Le catalogue et les grilles
---------------------------

Un modèle n'est pas une grille : on ne note personne dessus. Il sert à déposer
une grille en brouillon, dans la société courante, qu'on adapte à son poste
avant de la publier. Les deux restent séparés pour deux raisons : la liste des
grilles reste celle de l'organisation, et une mise à jour du module peut
corriger une question mal tournée du catalogue sans jamais toucher aux grilles
déjà tirées.

Ce qu'il ne fait pas
--------------------

* Il ne classe personne automatiquement. Le score aide une personne à trancher ;
  il n'écarte aucune candidature. Une décision de refus prise après une entrevue
  tenue exige un motif écrit et laisse le nom de qui a décidé.
* Il ne remplace pas `hr_recruitment`, il s'y greffe.
* Il ne porte aucune règle de conservation : c'est le rôle du pont
  `bf_recruitment_privacy`.

Le gel des grilles
------------------

Une grille publiée ne se modifie plus, ni elle ni ses critères. Pour la faire
évoluer, on en tire une **nouvelle version**, qui est un autre enregistrement.
Une séance tenue l'an dernier reste donc lisible exactement telle qu'elle a été
notée. Sans ça, un dossier d'entrevue ne prouve rien.
""",
    "depends": [
        "hr_recruitment",
        "mail",
    ],
    "data": [
        "security/recrutement_security.xml",
        "security/ir.model.access.csv",
        "data/interview_guide_templates_transversales.xml",
        "data/interview_guide_templates_metiers_affaires.xml",
        "data/interview_guide_templates_metiers_techniques.xml",
        "data/interview_guide_templates_secteurs.xml",
        "views/interview_guide_views.xml",
        "views/interview_guide_template_views.xml",
        "views/interview_views.xml",
        "views/hr_applicant_views.xml",
        "report/interview_reports.xml",
        "report/interview_book_templates.xml",
        "report/interview_comparison_templates.xml",
        "views/menuitems.xml",
    ],
}

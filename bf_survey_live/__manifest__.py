# -*- coding: utf-8 -*-
{
    "name": "Séance en direct : la trame et les écrans",
    "version": "18.0.1.5.0",
    "category": "Marketing/Surveys",
    "summary": "Trame de présentation, nuage de mots, équipes et podium pour une séance de sondage en direct",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["survey", "bf_live_base"],
    "installable": True,
    "auto_install": False,
    "data": [
        "security/ir.model.access.csv",
        "security/bf_survey_live_security.xml",
        "views/bf_live_team_views.xml",
        "views/survey_views.xml",
        "views/session_templates.xml",
        "views/participant_templates.xml",
    ],
    "assets": {
        "survey.survey_user_input_session_assets": [
            "bf_survey_live/static/src/js/word_cloud.js",
            "bf_survey_live/static/src/js/scoreboard.js",
            "bf_survey_live/static/src/js/session_manage.js",
            "bf_survey_live/static/src/scss/bf_survey_live.scss",
        ],
        # Le paquet du formulaire du répondant : c'est le téléphone. L'écran de
        # l'animateur charge les deux paquets, donc ce fichier-ci ne contient que
        # des classes qui ne paraissent que sur le téléphone.
        "survey.survey_assets": [
            "bf_survey_live/static/src/scss/bf_live_phone.scss",
        ],
    },
    "description": """
Séance en direct : les écrans qui manquaient
============================================

Odoo tient déjà la séance de style Kahoot : un code, un code QR projeté, les
gens rejoignent par `/s/<code>` avec un pseudonyme, la minuterie, le bonus de
rapidité, l'histogramme qui monte et le classement. Ce module ajoute les trois
écrans qu'il n'a pas.

Le nuage de mots
----------------
Une question ouverte en direct ne se projette pas en liste : à trente réponses,
personne ne lit plus rien. Le nuage compte les mots de la salle et les écrit
d'autant plus gros qu'ils reviennent souvent. Le compte se fait au serveur, en
écartant les mots outils du français et de l'anglais, et la question peut en
écarter d'autres : le mot de la question elle-même écrase tout le reste.

⚠️ Une question de type « texte long » n'affiche RIEN dans la séance du cœur :
ni histogramme, ni liste. Le nuage est le premier écran qu'elle ait.

Les équipes
-----------
Deux façons de faire des équipes, jamais les deux à la fois :

* **Au hasard.** Un bouton répartit les gens présents en deux à six équipes, à
  un membre près. Le tirage se fait au serveur, avec le hasard du système.
* **Par une question.** Une question à choix unique porte le drapeau « choisit
  l'équipe » : chaque réponse devient une équipe, et qui répond entre dedans.

Le tableau des équipes se classe par **moyenne par membre**, et affiche aussi le
total et le nombre de membres. Classer au total ferait gagner la plus grosse
équipe : avec des équipes nées d'une question, « quel service ? » donnerait la
victoire au plus gros service, ce qui n'est pas un jeu.

Le podium
---------
Le dernier écran d'une séance est le classement du cœur : des barres. Le podium
met les trois premiers sur des blocs à la marque de la maison, le reste en
dessous, et c'est l'écran qu'on photographie en fin d'atelier. Il marche pour les
personnes comme pour les équipes.

La trame de présentation
------------------------
Une séance n'est pas qu'une suite de questions : il faut ouvrir, présenter,
illustrer, conclure. Une **section** du sondage qui reçoit une mise en page
devient une **diapositive**, projetée à sa place dans la trame, et l'animateur
avance d'un écran à l'autre avec la même flèche.

Six mises en page : standard (la section du cœur, inchangée), titre, texte,
image, image et texte, citation. Les trois mêmes champs s'affichent toujours, le
titre, le sous-titre et le texte de l'onglet Description ; la mise en page ne
change que leur taille et leur place. Un compteur d'écrans, en bas à droite, dit
où on en est.

⚠️ Une diapositive EST une section : les questions qui la suivent lui
appartiennent, ce qui se voit au rapport des résultats. Et une trame a besoin
d'au moins une question : le cœur refuse d'ouvrir une séance sur un sondage qui
n'en a aucune.

Le téléphone du participant
---------------------------
Pendant une diapositive, le téléphone porte les mots de l'écran (le titre, le
sous-titre, le texte) et son image, en une seule colonne. La mise en page, elle,
ne traverse pas : un téléphone n'a qu'une colonne, et « l'image à gauche, le
texte à droite » n'y veut rien dire.

Le participant y lit aussi **son équipe**, sur une pastille à la couleur de
l'équipe, dès la réponse qui l'y fait entrer et sur tous les écrans suivants. Et
l'écran d'attente, celui que la salle regarde en arrivant, montre les équipes et
leurs effectifs, rafraîchis tout seuls : le tirage se fait au bureau pendant que
l'écran est déjà projeté.

⚠️ Une diapositive projetée n'offre AUCUN bouton au téléphone, et la touche
Entrée y est désarmée. C'est une correction, pas un choix d'ergonomie : une
soumission sur une section faisait tomber le téléphone du participant en 500.

Le code pour rejoindre, sur chaque écran
----------------------------------------
Le cœur projette le code QR sur l'écran d'accueil, et plus jamais ensuite : dès
la première question, qui arrive en retard n'a plus rien à scanner. Une carte en
bas à gauche porte donc le code QR et le code de la séance, assez gros pour une
salle, sur tous les écrans de la séance. Elle disparaît avec l'en-tête du cœur
quand la séance se ferme.

Les réponses que la séance n'affichait pas
------------------------------------------
Un texte long sans nuage de mots et une question numérique laissaient l'écran
VIDE pendant que la salle répondait : le cœur n'écrit les réponses que pour
`char_box`, `date` et `datetime`. Les deux s'affichent maintenant dans son propre
cadre, une réponse par étiquette.

⚠️ Une réponse longue est coupée à 70 caractères : le cadre du cœur écrit chaque
réponse sur une ligne, sans retour. Et une question numérique SAUTÉE ne compte
pas : son zéro aurait rempli le mur de « 0 ».

La marque de la maison
----------------------
Le logo de la société paraît sur l'écran d'attente et à côté du code de la
séance, et les blocs du podium prennent sa couleur (`primary_color`, celle de la
mise en page de ses documents). Sans logo, rien ne paraît ; sans couleur, le
podium garde le bleu mesuré du socle.

⚠️ Une couleur trop pâle ou trop sombre pour porter un texte est poussée jusqu'à
ce qu'une encre y atteigne 4,5:1 (`bf_live_base.brand_ramp`) : la teinte de la
maison reste la sienne, elle ne fait que s'assombrir.
""",
}

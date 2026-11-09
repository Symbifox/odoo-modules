# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import formatLang, is_html_empty

from . import nuage

# Les deux seuls types où un nuage de mots veut dire quelque chose : on y écrit
# ses propres mots. Un choix multiple a déjà son histogramme, une date n'a pas
# de mots, un nombre non plus.
TYPES_NUAGE = ("char_box", "text_box")

# Les deux types dont la séance du cœur n'affiche RIEN, ni histogramme ni liste :
# il n'écrit les réponses que pour `char_box`, `date` et `datetime`. Un texte long
# sans nuage de mots et un nombre laissaient donc l'écran vide pendant que la
# salle répondait.
TYPES_ECRITS = ("text_box", "numerical_box")

# Une réponse projetée se lit d'un coup d'œil. Le cadre du cœur écrit chaque
# réponse sur UNE ligne sans retour (`white-space: nowrap`) et calcule sa largeur
# sur le texte : une réponse de 400 caractères sortirait du mur sans rien dire.
# Mesuré sur une fenêtre de 1500 px, à la taille du cadre : 70 caractères
# tiennent. Le cœur tronque les siennes à 25, dans le navigateur.
LONGUEUR_PROJETEE = 70

# Les mises en page d'une diapositive. « standard » n'en est pas une : c'est la
# section du cœur, inchangée. Chaque mise en page lit les MÊMES trois champs
# (titre, sous-titre, texte) et ne change que leur taille et leur place : rien
# n'est jamais écarté en silence selon la mise en page choisie.
MISES_EN_PAGE = ("title", "text", "image", "split", "quote")

# Les deux mises en page qui n'existent pas sans image.
IMAGE_REQUISE = ("image", "split")


class SurveyQuestion(models.Model):
    _inherit = "survey.question"

    bf_live_screen = fields.Selection(
        [("standard", "Standard"), ("word_cloud", "Nuage de mots")],
        string="À l'écran, en direct", default="standard", required=True,
        help="Le nuage compte les mots des réponses et les écrit d'autant plus "
             "gros qu'ils reviennent souvent. Sur une question de type texte "
             "long, c'est le premier écran : la séance du cœur n'en affiche aucun.")
    bf_word_cloud_ignore = fields.Char(
        string="Mots à écarter du nuage",
        help="Séparés par des virgules. Les mots outils du français et de "
             "l'anglais sont déjà écartés ; ceux-ci s'ajoutent. Le mot de la "
             "question elle-même est souvent le premier à écarter : il écrase "
             "tout le reste.")
    bf_save_as_team = fields.Boolean(
        string="Cette réponse choisit l'équipe",
        help="Chaque réponse proposée devient une équipe, et qui répond entre "
             "dedans. Les équipes se figent à l'ouverture de la séance.")

    @api.constrains("bf_save_as_team", "question_type")
    def _check_bf_save_as_team(self):
        for question in self:
            if not question.bf_save_as_team:
                continue
            if question.question_type != "simple_choice":
                raise ValidationError(_(
                    "Seule une question à choix unique peut choisir l'équipe, et "
                    "« %(question)s » n'en est pas une.", question=question.title))
            autres = question.survey_id.question_ids.filtered(
                lambda autre: autre.bf_save_as_team and autre != question)
            if autres:
                raise ValidationError(_(
                    "« %(sondage)s » a déjà une question qui choisit l'équipe : "
                    "« %(question)s ». Une séance n'a qu'une façon de faire ses "
                    "équipes, sinon la dernière réponse écrase la première.",
                    sondage=question.survey_id.title, question=autres[0].title))

    def _bf_shows_word_cloud(self):
        """Vrai si cette question se projette en nuage de mots.

        ⚠️ Les mêmes gardes que la liste de réponses du cœur : une question qui
        sert à récolter un courriel ou un pseudonyme ne se projette pas, nuage ou
        pas. Un nuage de courriels serait une fuite affichée au mur.
        """
        self.ensure_one()
        return bool(
            self.bf_live_screen == "word_cloud"
            and self.question_type in TYPES_NUAGE
            and not self.save_as_email
            and not self.save_as_nickname
        )

    def _bf_shows_written_answers(self):
        """Vrai si les réponses de cette question s'écrivent au mur par nous.

        Les mêmes gardes que la liste du cœur et que le nuage : une question qui
        récolte un courriel ou un pseudonyme ne se projette pas. Et un texte long
        qui a son nuage garde son nuage : le nuage EST son écran.
        """
        self.ensure_one()
        return bool(
            self.question_type in TYPES_ECRITS
            and not self.save_as_email
            and not self.save_as_nickname
            and not self._bf_shows_word_cloud()
        )

    def _bf_written_answers(self, lignes, limite=100):
        """Les réponses écrites, telles qu'elles partent au mur.

        La même forme que la charge utile du cœur (`{id, value}`), pour que son
        propre cadre les affiche : la greffe n'ajoute pas un écran, elle remplit
        celui qui restait vide.

        🔴 Une ligne SAUTÉE sort de la liste, et c'est l'inverse du cœur, qui les
        laisse passer. Il peut se le permettre : une réponse texte sautée porte
        `False` et le navigateur l'écarte. Un NOMBRE sauté, lui, porte 0.0, et le
        mur aurait affiché « 0 » pour chaque personne qui a passé la question.

        🔴 Un ESSAI du sondage sort aussi, et le module le promet déjà ailleurs
        (`_bf_live_participations` et le podium l'écartent). Le cœur pose
        `is_session_answer=True` sur un essai ouvert pendant la séance : sans
        cette garde, un « Test » de l'animateur affichait sa réponse au mur,
        parmi celles de la salle.

        🔴 On ÉCARTE d'abord, on tranche ensuite. L'inverse — `lignes[:limite]`
        avant le tri — compte les lignes jetées dans le quota : 150 participants
        dont 60 ont passé la question donnaient 40 réponses au mur sur 90.
        """
        self.ensure_one()
        champ = "value_%s" % self.question_type
        reponses = []
        retenues = lignes.filtered(
            lambda ligne: not ligne.skipped and not ligne.user_input_id.test_entry)
        for ligne in retenues[:limite]:
            brut = ligne[champ]
            if self.question_type == "numerical_box":
                # Le nombre tel que la maison l'écrit : séparateur de milliers et
                # virgule décimale viennent de la langue, pas de « %s ». Et pas de
                # « 42,00 » pour une réponse de 42.
                texte = formatLang(
                    self.env, brut, digits=0 if float(brut).is_integer() else 2)
            else:
                texte = " ".join((brut or "").split())
                if len(texte) > LONGUEUR_PROJETEE:
                    texte = texte[:LONGUEUR_PROJETEE - 1].rstrip() + "…"
            if texte:
                reponses.append({"id": ligne.id, "value": texte})
        return reponses

    def _bf_word_cloud(self, lignes, limite=40):
        """Le nuage des réponses données, compté au serveur."""
        self.ensure_one()
        champ = "value_%s" % self.question_type
        textes = [ligne[champ] for ligne in lignes if ligne[champ]]
        return nuage.nuage(textes, limite=limite, a_ecarter=self.bf_word_cloud_ignore)

    # ------------------------------------------------------------
    # LA TRAME : UNE SECTION QUI DEVIENT UNE DIAPOSITIVE
    # ------------------------------------------------------------

    bf_slide_layout = fields.Selection(
        [("standard", "Standard"),
         ("title", "Titre"),
         ("text", "Texte"),
         ("image", "Image"),
         ("split", "Image et texte"),
         ("quote", "Citation")],
        string="Mise en page projetée", default="standard", required=True,
        help="Une section qui porte une mise en page devient une diapositive de "
             "la trame, projetée entre les questions. Les trois mêmes champs "
             "s'affichent toujours (le titre, le sous-titre, puis le texte de "
             "l'onglet Description) : la mise en page ne change que leur taille "
             "et leur place.")
    bf_slide_subtitle = fields.Char(
        string="Sous-titre", translate=True,
        help="Toujours sous le titre, quelle que soit la mise en page : le "
             "sous-titre d'un écran de titre, la signature d'une citation, "
             "l'intention d'une image.")
    bf_slide_image = fields.Image(
        string="Image de la diapositive", max_width=1920, max_height=1920,
        help="Montrée en entier, jamais recadrée. Pour une image qui remplit le "
             "fond derrière le texte, c'est « Image de fond » qu'il faut.")
    bf_slide_image_position = fields.Selection(
        [("left", "À gauche"), ("right", "À droite")],
        string="Côté de l'image", default="left")
    bf_slide_has_text = fields.Boolean(
        string="Porte un texte", compute="_compute_bf_slide_has_text")

    @api.depends("description")
    def _compute_bf_slide_has_text(self):
        """Le gabarit projeté a besoin de savoir si la description dit quelque chose.

        ⚠️ Et il ne peut pas le demander à `is_html_empty` : cet outil n'est dans
        le contexte de QWeb que parce que le cœur l'y pose, et il ne l'y pose pas
        quand la séance n'a pas de question en cours. Un champ calculé ne dépend
        de personne.
        """
        for element in self:
            element.bf_slide_has_text = not is_html_empty(element.description)

    @api.constrains("bf_slide_layout", "is_page")
    def _check_bf_slide_layout(self):
        for element in self:
            if element.bf_slide_layout != "standard" and not element.is_page:
                raise ValidationError(_(
                    "Seule une section devient une diapositive, et "
                    "« %(titre)s » est une question. Une question se projette "
                    "déjà d'elle-même.", titre=element.title))

    @api.constrains("bf_slide_layout", "bf_slide_image")
    def _check_bf_slide_image(self):
        for element in self:
            if element.bf_slide_layout in IMAGE_REQUISE and not element.bf_slide_image:
                raise ValidationError(_(
                    "La diapositive « %(titre)s » est une image et n'en porte "
                    "pas : à l'écran, ça fait un trou au mur.",
                    titre=element.title))

    def _bf_is_slide(self):
        """Vrai si cette section se projette comme une diapositive de la trame."""
        self.ensure_one()
        return bool(self.is_page and self.bf_slide_layout in MISES_EN_PAGE)

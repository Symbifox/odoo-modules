# -*- coding: utf-8 -*-
"""Le questionnaire du mandat, ouvert depuis la page de progression du client.

Ce que le module ajoute au mandat : QUEL questionnaire, à QUELLE étape le bouton
se montre, et LA réponse de ce mandat. Ce qu'il ne refait pas : la garde de la
page, qui reste celle du cœur (projet existant, actif, publié, jeton juste).

🔴 La réponse est rattachée au MANDAT, et non au couple (questionnaire, client).
Chez un donneur d'ordre, deux mandats portent le même `partner_id` : il paie
pour deux bénéficiaires, et leurs pages publiées le montrent.
Une clé par client ferait lire au second client la réponse du premier, et la lui
ferait écraser. Le mandat est la seule clé qui ne confond rien.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Le même groupe que la publication du portail dans le cœur : rattacher un
# questionnaire à une page DÉJÀ publiée expose ses questions au client, et fait
# déposer des réponses. Ce n'est pas du texte, c'est un accès.
from odoo.addons.bf_stepbystep_clients.models.project_project import GROUPE_PUBLICATION

# Une réponse dans cet état ne se rouvre pas : rouvrir effacerait ce que le
# client a déjà écrit.
ETAT_FINI = "done"

# Les trois états du questionnaire d'un mandat, calculés une fois par rendu.
FERME = "ferme"      # rien à ouvrir : pas de questionnaire, archivé, ou à compte
OUVERT = "ouvert"    # il reste à répondre
FINI = "fini"        # le client a terminé


class ProjectProject(models.Model):
    _inherit = "project.project"

    progression_survey_id = fields.Many2one(
        "survey.survey",
        string="Questionnaire on the client page",
        groups=GROUPE_PUBLICATION,
        help="Questionnaire the client opens from the progression page. The "
             "answer is tied to this mandate.",
    )
    # 🔴 Les deux champs suivants portent le groupe de publication, comme le
    # questionnaire : `readonly=True` ne protège rien d'un `write` par RPC, là
    # où une base élargit le droit d'écrire les projets.
    progression_survey_stage_id = fields.Many2one(
        "project.task.type",
        groups=GROUPE_PUBLICATION,
        string="Stage that asks the questionnaire",
        help="Stage of the progression where the button appears. The stage "
             "must belong to this project, carry a progression step number "
             "and be visible on the client portal. Leave empty to show the "
             "button once, under the heading of the page.",
    )
    # 🔴 `copy=False` : une copie de mandat qui héritait de la réponse naissait
    # « déjà répondue », et son client lisait les réponses de l'autre.
    progression_survey_answer_id = fields.Many2one(
        "survey.user_input",
        string="Client's answer",
        copy=False,
        readonly=True,
        groups=GROUPE_PUBLICATION,
        ondelete="set null",
        help="The answer of this mandate. Created the first time the client "
             "opens the questionnaire.",
    )

    @api.constrains("progression_survey_stage_id", "type_ids")
    def _progression_controler_l_etape_du_questionnaire(self):
        """Une étape d'un AUTRE projet ne montrerait jamais le bouton : la page
        ne rend que les étapes du mandat. ⚠️ `type_ids` est surveillé aussi :
        retirer l'étape du projet après coup laissait une étape étrangère, et le
        bouton disparaissait de partout en silence."""
        for projet in self.sudo():
            etape = projet.progression_survey_stage_id
            if etape and etape not in projet.type_ids:
                raise ValidationError(_(
                    "The stage that asks the questionnaire must be one of this "
                    "project's stages."))

    @api.constrains("progression_survey_id")
    def _progression_controler_le_questionnaire(self):
        """⚠️ Un questionnaire « Require Login » mène le client à un écran de
        connexion : il n'a pas de compte, c'est tout le propos de la page. Le
        mode d'accès, lui, peut rester « Invited people only » — le client passe
        toujours par un jeton de réponse."""
        for projet in self.sudo():
            sondage = projet.progression_survey_id
            if sondage and sondage.users_login_required:
                raise ValidationError(_(
                    "The questionnaire \"%(nom)s\" requires a login. The client "
                    "of a progression page has no account: untick \"Require "
                    "Login\" on the questionnaire, or choose another one.",
                    nom=sondage.display_name))

    # ------------------------------------------------------------------
    # La réponse de CE mandat
    # ------------------------------------------------------------------

    def _progression_survey_reponse(self):
        """La réponse du mandat, si elle sert encore son questionnaire.

        Changer le questionnaire du mandat détache l'ancienne réponse : elle
        répond à des questions qu'on ne pose plus.
        """
        self.ensure_one()
        projet = self.sudo()
        vide = self.env["survey.user_input"].sudo().browse()
        reponse = projet.progression_survey_answer_id
        if not reponse or not projet.progression_survey_id:
            return vide
        if reponse.survey_id != projet.progression_survey_id:
            return vide
        return reponse

    def _progression_survey_etat(self):
        """`ferme`, `ouvert` ou `fini`. Un seul appel par rendu.

        ⚠️ `active` et « à compte » sont relus ici, pas seulement à la saisie :
        archiver le questionnaire ou cocher « Require Login » après l'avoir
        rattaché laissait un bouton qui ne rendait qu'un refus.
        """
        self.ensure_one()
        sondage = self.sudo().progression_survey_id
        if not sondage or not sondage.active or sondage.users_login_required:
            return FERME
        if self._progression_survey_reponse().state == ETAT_FINI:
            return FINI
        return OUVERT

    def _progression_survey_adresse(self):
        """L'adresse du bouton. L'état se juge AVANT, par `_progression_survey_etat`."""
        self.ensure_one()
        projet = self.sudo()
        if not projet.progression_portal_token:
            return ""
        return "/progression/%s/%s/questionnaire" % (
            projet.id, projet.progression_portal_token)

    def _progression_survey_ouvrir(self, partenaire=None):
        """La réponse à servir au client, créée au premier geste.

        Rend un ensemble VIDE s'il n'y a rien à ouvrir — pas de questionnaire,
        questionnaire archivé ou à compte, réponse déjà finie. Une réponse finie
        ne ressort jamais d'ici : son jeton n'a plus à voyager dans une adresse.
        """
        self.ensure_one()
        projet = self.sudo()
        vide = self.env["survey.user_input"].sudo().browse()
        if self._progression_survey_etat() != OUVERT:
            return vide
        # Le cas ordinaire d'abord — « je reprends mon questionnaire » — et SANS
        # verrou : prendre une version de ligne à chaque clic était gratuit.
        reponse = self._progression_survey_reponse()
        if reponse:
            return reponse
        # ⚠️ Deux gestes simultanés sur un mandat qui n'a pas encore de réponse
        # cherchaient chacun sur son instantané et créaient chacun la leur. Une
        # ÉCRITURE réelle de la ligne du projet fait lever `SerializationFailure`
        # à la seconde transaction ; Odoo REJOUE alors la requête entière sur un
        # instantané neuf, qui voit la réponse de la première. Même technique que
        # le dépôt de pièces du cœur, pour la même raison : en REPEATABLE READ un
        # verrou de ligne ne suffit pas.
        #
        # 🔴 Tout tient au REJEU, donc à la REMONTÉE de l'erreur : la route doit
        # laisser sortir `OperationalError` avant son filet à tout faire, sinon
        # ce verrou ne corrige RIEN et le second geste rend 404 au client.
        self.env.cr.execute(
            "UPDATE project_project SET write_date = write_date WHERE id = %s",
            (projet.id,))
        reponse = projet.progression_survey_id.sudo()._create_answer(
            partner=partenaire or False,
            email=(partenaire.email or False) if partenaire else False,
            # La limite de tentatives vise un répondant anonyme, pas le client
            # nommé d'un mandat ; et une UserError ici rendrait un 500.
            check_attempts=False)
        projet.progression_survey_answer_id = reponse
        return reponse

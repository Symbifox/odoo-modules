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
from datetime import timedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

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

# Jusqu'où remonter, depuis le clic, pour reprendre une réponse.
# En pratique, l'invitation part souvent avant le mandat : des entreprises
# répondent sans mandat encore créé, parfois quelques jours avant le leur.
# Assez large pour ces cas, assez court
# pour qu'une réponse de l'an dernier ne fasse pas dire « reçu » à un mandat neuf.
FENETRE_REPRISE_JOURS = 90


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

    # Les réponses détachées de ce mandat. Non vide : plus de reprise d'office.
    progression_survey_skip_ids = fields.Many2many(
        "survey.user_input",
        "bf_stepbystep_survey_skip_rel", "project_id", "user_input_id",
        string="Answers detached from this mandate",
        copy=False,
        groups=GROUPE_PUBLICATION,
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

    def _progression_survey_a_reprendre(self, partenaire):
        """La réponse que l'entreprise nommée par la page a déjà, s'il y en a une.

        Le cas ordinaire : l'entreprise a reçu son questionnaire par un lien
        nominatif dans un courriel, puis clique le bouton de sa page. Sans
        reprise, elle se retrouvait avec deux questionnaires ouverts.

        Ce qui est repris : une réponse du MÊME questionnaire, faite au nom d'un
        contact de la MÊME entreprise (partenaire commercial), créée dans les
        FENETRE_REPRISE_JOURS qui précèdent le clic, qui n'est pas un essai,
        dont l'échéance n'est pas passée, qui n'a pas épuisé sa limite de temps
        et qu'aucun mandat ne porte (archivés compris). La plus avancée
        d'abord (finie, puis commencée, puis neuve), puis la plus récente.
        Rien, sur un mandat dont une personne a déjà détaché une réponse : elle
        a dit « pas celle-là », la suivante ne vaut pas mieux.

        🔴 La fenêtre compte depuis le CLIC. Sans borne, une réponse finie de
        l'an dernier faisait dire « reçu » au mandat de cette année ; ancrée
        sur la création du mandat, elle laissait revenir ce cas sur un mandat
        long. Pas « après le mandat » non plus : l'invitation part souvent
        AVANT lui.

        🔴 La garde du payeur : quand la page nomme le PAYEUR du mandat, c'est
        peut-être un donneur d'ordre dont le mandat ne nomme pas de
        bénéficiaire, et la page n'est pas lue par lui. On ne reprend rien s'il
        a un autre mandat qui le trahit comme donneur d'ordre : un mandat qui
        nomme un autre bénéficiaire, ou qui porte ce même questionnaire. Sans
        le pont du bénéficiaire, on ne peut pas le savoir : n'importe quel autre
        mandat suffit. Une page qui nomme un bénéficiaire n'est pas concernée :
        c'est lui qui la lit.
        """
        self.ensure_one()
        projet = self.sudo()
        Reponses = self.env["survey.user_input"].sudo()
        vide = Reponses.browse()
        sondage = projet.progression_survey_id
        if not partenaire or not sondage or projet.progression_survey_skip_ids:
            return vide
        societe = partenaire.commercial_partner_id
        Projets = self.env["project.project"].sudo().with_context(active_test=False)
        if societe == projet.partner_id.commercial_partner_id:
            domaine = [("partner_id.commercial_partner_id", "=", societe.id),
                       ("id", "!=", projet.id)]
            if "bf_beneficiary_id" in Projets._fields:
                domaine += ["|",
                            "&", ("bf_beneficiary_id", "!=", False),
                            ("bf_beneficiary_id.commercial_partner_id", "!=", societe.id),
                            ("progression_survey_id", "=", sondage.id)]
            if Projets.search_count(domaine):
                return vide
        maintenant = fields.Datetime.now()
        candidates = Reponses.search([
            ("survey_id", "=", sondage.id),
            ("test_entry", "=", False),
            ("partner_id.commercial_partner_id", "=", societe.id),
            ("create_date", ">=", maintenant - timedelta(days=FENETRE_REPRISE_JOURS)),
            "|", ("deadline", "=", False), ("deadline", ">", maintenant),
        ])
        # ⚠️ Une réponse commencée dont la limite de temps est épuisée se
        # FERME au premier affichage (`survey` appelle `_mark_done`) : la
        # reprendre la rendait « reçue » à moitié vide, avec un avis au
        # responsable pour un geste que le client n'a pas fait.
        candidates = candidates.filtered(
            lambda r: not (r.state == "in_progress" and r.survey_time_limit_reached))
        prises = Projets.search([
            ("progression_survey_answer_id", "in", candidates.ids),
        ]).progression_survey_answer_id
        candidates -= prises
        rang = {ETAT_FINI: 2, "in_progress": 1}
        return max(candidates, key=lambda r: (rang.get(r.state, 0), r.id), default=vide)

    def action_progression_survey_detacher(self):
        """Détacher la réponse du mandat : le prochain geste du client en crée
        une neuve. La réponse détachée n'est pas effacée, et CE mandat ne
        reprend plus aucune réponse d'office."""
        if not self.env.user.has_group(GROUPE_PUBLICATION):
            raise AccessError(_("Only a project administrator can detach the "
                                "client's answer."))
        for projet in self.sudo():
            reponse = projet.progression_survey_answer_id
            if not reponse:
                continue
            projet.write({
                "progression_survey_answer_id": False,
                "progression_survey_skip_ids": [Command.link(reponse.id)],
            })
            projet.message_post(
                body=_("Questionnaire: the answer of %(nom)s was detached from "
                       "this mandate. The client's next click starts a new one.",
                       nom=reponse.partner_id.display_name or reponse.email or "?"),
                subtype_xmlid="mail.mt_note")
        return True

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
        # La réponse que l'entreprise a DÉJÀ reçue (lien envoyé par courriel)
        # passe avant une réponse neuve : une entreprise, un questionnaire.
        reprise = projet._progression_survey_a_reprendre(partenaire)
        if reprise:
            # ⚠️ Verrou sur la RÉPONSE aussi : deux mandats de la même
            # entreprise, cliqués au même instant, la rattachaient chacun. Le
            # second lève `SerializationFailure`, est rejoué, et la voit prise.
            self.env.cr.execute(
                "UPDATE survey_user_input SET write_date = write_date WHERE id = %s",
                (reprise.id,))
            projet.progression_survey_answer_id = reprise
            # ⚠️ L'échéance de l'invitation tombe : la page devient le canal du
            # mandat. Sinon, une échéance passée APRÈS la reprise laissait un
            # bouton qui ne menait plus qu'à « survey expired ».
            if reprise.deadline:
                reprise.deadline = False
            # Une trace au dossier : le conseiller voit d'où vient la réponse,
            # et peut la détacher si elle n'est pas la bonne.
            projet.message_post(
                body=_("Questionnaire: the client's page took over the answer "
                       "already sent to %(nom)s.",
                       nom=reprise.partner_id.display_name),
                subtype_xmlid="mail.mt_note",
                author_id=self.env.ref("base.partner_root").id)
            # Une réponse déjà finie se rattache, mais ne ressort pas d'ici :
            # la route ramène le client à sa page, qui dit « reçu ».
            return reprise if reprise.state != ETAT_FINI else vide
        reponse = projet.progression_survey_id.sudo()._create_answer(
            partner=partenaire or False,
            email=(partenaire.email or False) if partenaire else False,
            # La limite de tentatives vise un répondant anonyme, pas le client
            # nommé d'un mandat ; et une UserError ici rendrait un 500.
            check_attempts=False)
        projet.progression_survey_answer_id = reponse
        return reponse

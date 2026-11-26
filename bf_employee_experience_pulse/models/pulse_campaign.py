"""La vague : ce qu'on demande, à qui, et pendant combien de temps."""

import logging
import random

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import get_lang

_logger = logging.getLogger(__name__)


class PulseCampaign(models.Model):
    _name = "bf.ex.pulse.campaign"
    _description = "Pulse wave"
    _inherit = ["mail.thread"]
    _order = "date_open desc, id desc"

    name = fields.Char(string="Wave", required=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", string="Company", required=True,
        default=lambda self: self.env.company,
    )
    state = fields.Selection(
        [("draft", "Draft"), ("open", "Open"), ("closed", "Closed")],
        string="Status", default="draft", required=True, tracking=True,
    )
    date_open = fields.Date(string="Opened on", tracking=True)
    date_close = fields.Date(string="Closed on", tracking=True)
    question_ids = fields.Many2many(
        "bf.ex.pulse.question", string="Questions asked",
        help="Three to five questions are enough. A long questionnaire "
             "gets answered once.",
    )
    segment_mode = fields.Selection(
        [("company", "The whole company"), ("department", "By department")],
        string="Breakdown", default="company", required=True,
        help="Breaking results down by department only makes sense above "
             "the threshold: below it, every segment stays silent and the "
             "table is empty.",
    )

    score_threshold = fields.Integer(
        string="Score threshold", default=5, required=True,
        help="Number of respondents below which a numeric score is not "
             "shown. Five: stricter than the three the market uses, and "
             "never to be lowered.",
    )
    text_threshold = fields.Integer(
        string="Comment threshold", default=5, required=True,
        help="Number of respondents below which written comments are not "
             "shown. Higher than the score threshold, because a verbatim "
             "comment gives its author away by their style.",
    )
    window_days = fields.Integer(
        string="Rolling window (days)", default=90, required=True,
        help="Scores aggregate the waves of the last N days. That is what "
             "lets a small team reach the threshold over the quarter "
             "instead of never over the week.",
    )

    invitation_ids = fields.One2many(
        "bf.ex.pulse.invitation", "campaign_id", string="Invitations",
    )
    invitation_count = fields.Integer(
        string="People invited", compute="_compute_counters",
    )
    response_count = fields.Integer(
        string="Responses received", compute="_compute_counters",
        help="The number of people who have responded. Never which ones, "
             "never when.",
    )
    # ⚠️ Échelle 0 à 100 : une vue ne lui met jamais `widget="percentage"`,
    # qui attend 0 à 1 et multiplie par cent.
    response_rate = fields.Float(
        string="Response rate (%)", compute="_compute_counters",
    )
    pending_flush_count = fields.Integer(
        string="Awaiting flush", compute="_compute_counters",
        help="Responses in the staging area, not yet counted in the "
             "scores. They are flushed as a batch, in a randomly drawn "
             "order.",
    )

    @api.constrains("score_threshold", "text_threshold")
    def _check_thresholds(self):
        for rec in self:
            if rec.score_threshold < 5:
                raise ValidationError(rec.env._(
                    "A score threshold does not go below five respondents. "
                    "Under that number, a department de-anonymises itself by "
                    "subtraction."
                ))
            if rec.text_threshold < rec.score_threshold:
                raise ValidationError(rec.env._(
                    "The comment threshold cannot be lower than a score's: a "
                    "verbatim comment says more than a number."
                ))

    @api.constrains("window_days")
    def _check_window(self):
        for rec in self:
            if rec.window_days < 1:
                raise ValidationError(
                    "La fenêtre glissante se compte en jours, au moins un."
                )

    @api.depends("invitation_ids", "invitation_ids.used")
    def _compute_counters(self):
        Staging = self.env["bf.ex.pulse.staging"].sudo()
        for rec in self:
            invitations = rec.invitation_ids
            rec.invitation_count = len(invitations)
            rec.response_count = len(invitations.filtered("used"))
            rec.response_rate = (
                100.0 * rec.response_count / rec.invitation_count
                if rec.invitation_count else 0.0
            )
            staged = Staging.search_read(
                [("campaign_id", "=", rec.id)], ["token"],
            )
            rec.pending_flush_count = len({row["token"] for row in staged})

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------

    def _target_employees(self):
        """Les personnes invitées : l'effectif actif de la société."""
        self.ensure_one()
        return self.env["hr.employee"].search([
            ("company_id", "=", self.company_id.id),
        ])

    def _segment_of(self, employee):
        self.ensure_one()
        if self.segment_mode == "department" and employee.department_id:
            return "dept-%s" % employee.department_id.id
        return "company-%s" % self.company_id.id

    def action_open(self):
        """Ouvre la vague et tire un jeton par personne."""
        Invitation = self.env["bf.ex.pulse.invitation"].sudo()
        for rec in self:
            if rec.state != "draft":
                raise UserError(
                    "Une vague ne s'ouvre qu'une fois. Celle-ci est déjà %s."
                    % dict(rec._fields["state"]._description_selection(rec.env))[rec.state].lower()
                )
            if not rec.question_ids:
                raise UserError(
                    "Une vague sans question ne mesure rien. Choisissez au "
                    "moins une question."
                )
            employees = rec._target_employees()
            if not employees:
                raise UserError(
                    "Aucune personne à inviter dans cette société."
                )
            Invitation.create([
                {
                    "campaign_id": rec.id,
                    "employee_id": employee.id,
                    "token": Invitation._new_token(),
                    "segment_key": rec._segment_of(employee),
                }
                for employee in employees
            ])
            rec.write({
                "state": "open",
                "date_open": fields.Date.context_today(rec),
            })
            rec.message_post(
                body="Vague ouverte. %s personnes invitées."
                     % len(employees)
            )
        return True

    def action_close(self):
        """Ferme la vague et verse ce qui reste au sas, seuil ou pas."""
        for rec in self:
            if rec.state != "open":
                raise UserError("Seule une vague ouverte se ferme.")
            rec.write({
                "state": "closed",
                "date_close": fields.Date.context_today(rec),
            })
            rec._flush_staged(force=True)
            rec.message_post(
                body="Vague fermée. %s réponses sur %s invitations."
                     % (rec.response_count, rec.invitation_count)
            )
            rec._rebuild_scores()
        return True

    # ------------------------------------------------------------------
    # Le versement, qui est tout l'intérêt du module
    # ------------------------------------------------------------------

    def _flush_staged(self, force=False):
        """Verse le sas dans le registre définitif, en ordre tiré au hasard.

        Tant que le lot compte moins de répondants que le seuil des
        commentaires, rien ne bouge : verser une réponse seule la daterait par
        son rang. `force` n'est employé qu'à la fermeture de la vague, où
        l'attente n'a plus d'objet.
        """
        Staging = self.env["bf.ex.pulse.staging"].sudo()
        Answer = self.env["bf.ex.pulse.answer"].sudo()
        versees = 0
        for rec in self:
            staged = Staging.search([("campaign_id", "=", rec.id)])
            if not staged:
                continue
            repondants = len(set(staged.mapped("token")))
            if not force and repondants < rec.text_threshold:
                continue
            vals = [
                {
                    "campaign_id": rec.id,
                    "company_id": rec.company_id.id,
                    "question_id": row.question_id.id,
                    "metric_id": row.question_id.metric_id.id,
                    "segment_key": row.segment_key,
                    "period": rec.date_open or fields.Date.context_today(rec),
                    "value_scale": row.value_scale,
                    "value_text": row.value_text,
                }
                for row in staged
            ]
            random.shuffle(vals)
            Answer.create(vals)
            staged.unlink()
            versees += len(vals)
        return versees

    @api.model
    def _cron_flush_and_score(self):
        """Passe périodique : verse ce qui peut l'être, recalcule les scores."""
        # Sans langue au contexte (travail planifié, passerelle de courriel), le suivi au fil et
        # les libellés stockés sortaient en anglais depuis la bascule de la source.
        if not self.env.lang:
            self = self.with_context(lang=get_lang(self.env).code)
        campaigns = self.search([("state", "in", ("open", "closed"))])
        versees = campaigns._flush_staged()
        campaigns._rebuild_scores()
        _logger.info(
            "Pulse : %s réponses versées, scores recalculés sur %s vagues.",
            versees, len(campaigns),
        )
        return True

    def _rebuild_scores(self):
        """Une seule reconstruction par FENÊTRE, pas une par vague.

        Deux vagues d'une même société ouvertes le même jour partagent leur
        fenêtre : les recalculer toutes les deux referait deux fois le même
        travail, et la seconde effacerait la première. On ne garde donc que la
        vague la plus récente de chaque fenêtre, qui sert de référence.
        """
        Score = self.env["bf.ex.pulse.score"].sudo()
        fenetres = {}
        for rec in self:
            if not rec.date_open:
                continue
            cle = (rec.company_id.id, rec.date_open, rec.window_days)
            precedente = fenetres.get(cle)
            if not precedente or rec.id > precedente.id:
                fenetres[cle] = rec
        for rec in fenetres.values():
            Score._rebuild_for_campaign(rec)
        return True

    # ------------------------------------------------------------------
    # Envoi
    # ------------------------------------------------------------------

    def action_send_invitations(self):
        """Envoie le lien personnel à qui n'a pas encore répondu.

        La relance vise `used = False`. Le module sait donc qui n'a pas
        répondu, et c'est assumé : sans ça, une relance sursollicite ceux qui
        ont déjà joué le jeu. Ce qu'il ne sait pas, c'est ce qu'ont répondu
        les autres.
        """
        template = self.env.ref(
            "bf_employee_experience_pulse.mail_template_pulse_invite",
            raise_if_not_found=False,
        )
        if not template:
            raise UserError("Le gabarit d'invitation est introuvable.")
        envoyes = 0
        for rec in self:
            if rec.state != "open":
                raise UserError(
                    "Une vague qui n'est pas ouverte n'a pas de lien à "
                    "envoyer."
                )
            for invitation in rec.invitation_ids.filtered(lambda i: not i.used):
                if not invitation.employee_id.work_email:
                    continue
                # Le gabarit n'a pas de langue : sans elle, l'invitation sortait dans la langue de qui
                # l'envoie (un appel sans langue la mettait en anglais pour tout le personnel). La
                # langue de la personne invitée, sinon celle de la société.
                emp = invitation.employee_id
                lang = get_lang(rec.env, emp.user_id.lang or emp.work_contact_id.lang
                                or rec.company_id.partner_id.lang).code
                template.with_context(lang=lang).send_mail(invitation.id, force_send=False)
                envoyes += 1
            rec.message_post(body="%s invitations mises à la file." % envoyes)
        return envoyes

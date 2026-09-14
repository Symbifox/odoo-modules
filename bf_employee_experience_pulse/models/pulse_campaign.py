"""La vague : ce qu'on demande, à qui, et pendant combien de temps."""

import logging
import random

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class PulseCampaign(models.Model):
    _name = "bf.ex.pulse.campaign"
    _description = "Vague de pulse"
    _inherit = ["mail.thread"]
    _order = "date_open desc, id desc"

    name = fields.Char(string="Vague", required=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", string="Société", required=True,
        default=lambda self: self.env.company,
    )
    state = fields.Selection(
        [("draft", "Brouillon"), ("open", "Ouverte"), ("closed", "Fermée")],
        string="État", default="draft", required=True, tracking=True,
    )
    date_open = fields.Date(string="Ouverte le", tracking=True)
    date_close = fields.Date(string="Fermée le", tracking=True)
    question_ids = fields.Many2many(
        "bf.ex.pulse.question", string="Questions posées",
        help="Trois à cinq questions suffisent. Un questionnaire long se "
             "répond une fois.",
    )
    segment_mode = fields.Selection(
        [("company", "Toute la société"), ("department", "Par département")],
        string="Découpage", default="company", required=True,
        help="Découper par département n'a de sens qu'au-dessus du seuil : "
             "sous le seuil, chaque segment reste muet et le tableau est vide.",
    )

    score_threshold = fields.Integer(
        string="Seuil d'un score", default=3, required=True,
        help="Nombre de répondants sous lequel un score chiffré ne s'affiche "
             "pas. Trois est la pratique du marché.",
    )
    text_threshold = fields.Integer(
        string="Seuil des commentaires", default=5, required=True,
        help="Nombre de répondants sous lequel les commentaires écrits ne "
             "s'affichent pas. Plus élevé que le seuil d'un score, parce "
             "qu'un verbatim se reconnaît à la plume.",
    )
    window_days = fields.Integer(
        string="Fenêtre glissante (jours)", default=90, required=True,
        help="Les scores agrègent les vagues des N derniers jours. C'est ce "
             "qui permet à une petite équipe d'atteindre le seuil sur le "
             "trimestre plutôt que jamais sur la semaine.",
    )

    invitation_ids = fields.One2many(
        "bf.ex.pulse.invitation", "campaign_id", string="Invitations",
    )
    invitation_count = fields.Integer(
        string="Invitées", compute="_compute_counters",
    )
    response_count = fields.Integer(
        string="Réponses reçues", compute="_compute_counters",
        help="Le nombre de personnes qui ont répondu. Jamais lesquelles, "
             "jamais quand.",
    )
    response_rate = fields.Float(
        string="Taux de réponse (%)", compute="_compute_counters",
    )
    pending_flush_count = fields.Integer(
        string="En attente de versement", compute="_compute_counters",
        help="Réponses au sas, qui ne sont pas encore comptées dans les "
             "scores. Elles se versent en lot, dans un ordre tiré au hasard.",
    )

    @api.constrains("score_threshold", "text_threshold")
    def _check_thresholds(self):
        for rec in self:
            if rec.score_threshold < 3:
                raise ValidationError(
                    "Le seuil d'un score ne descend pas sous trois "
                    "répondants. Sous ce nombre, un score se lit comme une "
                    "réponse individuelle."
                )
            if rec.text_threshold < rec.score_threshold:
                raise ValidationError(
                    "Le seuil des commentaires ne peut pas être plus bas que "
                    "celui d'un score : un verbatim en dit plus qu'un chiffre."
                )

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
                    % dict(rec._fields["state"].selection)[rec.state].lower()
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
                template.send_mail(invitation.id, force_send=False)
                envoyes += 1
            rec.message_post(body="%s invitations mises à la file." % envoyes)
        return envoyes

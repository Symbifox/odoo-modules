"""Satisfaction v2 : une question dans le courriel, une page pour répondre.

Le courriel porte cinq notes cliquables. Le clic ouvre une page où la note
est présélectionnée ; rien n'est enregistré avant l'envoi du formulaire. Les
filtres de liens des messageries (Safe Links et autres) ouvrent chaque lien
d'un courriel : une note enregistrée au clic serait une note inventée.
"""
import secrets
from datetime import timedelta

from odoo import _, api, fields, models

from .isolation import each_isolated

NEGATIVE_MAX = 2  # 1 ou 2 sur 5 : note négative, suivi humain

RATINGS = [
    ("1", "Très insatisfait"),
    ("2", "Insatisfait"),
    ("3", "Neutre"),
    ("4", "Satisfait"),
    ("5", "Très satisfait"),
]
CES_SCALE = [
    ("1", "Pas du tout d'accord"),
    ("2", "Plutôt en désaccord"),
    ("3", "Ni d'accord ni en désaccord"),
    ("4", "Plutôt d'accord"),
    ("5", "Tout à fait d'accord"),
]
# Raisons d'une note négative : courtes, et qui ne se chevauchent pas.
REASONS = [
    ("slow", "Le délai a été trop long"),
    ("unresolved", "Mon problème n'est pas réglé"),
    ("communication", "Les explications n'étaient pas claires"),
    ("effort", "J'ai dû en faire trop moi-même"),
    ("other", "Autre raison"),
]


# Libellés anglais des pages et du courriel (le module est écrit en français,
# qui occupe la case source : l'anglais ne peut pas passer par un .po).
RATINGS_EN = dict([("1", "Very dissatisfied"), ("2", "Dissatisfied"), ("3", "Neutral"),
                   ("4", "Satisfied"), ("5", "Very satisfied")])
CES_EN = dict([("1", "Strongly disagree"), ("2", "Somewhat disagree"),
               ("3", "Neither agree nor disagree"), ("4", "Somewhat agree"), ("5", "Strongly agree")])
REASONS_EN = dict([("slow", "It took too long"), ("unresolved", "My problem is not solved"),
                   ("communication", "The explanations were not clear"),
                   ("effort", "I had to do too much myself"), ("other", "Another reason")])


def localized(choices, english, en):
    return [(k, english.get(k, v)) for k, v in choices] if en else list(choices)


class HelpdeskTicketCsat(models.Model):
    _name = "helpdesk.ticket.csat"
    _inherit = ["bf.helpdesk.onchange.guard"]
    _description = "sondage de satisfaction"
    _order = "id desc"
    _rec_name = "ticket_id"

    ticket_id = fields.Many2one(
        "helpdesk.ticket", string="Billet", required=True,
        ondelete="cascade", index=True,
    )
    # Lus avec les droits de l'usager : en sudo (le défaut d'Odoo), un onchange
    # sur un billet quelconque en rendait le client et l'équipe.
    team_id = fields.Many2one(related="ticket_id.team_id", store=True, related_sudo=False)
    partner_id = fields.Many2one(related="ticket_id.partner_id", store=True, related_sudo=False)
    user_id = fields.Many2one(
        "res.users", string="Agent",
        help="L'agent assigné au moment de la fermeture.",
    )
    company_id = fields.Many2one(related="ticket_id.company_id", store=True, related_sudo=False)
    token = fields.Char(
        required=True, copy=False, index=True, readonly=True,
        default=lambda self: secrets.token_urlsafe(24),
        groups="helpdesk_mgmt.group_helpdesk_manager",
    )
    state = fields.Selection(
        [
            ("scheduled", "Prévu"),
            ("sent", "Envoyé"),
            ("answered", "Répondu"),
            ("expired", "Expiré"),
            ("cancelled", "Annulé"),
        ],
        default="scheduled", required=True, index=True, readonly=True,
    )
    cancel_reason = fields.Char(string="Motif d'annulation", readonly=True)
    scheduled_date = fields.Datetime(string="Envoi prévu", readonly=True)
    sent_date = fields.Datetime(string="Envoyé le", readonly=True)
    answered_date = fields.Datetime(string="Répondu le", readonly=True)
    rating = fields.Selection(RATINGS, string="Satisfaction", readonly=True)
    rating_value = fields.Integer(
        string="Note (1 à 5)", compute="_compute_rating_value", store=True,
        aggregator="avg",
    )
    negative = fields.Boolean(
        compute="_compute_rating_value", store=True, index=True,
        string="Note négative",
    )
    reason = fields.Selection(REASONS, string="Raison", readonly=True)
    ces = fields.Selection(
        CES_SCALE, string="Facilité (CES)", readonly=True,
        help="« L'équipe m'a rendu la tâche facile pour régler ma demande. »",
    )
    ces_value = fields.Integer(
        string="CES (1 à 5)", compute="_compute_rating_value", store=True,
        aggregator="avg",
    )
    comment = fields.Text(string="Commentaire", readonly=True)
    followup_activity_id = fields.Many2one(
        "mail.activity", string="Activité de suivi", readonly=True,
        ondelete="set null",
    )
    followup_state = fields.Selection(
        [("none", "Sans objet"), ("todo", "À faire"), ("done", "Fait")],
        string="Suivi", compute="_compute_followup_state",
    )

    @api.depends("rating", "ces")
    def _compute_rating_value(self):
        for csat in self:
            csat.rating_value = int(csat.rating) if csat.rating else 0
            csat.negative = bool(
                csat.rating and int(csat.rating) <= NEGATIVE_MAX)
            csat.ces_value = int(csat.ces) if csat.ces else 0

    @api.depends("negative", "followup_activity_id")
    def _compute_followup_state(self):
        for csat in self:
            if not csat.negative:
                csat.followup_state = "none"
            else:
                csat.followup_state = (
                    "todo" if csat.followup_activity_id else "done")

    access_url_base = fields.Char(
        compute="_compute_access_url_base",
        groups="helpdesk_mgmt.group_helpdesk_manager",
        help="Lien de réponse, jeton compris : le gabarit y ajoute /1 à /5.",
    )

    def _compute_access_url_base(self):
        for csat in self:
            base = csat.ticket_id.get_base_url()
            csat.access_url_base = f"{base}/helpdesk/csat/{csat.token}"

    def _is_open(self):
        """La réponse est encore acceptée : envoyé ou répondu, et dans le délai.

        Un sondage répondu garde son état (le rapport le compte), mais sa page
        se ferme à l'échéance comme les autres : sans cela, le lien restait
        ouvert pour toujours et chaque envoi rouvrait le suivi.
        """
        self.ensure_one()
        if self.state not in ("sent", "answered") or not self.sent_date:
            return False
        days = self.ticket_id.team_id.csat_expiry_days or 28
        return fields.Datetime.now() < self.sent_date + timedelta(days=days)

    def _send(self):
        """Mettre le courriel en file. Rend True s'il part."""
        self.ensure_one()
        template = self.env.ref(
            "bf_helpdesk.mail_template_csat_v2", raise_if_not_found=False)
        ticket = self.ticket_id
        email = ticket.partner_email or ticket.partner_id.email
        if not template or not email:
            self._cancel(_("aucune adresse") if not email else _("gabarit absent"))
            return False
        template.sudo().send_mail(
            self.id,
            force_send=False,
            email_layout_xmlid=self.env["helpdesk.ticket"].MAIL_LAYOUT,
            email_values={
                "headers": repr({
                    "Auto-Submitted": "auto-generated",
                    "X-Auto-Response-Suppress": "All",
                }),
                # Le mail.message du courriel est rattaché au billet : une
                # réponse par courriel au sondage revient sur le billet.
                "model": "helpdesk.ticket",
                "res_id": ticket.id,
            },
        )
        self.write({"state": "sent", "sent_date": fields.Datetime.now()})
        ticket.sudo().message_post(
            body=_("Sondage de satisfaction envoyé à %s.", email),
            message_type="notification",
            subtype_xmlid="mail.mt_note",
        )
        return True

    def _cancel(self, reason):
        self.write({"state": "cancelled", "cancel_reason": reason})

    def _record_answer(self, rating, reason=None, ces=None, comment=None):
        """Enregistrer (ou corriger) la réponse, puis le suivi si négatif."""
        self.ensure_one()
        rating = str(rating)
        if rating not in dict(RATINGS):
            raise ValueError("note invalide")
        negative = int(rating) <= NEGATIVE_MAX
        vals = {
            "rating": rating,
            # La raison ne vaut que pour une note négative.
            "reason": reason if negative and reason in dict(REASONS) else False,
            "ces": ces if (ces in dict(CES_SCALE)
                           and self.ticket_id.team_id.csat_ces_enabled) else False,
            "comment": (comment or "").strip()[:2000] or False,
            "state": "answered",
            "answered_date": fields.Datetime.now(),
        }
        first_answer = self.state != "answered"
        self.write(vals)
        self._post_answer_note(first_answer)
        if negative and not self.followup_activity_id:
            self._open_followup()
        elif not negative and self.followup_activity_id:
            # Le client a corrigé sa note vers le haut : le suivi tombe.
            self.followup_activity_id.unlink()

    def _post_answer_note(self, first_answer):
        self.ensure_one()
        label = dict(RATINGS)[self.rating]
        parts = [_("Satisfaction : %(n)s/5 (%(l)s).", n=self.rating, l=label)]
        if self.reason:
            parts.append(_("Raison : %s.", dict(REASONS)[self.reason]))
        if self.ces:
            parts.append(_("Facilité : %s/5.", self.ces))
        if self.comment:
            parts.append(_("Commentaire : « %s »", self.comment))
        if not first_answer:
            parts.insert(0, _("Réponse corrigée par le client."))
        self.ticket_id.sudo().message_post(
            body=" ".join(parts),
            message_type="notification",
            subtype_xmlid="mail.mt_note",
        )

    def _open_followup(self):
        self.ensure_one()
        ticket = self.ticket_id.sudo()
        team = ticket.team_id
        user = (
            team.csat_followup_user_id
            or self.user_id
            or ticket.user_id
            or team.user_ids[:1]
            or self.env.ref("base.user_admin")
        )
        activity = ticket.activity_schedule(
            act_type_xmlid="mail.mail_activity_data_todo",
            summary=_("Suivi : satisfaction négative"),
            note=_("Le client a donné %s/5. Communiquer avec lui pour "
                   "comprendre et corriger, puis marquer l'activité comme "
                   "faite.", self.rating),
            user_id=user.id,
        )
        self.followup_activity_id = activity

    # ------------------------------------------------------------------
    @api.model
    def _cron_csat(self):
        """Envoyer les sondages dus et expirer ceux qui n'ont pas eu réponse."""
        now = fields.Datetime.now()
        due = self.search([
            ("state", "=", "scheduled"), ("scheduled_date", "<=", now),
        ])
        def _one(csat):
            ticket = csat.ticket_id
            if not ticket.stage_id.closed:
                csat._cancel(_("billet rouvert avant l'envoi"))
                return
            if ticket.reminder_auto_closed:
                csat._cancel(_("fermé faute de réponse"))
                return
            # Le point de passage unique : bf_cx_helpdesk y branche ses
            # garde-fous de sollicitation (cooldown, ne pas contacter).
            if not ticket._send_csat_invite() and csat.state == "scheduled":
                csat._cancel(_("garde-fou de sollicitation"))
        each_isolated(due, _one, "bf_helpdesk sondages")
        for team in self.env["helpdesk.ticket.team"].search([]):
            limit = now - timedelta(days=team.csat_expiry_days or 28)
            self.search([
                ("team_id", "=", team.id), ("state", "=", "sent"),
                ("sent_date", "<", limit),
            ]).write({"state": "expired"})

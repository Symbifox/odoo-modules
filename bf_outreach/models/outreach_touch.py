# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import _, api, fields, models

# Résultats qui valent une réponse de la cible : ils arrêtent la cadence
# lorsque la campagne est réglée sur « Arrêter à la première réponse ».
REPLY_OUTCOMES = ("reached", "replied", "interested", "not_interested", "callback")


class BfOutreachTouch(models.Model):
    _name = "bf.outreach.touch"
    _description = "Outreach interaction"
    _order = "date desc, id desc"

    target_id = fields.Many2one(
        "bf.outreach.target",
        string="Target",
        required=True,
        ondelete="cascade",
        index=True,
    )
    campaign_id = fields.Many2one(
        related="target_id.campaign_id", store=True, index=True, string="Campaign"
    )
    company_id = fields.Many2one(related="target_id.company_id", store=True)
    partner_id = fields.Many2one(related="target_id.partner_id", store=True)
    user_id = fields.Many2one(
        "res.users", string="By", default=lambda self: self.env.user, required=True
    )
    date = fields.Datetime(
        string="Date", default=fields.Datetime.now, required=True, index=True
    )
    kind = fields.Selection(
        [
            ("call", "Call"),
            ("email", "Email"),
            ("letter", "Letter"),
            ("sms", "Text message"),
            ("meeting", "Meeting"),
            ("linkedin", "LinkedIn"),
            ("other", "Other"),
        ],
        string="Type",
        default="call",
        required=True,
        index=True,
    )
    direction = fields.Selection(
        [("out", "Outgoing"), ("in", "Incoming")],
        string="Direction",
        default="out",
        required=True,
    )
    outcome = fields.Selection(
        [
            ("no_answer", "No answer"),
            ("voicemail", "Voicemail"),
            ("reached", "Person reached"),
            ("replied", "Reply received"),
            ("interested", "Interest shown"),
            ("not_interested", "Declined"),
            ("callback", "Callback requested"),
            ("bounced", "Invalid address"),
            ("sent", "Sent"),
        ],
        string="Outcome",
        default="no_answer",
        required=True,
        index=True,
    )
    duration = fields.Float(
        string="Duration (min)", help="Length of the call or the meeting, in "
                                   "minutes."
    )
    summary = fields.Char(string="Summary")
    note = fields.Html(string="Details")
    is_reply = fields.Boolean(
        string="Reply from the target", compute="_compute_is_reply", store=True
    )
    mail_message_id = fields.Many2one(
        "mail.message",
        string="Source message",
        ondelete="set null",
        copy=False,
        index="btree_not_null",
        help="Message in the thread this interaction came from, when it "
             "was inferred rather than keyed in.",
    )

    @api.depends("direction", "outcome")
    def _compute_is_reply(self):
        for touch in self:
            touch.is_reply = touch.direction == "in" or touch.outcome in REPLY_OUTCOMES

    @api.depends("kind", "date", "outcome")
    @api.depends_context("lang")
    def _compute_display_name(self):
        for touch in self:
            date_str = fields.Datetime.to_string(touch.date) if touch.date else ""
            touch.display_name = "%s — %s — %s" % (
                touch._kind_label(),
                date_str,
                touch._label_of("outcome"),
            )

    def _label_of(self, field_name):
        """Libellé traduit de la valeur courante d'un champ sélection."""
        self.ensure_one()
        labels = dict(self._fields[field_name]._description_selection(self.env))
        return labels.get(self[field_name], "")

    def _kind_label(self):
        return self._label_of("kind")

    # ------------------------------------------------------------------
    # Surcharges
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        touches = super().create(vals_list)
        touches._post_on_target()
        touches._advance_stage()
        return touches

    def _post_on_target(self):
        """Trace l'interaction dans la discussion de la cible."""
        for touch in self:
            body = Markup(
                _("<p><b>%(kind)s</b> (%(direction)s): %(outcome)s</p>")
            ) % {
                "kind": touch._kind_label(),
                "direction": touch._label_of("direction"),
                "outcome": touch._label_of("outcome"),
            }
            if touch.summary:
                body += Markup("<p>%s</p>") % touch.summary
            if touch.note:
                body += Markup(touch.note)
            touch.target_id.with_context(
                bf_outreach_touch_log=True
            ).message_post(body=body)

    def _advance_stage(self):
        """Sort automatiquement du « À contacter » dès le premier contact."""
        Stage = self.env["bf.outreach.stage"]
        for touch in self:
            target = touch.target_id
            if target.stage_type != "todo":
                continue
            next_stage = Stage.search(
                [
                    ("stage_type", "=", "active"),
                    "|",
                    ("campaign_ids", "=", False),
                    ("campaign_ids", "in", target.campaign_id.id),
                ],
                order="sequence, id",
                limit=1,
            )
            if next_stage:
                target.stage_id = next_stage

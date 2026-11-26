"""Complaint registry.

Thin, self-sufficient complaint log: severity, acknowledgement deadline,
root cause and corrective action (closed loop). When helpdesk_mgmt is
installed the bf_cx_helpdesk bridge adds the ticket link - the core
deliberately has no reference to helpdesk models so it loads on any tenant.
"""
import logging
import secrets
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import get_lang

from .langue import langue_de

_logger = logging.getLogger(__name__)

# Confirmation codes shown to complainants: unambiguous alphabet (no 0/O,
# 1/I/L, 2/Z, 5/S, 8/B, Q). The sequential PLT number stays INTERNAL: a
# sequence leaks the complaint volume and biases the complainant.
CONFIRMATION_ALPHABET = "ACDEFGHJKMNPRTUVWXY34679"


class BfCxComplaint(models.Model):
    _name = "bf.cx.complaint"
    _description = "Customer complaint"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_received desc, id desc"
    _rec_name = "number"

    number = fields.Char(
        string="Number (internal)",
        readonly=True,
        copy=False,
        default="/",
        help="Internal sequence. NEVER give it to the complainant: it "
             "reveals the volume of complaints received. Use the "
             "confirmation code.",
    )
    confirmation_code = fields.Char(
        string="Confirmation code",
        readonly=True,
        copy=False,
        index=True,
        help="Non-sequential reference given to the complainant in the "
             "acknowledgement.",
    )
    name = fields.Char(string="Subject", required=True, tracking=True)
    partner_id = fields.Many2one(
        "res.partner", string="Complainant", index=True, tracking=True
    )
    contact_name = fields.Char(
        string="Contact name",
        help="If the complainant has no contact record.",
    )
    contact_email = fields.Char(string="Contact email")
    severity = fields.Selection(
        [
            ("low", "Low"),
            ("medium", "Medium"),
            ("high", "High"),
            ("critical", "Critical"),
        ],
        string="Severity",
        default="medium",
        required=True,
        tracking=True,
    )
    state = fields.Selection(
        [
            ("received", "Received"),
            ("ack", "Acknowledged"),
            ("analysis", "Under analysis"),
            ("resolved", "Resolved"),
            ("closed", "Closed"),
        ],
        string="Status",
        default="received",
        required=True,
        tracking=True,
    )
    date_received = fields.Datetime(
        string="Received on",
        required=True,
        default=fields.Datetime.now,
    )
    ack_deadline = fields.Datetime(
        string="Acknowledgement deadline",
        compute="_compute_ack_deadline",
        store=True,
        help="Deadline to acknowledge receipt (delay set in the "
             "settings). The scheduled job alerts the owner as the "
             "deadline approaches.",
    )
    date_acknowledged = fields.Datetime(
        string="Acknowledgement sent on", readonly=True, copy=False
    )
    ack_delay_hours = fields.Float(
        string="Acknowledgement delay (h)",
        compute="_compute_delays",
        store=True,
        help="Hours between receipt and acknowledgement - ISO 10002 indicator.",
    )
    resolution_delay_days = fields.Float(
        string="Resolution time (days)",
        compute="_compute_delays",
        store=True,
    )
    date_resolved = fields.Datetime(string="Resolved on", readonly=True, copy=False)
    complainant_satisfied = fields.Boolean(
        string="Complainant satisfied with the handling",
        tracking=True,
        help="ISO 10002: check the complainant's satisfaction with the "
             "HANDLING of the complaint after resolution (not with the "
             "product).",
    )
    description = fields.Html(string="Description", required=True)
    resolution = fields.Html(string="Resolution")
    theme_ids = fields.Many2many(
        "bf.cx.theme",
        string="Themes",
        help="Recurring causes: the same reference list as feedback, for "
             "the outer-loop aggregation.",
    )
    root_cause = fields.Text(string="Root cause")
    corrective_action = fields.Text(
        string="Corrective action",
        help="What is put in place so that the situation does not happen "
             "again.",
    )
    project_id = fields.Many2one("project.project", string="Project / "
                                                           "engagement")
    user_id = fields.Many2one(
        "res.users",
        string="Owner",
        default=lambda self: self.env.user,
        tracking=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )
    feedback_id = fields.Many2one(
        "bf.cx.feedback",
        string="Source feedback",
        ondelete="set null",
    )

    # ── Compute ──────────────────────────────────────────────────────────────

    @api.depends("date_received")
    def _compute_ack_deadline(self):
        raw = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("bf_cx.complaint_ack_days", "2")
        )
        try:
            days = int(raw)
        except (TypeError, ValueError):
            days = 2
        for rec in self:
            rec.ack_deadline = (
                rec.date_received + timedelta(days=days)
                if rec.date_received
                else False
            )

    @api.depends("date_received", "date_acknowledged", "date_resolved")
    def _compute_delays(self):
        for rec in self:
            rec.ack_delay_hours = (
                (rec.date_acknowledged - rec.date_received).total_seconds()
                / 3600.0
                if rec.date_acknowledged and rec.date_received
                else 0.0
            )
            rec.resolution_delay_days = (
                (rec.date_resolved - rec.date_received).total_seconds()
                / 86400.0
                if rec.date_resolved and rec.date_received
                else 0.0
            )

    # ── ORM ──────────────────────────────────────────────────────────────────

    def _generate_confirmation_code(self):
        """5-char unambiguous code, collision-checked (24^5 ≈ 8M combos)."""
        for _attempt in range(20):
            code = "".join(
                secrets.choice(CONFIRMATION_ALPHABET) for _i in range(5)
            )
            if not self.sudo().search_count(
                [("confirmation_code", "=", code)]
            ):
                return code
        return secrets.token_hex(4).upper()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("number") or vals["number"] == "/":
                vals["number"] = (
                    self.env["ir.sequence"].next_by_code("bf.cx.complaint")
                    or "/"
                )
            if not vals.get("confirmation_code"):
                vals["confirmation_code"] = self._generate_confirmation_code()
        return super().create(vals_list)

    @api.depends("number", "name")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = (
                "%s - %s" % (rec.number, rec.name)
                if rec.number and rec.number != "/"
                else (rec.name or "")
            )

    # ── Actions ──────────────────────────────────────────────────────────────

    def action_acknowledge(self):
        """Real acknowledgement: email the complainant when reachable."""
        template = self.env.ref(
            "bf_cx.mail_template_complaint_ack", raise_if_not_found=False
        )
        for rec in self:
            email = rec.partner_id.email or rec.contact_email
            if template and email:
                # A complainant without a language gets the company's (the
                # template's `lang` renders empty; since 18.0.1.13.2 the source,
                # read without a language, is English).
                template.with_context(
                    lang=langue_de(rec.env, societe=rec.company_id)
                ).send_mail(rec.id, force_send=False)
                body = _("Acknowledgement sent to %s.") % email
            else:
                body = _(
                    "Acknowledgement recorded by %s (no email address - "
                    "passed on outside the system)."
                ) % self.env.user.display_name
            rec.write(
                {
                    "state": "ack",
                    "date_acknowledged": fields.Datetime.now(),
                }
            )
            rec.message_post(body=body)
        return True

    def action_start_analysis(self):
        self.write({"state": "analysis"})
        return True

    def action_resolve(self):
        for rec in self:
            if not rec.resolution:
                raise UserError(
                    _("Record the resolution before closing the analysis "
                      "(\"Resolution\" field).")
                )
            rec.write(
                {"state": "resolved", "date_resolved": fields.Datetime.now()}
            )
        return True

    def action_close(self):
        """Close, then verify the complainant's satisfaction (ISO 10002)."""
        self.write({"state": "closed"})
        for rec in self:
            if rec.complainant_satisfied:
                continue
            if not (rec.partner_id or rec.contact_email or rec.contact_name):
                continue
            user = rec.user_id or self.env.user
            # Read by the owner, who is not always the one closing.
            env_l = rec.with_context(
                lang=langue_de(rec.env, user, societe=rec.company_id)).env
            rec.activity_schedule(
                "mail.mail_activity_data_todo",
                date_deadline=fields.Date.context_today(rec)
                + timedelta(days=7),
                user_id=user.id,
                summary=env_l._("Check the complainant's satisfaction (%s)")
                % (rec.partner_id.display_name or rec.contact_name or ""),
                note=env_l._(
                    "ISO 10002: check that the complainant is satisfied "
                    "with the HANDLING of their complaint, then tick "
                    "\"Complainant satisfied with the handling\" on the "
                    "record."
                ),
            )
        return True

    def action_reopen(self):
        self.write({"state": "analysis", "date_resolved": False})
        return True

    # ── Cron ─────────────────────────────────────────────────────────────────

    @api.model
    def _cron_ack_overdue(self):
        """Alert the owner when an acknowledgement deadline is (nearly) blown."""
        # Since 18.0.1.13.2 the source is English: a scheduled job has no language
        # in its context, and what it writes (notes, tracked states, selection
        # labels) would read the English source. Pin the job's language.
        if not self.env.lang:
            self = self.with_context(lang=get_lang(self.env).code)
        soon = fields.Datetime.now() + timedelta(hours=24)
        overdue = self.search(
            [("state", "=", "received"), ("ack_deadline", "<", soon)]
        )
        langs = [code for code, _name in self.env["res.lang"].get_installed()]
        for rec in overdue:
            user = rec.user_id or self.env.user
            env_l = rec.with_context(
                lang=langue_de(rec.env, user, societe=rec.company_id)).env
            summary = env_l._("Acknowledgement overdue - %s") % rec.number
            # The same alert may already exist in another language (written
            # before 18.0.1.13.2 in French, or for a previous owner): one per
            # complaint, whatever its language.
            summaries = {summary} | {
                self.with_context(lang=code).env._(
                    "Acknowledgement overdue - %s") % rec.number
                for code in langs
            } | {
                # Wording delivered before 18.0.1.13.2, still in open activities of
                # databases that have no French catalogue (English only).
                "Accusé de réception en retard - %s" % rec.number,
            }
            existing = self.env["mail.activity"].search_count(
                [
                    ("res_model", "=", self._name),
                    ("res_id", "=", rec.id),
                    ("summary", "in", list(summaries)),
                ]
            )
            if existing:
                continue
            rec.activity_schedule(
                "mail.mail_activity_data_todo",
                user_id=user.id,
                summary=summary,
                note=env_l._(
                    "The acknowledgement deadline of this complaint has "
                    "been reached or passed. Use \"Acknowledge\" "
                    "(automatic email if the complainant has an address)."
                ),
            )
        return True

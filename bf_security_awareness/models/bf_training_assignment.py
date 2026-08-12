import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class BfTrainingAssignment(models.Model):
    """Bridges a person to a website_slides course (slide.channel) with our own
    status mirror, so remediation can be tracked and chased independently of the
    LMS internals."""

    _name = "bf.training.assignment"
    _description = "Assignation de formation en cybersécurité"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    partner_id = fields.Many2one(
        "res.partner", string="Personne", required=True,
        ondelete="cascade", index=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", string="Société",
        default=lambda self: self.env.company)
    profile_id = fields.Many2one(
        "bf.security.profile", string="Profil de risque",
        ondelete="set null", index=True)
    channel_id = fields.Many2one(
        "slide.channel", string="Cours", required=True, tracking=True)
    source_result_id = fields.Many2one(
        "bf.phishing.result", string="Résultat déclencheur",
        ondelete="set null",
        help="Le résultat d'hameçonnage qui a déclenché l'assignation.")

    assigned_date = fields.Datetime(
        string="Assignée le", default=fields.Datetime.now)
    due_date = fields.Date(string="Échéance")
    assigned_by = fields.Many2one(
        "res.users", string="Assignée par", default=lambda self: self.env.user)

    channel_partner_id = fields.Many2one(
        "slide.channel.partner", string="Inscription",
        compute="_compute_channel_partner_id", store=True)
    member_status = fields.Selection(
        related="channel_partner_id.member_status", string="Statut LMS",
        store=True)
    completion = fields.Integer(
        related="channel_partner_id.completion", string="Avancement (%)",
        store=True)
    completed = fields.Boolean(
        compute="_compute_completed", string="Complétée", store=True)
    completed_date = fields.Datetime(string="Complétée le")

    state = fields.Selection(
        [
            ("to_invite", "À inscrire"),
            ("invited", "Invitée"),
            ("in_progress", "En cours"),
            ("completed", "Complétée"),
            ("overdue", "En retard"),
            ("error", "Échec d'inscription"),
        ],
        string="État", default="to_invite", required=True, tracking=True)
    reminder_count = fields.Integer(string="Rappels envoyés", default=0)

    _sql_constraints = [
        ("partner_channel_uniq", "unique(partner_id, channel_id)",
         "Cette personne est déjà inscrite à ce cours."),
    ]

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #
    @api.model_create_multi
    def create(self, vals_list):
        Profile = self.env["bf.security.profile"]
        IConf = self.env["ir.config_parameter"].sudo()
        due_days = int(float(IConf.get_param(
            "bf_security_awareness.training_due_days", "14") or 14))
        for vals in vals_list:
            if vals.get("partner_id") and not vals.get("profile_id"):
                vals["profile_id"] = Profile._get_or_create(
                    vals["partner_id"]).id
            if not vals.get("due_date"):
                vals["due_date"] = fields.Date.add(
                    fields.Date.today(), days=due_days)
        return super().create(vals_list)

    # ------------------------------------------------------------------ #
    # Computes
    # ------------------------------------------------------------------ #
    @api.depends("partner_id", "channel_id")
    def _compute_channel_partner_id(self):
        SCP = self.env["slide.channel.partner"].sudo()
        for rec in self:
            rec.channel_partner_id = SCP.with_context(
                active_test=False).search([
                    ("channel_id", "=", rec.channel_id.id),
                    ("partner_id", "=", rec.partner_id.id),
                ], limit=1)

    @api.depends("member_status")
    def _compute_completed(self):
        for rec in self:
            rec.completed = rec.member_status == "completed"

    # ------------------------------------------------------------------ #
    # Enrollment
    # ------------------------------------------------------------------ #
    def _auto_grant_portal(self):
        return self.env["ir.config_parameter"].sudo().get_param(
            "bf_security_awareness.auto_grant_portal", "1") in ("1", "True", "true")

    def _ensure_portal_user(self):
        """Course completion is only recorded for a logged-in user. If the
        person has no user and auto-grant is enabled, create a portal user so
        the slide invite/signup flow can give them access."""
        self.ensure_one()
        partner = self.partner_id
        if partner.user_ids:
            return partner.user_ids[0]
        if not (self._auto_grant_portal() and partner.email):
            return False
        portal_group = self.env.ref("base.group_portal", raise_if_not_found=False)
        if not portal_group:
            return False
        try:
            return self.env["res.users"].sudo().with_context(
                no_reset_password=True).create({
                    "name": partner.name or partner.email,
                    "login": partner.email,
                    "email": partner.email,
                    "partner_id": partner.id,
                    "groups_id": [(6, 0, [portal_group.id])],
                })
        except Exception as exc:  # noqa: BLE001 - login clash etc.
            _logger.warning(
                "bf_security_awareness: could not create portal user for "
                "partner %s: %s", partner.id, exc)
            return False

    def action_enroll(self):
        for rec in self:
            user = rec._ensure_portal_user()
            member_status = "joined" if user else "invited"
            try:
                rec.channel_id.sudo()._action_add_members(
                    rec.partner_id, member_status=member_status)
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "bf_security_awareness: enrollment failed for assignment "
                    "%s: %s", rec.id, exc)
                rec.state = "error"
                rec.message_post(body=_(
                    "L'inscription au cours a échoué : %s") % exc)
                continue
            rec._compute_channel_partner_id()
            rec.state = "invited" if member_status == "invited" else "in_progress"
            rec.message_post(body=_(
                "Inscrite au cours « %s » (%s).") % (
                    rec.channel_id.name,
                    dict(rec._fields["state"].selection).get(rec.state)))
        return True

    def action_send_invite(self):
        template = self.env.ref(
            "bf_security_awareness.mail_template_training_invite",
            raise_if_not_found=False)
        for rec in self:
            rec.reminder_count += 1
            if template and rec.partner_id.email:
                try:
                    template.send_mail(rec.id, force_send=False)
                except Exception as exc:  # noqa: BLE001
                    _logger.warning(
                        "bf_security_awareness: invite mail failed for %s: %s",
                        rec.id, exc)
            rec.activity_schedule(
                "mail.mail_activity_data_todo",
                summary=_("Relancer %s sur la formation cybersécurité")
                % rec.partner_id.name,
                user_id=rec.assigned_by.id or self.env.uid,
            )
        return True

    def action_open_course(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": "/slides/%s" % self.channel_id.id,
            "target": "new",
        }

    # ------------------------------------------------------------------ #
    # Cron
    # ------------------------------------------------------------------ #
    @api.model
    def _cron_training_reminders(self):
        today = fields.Date.today()
        # Mirror LMS completion into our state.
        open_assignments = self.search([("state", "!=", "completed")])
        for rec in open_assignments:
            rec._compute_channel_partner_id()
            if rec.member_status == "completed":
                rec.state = "completed"
                if not rec.completed_date:
                    rec.completed_date = fields.Datetime.now()
            elif rec.member_status in ("joined", "ongoing"):
                if rec.state not in ("in_progress",):
                    rec.state = "in_progress"
        # Flag + nudge overdue ones.
        overdue = self.search([
            ("state", "not in", ("completed",)),
            ("due_date", "!=", False),
            ("due_date", "<", today),
        ])
        for rec in overdue:
            if rec.state != "overdue":
                rec.state = "overdue"
            rec.activity_schedule(
                "mail.mail_activity_data_todo",
                summary=_("Formation cybersécurité en retard : %s")
                % rec.partner_id.name,
                user_id=rec.assigned_by.id or self.env.uid,
            )

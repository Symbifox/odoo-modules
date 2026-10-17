# -*- coding: utf-8 -*-
"""Les répondants : qui a accepté de l'être, pour quelle candidature on les a nommés.

Des tiers : leurs coordonnées restent privées à la personne, comme le reste. En
Nouvelle-Zélande, un employeur ne contacte que les répondants que le candidat a
nommés, sauf consentement exprès, et le candidat peut demander à voir ce qu'ils
ont dit (Commissariat à la vie privée NZ). Tahatū : les prévenir avant qu'on les
appelle.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class JobReferee(models.Model):
    _name = "bf.job.referee"
    _inherit = ["bf.job.private.name"]
    _description = "Referee"
    _order = "name"

    name = fields.Char("Name", required=True)
    user_id = fields.Many2one(
        "res.users", string="Person", required=True, readonly=True, index=True,
        default=lambda self: self.env.user, ondelete="cascade")
    active = fields.Boolean(default=True)
    relationship = fields.Char("Relationship", help="For example: my manager at Acme, 2019-2023.")
    organisation = fields.Char("Organisation")
    email = fields.Char("Email")
    phone = fields.Char("Phone")
    consent_date = fields.Date(
        "Agreed on", help="When they agreed to be your referee. Ask before naming anyone.")
    last_briefed = fields.Date("Last briefed on", readonly=True)
    note = fields.Text("Note", help="What they can speak to; anything to remind them of.")
    reference_ids = fields.One2many("bf.job.reference", "referee_id", string="Named for")
    times_named = fields.Integer(compute="_compute_times_named")

    @api.depends("reference_ids")
    def _compute_times_named(self):
        for rec in self:
            rec.times_named = len(rec.reference_ids)


class JobReference(models.Model):
    """Un répondant nommé pour une candidature."""
    _name = "bf.job.reference"
    _inherit = ["bf.job.private.name"]
    _description = "Referee named for an application"
    _order = "date_named desc, id desc"
    _rec_name = "referee_id"

    application_id = fields.Many2one(
        "bf.job.application", string="Application", required=True, ondelete="cascade",
        index=True, domain="[('user_id', '=', uid)]")
    referee_id = fields.Many2one(
        "bf.job.referee", string="Referee", required=True, ondelete="restrict",
        domain="[('user_id', '=', uid)]")
    user_id = fields.Many2one(related="application_id.user_id", store=True, index=True,
                              compute_sudo=False)
    state = fields.Selection(
        [("named", "Named"), ("warned", "Warned"), ("called", "Called by the employer")],
        string="Status", default="named", required=True)
    date_named = fields.Date("Named on", default=fields.Date.context_today)
    date_warned = fields.Date("Warned on", readonly=True)
    date_called = fields.Date("Called on")
    note = fields.Text("Note")

    _sql_constraints = [
        ("referee_once", "unique(application_id, referee_id)",
         "This referee is already named for this application."),
    ]

    @api.constrains("application_id", "referee_id")
    def _check_same_person(self):
        for rec in self.sudo():
            if not self.env.su and rec.application_id.user_id != self.env.user:
                raise ValidationError(self.env._(
                    "A referee can only be named for your own applications."))
            if rec.referee_id.user_id != rec.application_id.user_id:
                raise ValidationError(self.env._(
                    "A referee can only be named for your own applications."))

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        for app in recs.mapped("application_id"):
            if app.stage in ("interview", "offer") and not app.is_closed:
                app._ask_to_warn_referees()
        return recs

    def action_warned(self):
        self.check_access("write")
        today = fields.Date.context_today(self)
        for rec in self:
            rec.write({"state": "warned" if rec.state == "named" else rec.state,
                       "date_warned": today})
            rec.referee_id.last_briefed = today
        for app in self.mapped("application_id"):
            if not app.reference_ids.filtered(lambda r: r.state == "named"):
                app._done_or_cancel(("bf_job_search.mail_act_job_referees",))
        return True

    def action_called(self):
        self.check_access("write")
        today = fields.Date.context_today(self)
        for rec in self:
            rec.write({"state": "called", "date_called": rec.date_called or today})
        return True

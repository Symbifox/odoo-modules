"""The position notice (Quebec Law 25, s. 8.1) and who read which version of it.

⚠️ The notice is shown BEFORE any position is read. A new text, or turning the
positions on, raises the version: everyone reads it again before their next
position is kept. The text as read is stored with the acknowledgement, as evidence.
"""
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class ResCompany(models.Model):
    _inherit = "res.company"

    bf_route_position_mode = fields.Selection(
        [("off", "Never"), ("stops", "Once, when a stop is marked")],
        string="Worker position", default="off", required=True,
        help="Never: no position is read. Once: the phone reads it when the worker marks a "
             "stop, after the worker has read the notice. There is no continuous tracking.")
    bf_route_notice = fields.Html(
        string="Position notice", help="Empty = the standard notice, in each reader's language.")
    bf_route_notice_version = fields.Integer(default=1, readonly=True)
    bf_route_position_days = fields.Integer(string="Keep positions (days)", default=90)
    bf_route_late_minutes = fields.Integer(
        string="Alert after (min)", default=30,
        help="Minutes after the planned start before the person responsible is told a route "
             "has not started.")

    @api.constrains("bf_route_position_days", "bf_route_late_minutes")
    def _check_bf_route_bounds(self):
        for company in self:
            if not 1 <= company.bf_route_position_days <= 3650:
                raise ValidationError(_("Positions are kept from 1 to 3,650 days."))
            if not 0 <= company.bf_route_late_minutes <= 1440:
                raise ValidationError(_("The alert comes from 0 to 1,440 minutes after the start."))

    def write(self, vals):
        # ⚠️ The retention period is IN the notice: a longer one is a new notice to read.
        watched = {"bf_route_notice", "bf_route_position_mode", "bf_route_position_days"} & set(vals)
        before = {c.id: (c.bf_route_notice, c.bf_route_position_mode, c.bf_route_position_days)
                  for c in self} if watched else {}
        result = super().write(vals)
        if watched:
            for company in self:
                old_notice, old_mode, old_days = before[company.id]
                if company.bf_route_notice != old_notice or (
                        company.bf_route_position_mode == "stops" and old_mode != "stops") or (
                        company.bf_route_position_days != old_days):
                    company.sudo().bf_route_notice_version = company.bf_route_notice_version + 1
        return result


class BfRouteNoticeAck(models.Model):
    _name = "bf.route.notice.ack"
    _description = "Position notice read by a worker"
    _order = "date desc, id desc"

    user_id = fields.Many2one("res.users", string="Worker", required=True, ondelete="cascade",
                              index=True, readonly=True)
    company_id = fields.Many2one("res.company", required=True, ondelete="cascade", readonly=True)
    version = fields.Integer(required=True, readonly=True)
    date = fields.Datetime(string="Read on", required=True, default=fields.Datetime.now,
                           readonly=True)
    notice = fields.Html(string="Text read", readonly=True, sanitize=True)

    _sql_constraints = [
        ("one_per_version", "UNIQUE(user_id, company_id, version)",
         "The notice was already read in this version."),
    ]

    @api.model
    def _default_notice(self, company):
        return Markup(_(
            "<p><b>Your position at the stops</b></p>"
            "<p>When you mark a stop (done, customer absent, postponed), the application reads "
            "your phone's position <b>once</b>, at that moment, and keeps it with the stop as "
            "proof of the visit. It never follows you between stops, before the day starts or "
            "after it ends.</p>"
            "<p><b>Why:</b> to prove the visit to the customer and settle a disagreement about a "
            "delivery. <b>Who sees it:</b> the people who manage the routes. <b>How long:</b> "
            "%(days)s days, then it is erased.</p>"
            "<p>Your phone asks for your permission the first time. If you refuse, the stops "
            "are marked without a position.</p>",
            days=company.bf_route_position_days or 90))

    @api.model
    def _notice_text(self, company):
        return company.bf_route_notice or self._default_notice(company)

    @api.model
    def _acknowledged(self, user, company):
        return bool(self.sudo().search_count([
            ("user_id", "=", user.id), ("company_id", "=", company.id),
            ("version", "=", company.bf_route_notice_version)]))

    @api.model
    def _may_read_position(self, user, company):
        return company.bf_route_position_mode == "stops" and self._acknowledged(user, company)

    @api.model
    def _status(self, company):
        on = company.bf_route_position_mode == "stops"
        read = on and self._acknowledged(self.env.user, company)
        return {
            "company_id": company.id,
            "positions": on,
            "must_read": on and not read,
            "may_read_position": read,
            "version": company.bf_route_notice_version,
            "text": str(self._notice_text(company)) if on else "",
        }

    @api.model
    def app_acknowledge(self, version, company_id=None):
        """``company_id``: the company whose notice the phone showed (that of the worker's day);
        any of the worker's companies, else the current one."""
        self = self.with_env(self.env(context={k: v for k, v in self.env.context.items()
                                               if k in ("lang", "tz", "allowed_company_ids")}))
        if not self.env.user.has_group("bf_route.group_route_user"):
            raise AccessError(_("Only a worker on the road reads this notice."))
        company = self.env.company
        if company_id:
            try:
                wanted = self.env["res.company"].browse(int(company_id))
            except (TypeError, ValueError):
                wanted = company
            if wanted in self.env.user.company_ids:
                company = wanted
        if company.bf_route_position_mode != "stops":
            raise UserError(_("Positions are not read in this company."))
        try:
            version = int(version)
        except (TypeError, ValueError):
            version = 0
        if version != company.bf_route_notice_version:
            raise UserError(_("The notice has changed. Read the new one."))
        if not self._acknowledged(self.env.user, company):
            self.sudo().create({"user_id": self.env.user.id, "company_id": company.id,
                                "version": version, "notice": self._notice_text(company)})
        return self._status(company)

import secrets

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import consteq

KINDS = [("homework", "Homework"), ("study", "Study"), ("project", "Project"), ("test", "Announced test")]


class Homework(models.Model):
    """Something a group has to do by a date."""

    _name = "bf.school.homework"
    _description = "Homework"
    _order = "date_due, id"

    name = fields.Char(required=True)
    group_id = fields.Many2one("bf.school.group", "Group", required=True, ondelete="cascade", index=True,
                               domain="[('year_id.state', '=', 'current')]")
    company_id = fields.Many2one(related="group_id.company_id", store=True)
    teacher_id = fields.Many2one("res.users", "Teacher", default=lambda s: s.env.user, readonly=True)
    kind = fields.Selection(KINDS, default="homework", required=True)
    description = fields.Html(sanitize=True)
    date_assigned = fields.Date("Given on", required=True, default=fields.Date.context_today)
    date_due = fields.Date("Due on", required=True, index=True)
    active = fields.Boolean(default=True)

    @api.constrains("date_assigned", "date_due")
    def _check_dates(self):
        for homework in self:
            if homework.date_due < homework.date_assigned:
                raise ValidationError(_("A homework is due after it is given."))

    @api.model
    def _school_for_partner(self, partner, since=None):
        """[(homework, student)] for the children this adult receives notices about."""
        children = partner._school_portal_links().filtered("receives_notices").student_id
        pairs = []
        by_group = {}
        for child in children:
            for group in child.student_enrollment_ids.filtered(
                    lambda e: e.state == "active" and e.year_id.state == "current").group_id:
                by_group.setdefault(group, self.env["res.partner"])
                by_group[group] |= child
        if not by_group:
            return pairs
        domain = [("group_id", "in", [g.id for g in by_group])]
        if since:
            domain.append(("date_due", ">=", since))
        for homework in self.sudo().search(domain, order="date_due, id"):
            for child in by_group[homework.group_id]:
                pairs.append((homework, child))
        return pairs


class ResPartner(models.Model):
    _inherit = "res.partner"

    school_ical_token = fields.Char(copy=False, groups="base.group_system")

    def _school_ical_token(self):
        """The adult's secret for the calendar feed, created on first use."""
        self.ensure_one()
        partner = self.sudo()
        if not partner.school_ical_token:
            partner.school_ical_token = secrets.token_urlsafe(32)
        return partner.school_ical_token

    def _school_ical_check(self, token):
        self.ensure_one()
        stored = self.sudo().school_ical_token
        return bool(stored and token) and consteq(stored, token)

    def _school_ical_reset(self):
        """A new secret: the old link stops working (shared by mistake, lost phone)."""
        self.ensure_one()
        self.sudo().school_ical_token = secrets.token_urlsafe(32)

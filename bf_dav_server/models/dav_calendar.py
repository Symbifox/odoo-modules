# Part of bf_dav_server. See LICENSE file for full copyright and licensing details.
import json
import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def slugify(value):
    value = re.sub(r"[^a-z0-9._-]+", "-", (value or "").lower()).strip("-.")
    return value[:64] or "calendar"


class BfDavCalendar(models.Model):
    """A calendar collection served over CalDAV.

    Odoo has one agenda per person; CalDAV clients expect several named
    calendars. A collection is the "home" of the events created in it. The
    owner's default collection also shows every event the owner attends whose
    home belongs to someone else, which is what the Odoo agenda shows.
    """

    _name = "bf.dav.calendar"
    _description = "CalDAV calendar"
    _order = "owner_id, sequence, id"

    name = fields.Char(required=True, translate=False)
    owner_id = fields.Many2one(
        "res.users", required=True, ondelete="cascade", index=True,
        default=lambda self: self.env.user, domain=[("share", "=", False)])
    slug = fields.Char("Short address", required=True, copy=False,
                       help="Last part of the collection address. Changing it makes "
                            "clients see a new calendar.")
    color = fields.Char(default="#29ABE2", help="Color proposed to calendar apps (hex).")
    odoo_color = fields.Integer(
        "Color in Odoo", default=0,
        help="Color of this calendar and of its events in Odoo's Calendar app (Odoo palette).")
    sequence = fields.Integer(default=10)
    description = fields.Text()
    is_default = fields.Boolean(
        string="Default", help="Receives the events created in Odoo and every event "
                               "the owner attends that lives in someone else's calendar.")
    active = fields.Boolean(default=True)
    dead_props = fields.Text(
        default="{}", copy=False,
        help="Client properties stored as is (PROPPATCH), JSON.")
    event_count = fields.Integer("Events", compute="_compute_event_count")

    _sql_constraints = [
        ("owner_slug_uniq", "unique (owner_id, slug)", "Each calendar address must be unique per user."),
    ]

    @api.constrains("slug")
    def _check_slug(self):
        reserved = {"inbox", "outbox"}
        for cal in self:
            if not SLUG_RE.match(cal.slug or "") or cal.slug in reserved or cal.slug.startswith("s-"):
                raise ValidationError(_("Invalid calendar address: %s", cal.slug))

    @api.constrains("is_default", "owner_id")
    def _check_one_default(self):
        for cal in self.filtered("is_default"):
            if self.search_count([("owner_id", "=", cal.owner_id.id), ("is_default", "=", True),
                                  ("id", "!=", cal.id)]):
                raise ValidationError(_("A user has only one default calendar."))

    def _compute_event_count(self):
        groups = self.env["calendar.event"]._read_group(
            [("bf_dav_calendar_id", "in", self.ids)], ["bf_dav_calendar_id"], ["__count"])
        counts = {cal.id: count for cal, count in groups}
        for cal in self:
            cal.event_count = counts.get(cal.id, 0)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("slug"):
                vals["slug"] = self._unique_slug(vals.get("owner_id") or self.env.uid,
                                                 slugify(vals.get("name")))
        cals = super().create(vals_list)
        cals._bf_dav_touch()
        return cals

    def write(self, vals):
        res = super().write(vals)
        if set(vals) & {"name", "color", "slug", "active", "is_default", "dead_props", "description"}:
            self._bf_dav_touch()
        if "odoo_color" in vals:
            self._bf_dav_paint_events()
        return res

    def _bf_dav_paint_events(self):
        """Events take the color of the calendar they live in (Odoo's Calendar app)."""
        if "color" not in self.env["calendar.event"]._fields:
            return
        self.env["calendar.event"].flush_model()
        for cal in self:
            self.env.cr.execute("UPDATE calendar_event SET color = %s WHERE bf_dav_calendar_id = %s "
                                "AND color IS DISTINCT FROM %s", [cal.odoo_color, cal.id, cal.odoo_color])
        self.env["calendar.event"].invalidate_model(["color"])

    @api.model
    def _unique_slug(self, owner_id, base):
        slug, n = base, 1
        while self.with_context(active_test=False).search_count(
                [("owner_id", "=", owner_id), ("slug", "=", slug)]):
            n += 1
            slug = "%s-%d" % (base[:60], n)
        return slug

    def _bf_dav_key(self):
        self.ensure_one()
        return "cal:%d" % self.id

    def _bf_dav_touch(self):
        """Collection metadata changed: bump its sync token."""
        change = self.env["bf.dav.change"]
        for cal in self:
            change._record([cal._bf_dav_key()], "", "")

    def _bf_dav_dead_props(self):
        self.ensure_one()
        try:
            return json.loads(self.dead_props or "{}")
        except ValueError:
            return {}

    @api.model
    def _bf_dav_default_for(self, user):
        """The user's default collection, created on first use."""
        user = user.sudo()
        cal = self.sudo().search([("owner_id", "=", user.id), ("is_default", "=", True)], limit=1)
        if not cal:
            cal = self.sudo().create({
                "name": self.with_context(lang=user.lang or "en_US").env._("Calendar"),
                "owner_id": user.id,
                "slug": self._unique_slug(user.id, "calendar"),
                "is_default": True,
            })
        return cal

    def action_view_events(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": "calendar.event",
            "view_mode": "list,form",
            "domain": [("bf_dav_calendar_id", "=", self.id)],
        }

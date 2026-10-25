# Part of bf_dav_server. See LICENSE file for full copyright and licensing details.
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.base.models.res_users import check_identity

from ..models.res_users import DAV_SCOPE


class BfDavPasswordWizard(models.TransientModel):
    """Create an app password for one device (Thunderbird, a phone, a Mac).

    The password is an Odoo API key of scope ``dav``: it opens the calendars
    and address books only, never the Odoo API, never the Odoo login, and is
    revoked from the API keys list of the user's preferences.
    """

    _name = "bf.dav.password.wizard"
    _description = "New calendar and contacts app password"

    user_id = fields.Many2one("res.users", required=True, default=lambda self: self.env.user)
    name = fields.Char("Device", required=True, help="For example: Thunderbird office, Android phone.")
    key = fields.Char(readonly=True)
    login = fields.Char(related="user_id.login")
    server_url = fields.Char("Server address", compute="_compute_server_url")
    caldav_url = fields.Char("CalDAV address", compute="_compute_server_url")
    carddav_url = fields.Char("CardDAV address", compute="_compute_server_url")
    state = fields.Selection([("new", "New"), ("done", "Done")], default="new")

    @api.model
    def _bf_dav_public_url(self):
        """The address apps must use. Not web.base.url: it may carry a www. that the
        proxy answers with a redirect (308), and calendar apps
        do not follow a redirect while they look for calendars."""
        ICP = self.env["ir.config_parameter"].sudo()
        url = ICP.get_param("bf_dav_server.public_url")
        if not url and "website" in self.env:
            site = self.env["website"].sudo().search(
                [("company_id", "=", self.env.company.id), ("domain", "!=", False)], order="sequence, id", limit=1)
            url = site.domain or ""
            if url and "://" not in url:
                url = "https://" + url
        return (url or ICP.get_param("web.base.url", "")).rstrip("/")

    @api.depends("user_id")
    def _compute_server_url(self):
        base = self._bf_dav_public_url()
        for wiz in self:
            wiz.server_url = base
            wiz.caldav_url = "%s/dav/calendars/%d/" % (base, wiz.user_id.id)
            wiz.carddav_url = "%s/dav/addressbooks/%d/" % (base, wiz.user_id.id)

    @check_identity
    def action_generate(self):
        self.ensure_one()
        if self.user_id != self.env.user and not self.env.user.has_group("base.group_system"):
            raise AccessError(_("You can only create app passwords for yourself."))
        if self.user_id.share:
            raise UserError(_("Only internal users can sync calendars and contacts."))
        name = _("Calendar and contacts: %s", self.name)
        key = self.env["res.users.apikeys"].with_user(self.user_id).sudo()._generate(DAV_SCOPE, name, None)
        self.write({"key": key, "state": "done"})
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

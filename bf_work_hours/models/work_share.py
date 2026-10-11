from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .tools import acting_in_own_web_session, own_record_vals


class WorkShare(models.Model):
    """A person lets one named colleague see their days. Their choice, revocable.

    By design, the employer sees nothing individual,
    unless the person shares, by an express gesture, with someone they name.
    The colleague sees the DAYS (hours and, if activated, indicators), never
    the raw traces. Revoking cuts the access to the whole history at once.
    """

    _name = "bf.work.share"
    _description = "Working time: days shared with a colleague"
    _order = "create_date desc"
    _rec_name = "viewer_id"
    # bf_impersonate hides these from whoever impersonates the person.
    _gen_scope = "bf_work_hours"

    owner_id = fields.Many2one("res.users", string="Shared by", required=True, readonly=True, index=True,
                               ondelete="cascade", default=lambda self: self.env.user)
    viewer_id = fields.Many2one("res.users", string="Shared with", required=True, index=True,
                                ondelete="cascade", domain="[('share', '=', False), ('id', '!=', uid)]")
    active = fields.Boolean(default=True, readonly=True)
    revoked_at = fields.Datetime(readonly=True)

    def init(self):
        # One LIVE share per pair; revoked ones pile up as history.
        self.env.cr.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS bf_work_share_live_unique "
            "ON bf_work_share (owner_id, viewer_id) WHERE active")

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and not acting_in_own_web_session(self.env):
            raise AccessError(_("Only the person concerned can share their days, in their own session."))
        if not self.env.su:
            for vals in vals_list:
                own_record_vals(self.env, vals, "owner_id", _("Only the person concerned can share their days."))
        shares = super().create(vals_list)
        if not self.env.su:
            for share in shares:
                if share.owner_id.id != self.env.uid:
                    raise AccessError(_("Only the person concerned can share their days."))
                if share.viewer_id == share.owner_id or share.viewer_id.share or not share.active:
                    raise UserError(_("Share with one internal colleague other than yourself."))
        self._bf_access_changed()
        return shares

    def write(self, vals):
        if not self.env.su:
            raise UserError(_("A share cannot be changed: revoke it and share again."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise UserError(_("A share is revoked, not deleted: it stays as a trace of who saw what."))
        return super().unlink()

    def action_revoke(self):
        for share in self:
            if not self.env.su and (share.owner_id.id != self.env.uid or not acting_in_own_web_session(self.env)):
                raise AccessError(_("Only the person who shared can revoke."))
        live = self.filtered("active")
        live.sudo().write({"active": False, "revoked_at": fields.Datetime.now()})
        self._bf_access_changed()
        return True

    def _bf_access_changed(self):
        """Who sees whose days changed: forget what this transaction already read.

        The record rule itself names no user id but the reader's, so its
        registry cache never holds a stale list of owners.
        """
        self.env["res.users"].invalidate_model(["bf_work_shared_owner_ids", "bf_work_share_ids"])
        self.env["bf.work.day"].invalidate_model()

    @api.model
    def _bf_owner_ids_for(self, viewer_id):
        """Ids of the people who currently share their days with ``viewer_id``."""
        self.flush_model(["owner_id", "viewer_id", "active"])
        self.env.cr.execute(
            "SELECT owner_id FROM bf_work_share WHERE viewer_id = %s AND active", [viewer_id])
        return [row[0] for row in self.env.cr.fetchall()]

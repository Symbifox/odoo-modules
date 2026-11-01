from odoo import _, api, fields, models
from odoo.exceptions import UserError


class HostWait(models.Model):
    """A party waiting for a table, with the wait the host quoted. Its name is erased when the session closes."""
    _name = "bf.host.wait"
    _description = "Waiting party"
    _order = "arrived_at, id"

    company_id = fields.Many2one(related="config_id.company_id", store=True, index=True)
    config_id = fields.Many2one("pos.config", "Register", required=True, readonly=True, index=True,
                                ondelete="cascade")
    session_id = fields.Many2one("pos.session", readonly=True, index=True, ondelete="set null")
    guest_name = fields.Char("Name", readonly=True, help="Erased when the session closes.")
    party_size = fields.Integer("Covers", readonly=True, default=2)
    note = fields.Char(readonly=True, help="Erased when the session closes.")
    arrived_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)
    quoted_minutes = fields.Integer("Wait quoted (minutes)", readonly=True)
    state = fields.Selection([("waiting", "Waiting"), ("seated", "Seated"), ("left", "Left")],
                             required=True, default="waiting", readonly=True, index=True)
    seated_at = fields.Datetime(readonly=True)
    waited_minutes = fields.Integer("Waited (minutes)", compute="_compute_waited", store=True, aggregator="avg")
    seating_id = fields.Many2one("bf.host.seating", readonly=True, ondelete="set null")

    @api.depends("arrived_at", "seated_at")
    def _compute_waited(self):
        for wait in self:
            wait.waited_minutes = (max(int((wait.seated_at - wait.arrived_at).total_seconds() // 60), 0)
                                   if wait.seated_at and wait.arrived_at else 0)

    @api.depends("guest_name", "party_size")
    def _compute_display_name(self):
        for wait in self:
            wait.display_name = _("%(name)s, %(covers)s", name=wait.guest_name or _("Party"), covers=wait.party_size)

    @api.model
    def bf_host_wait_add(self, config_id, guest_name, party_size, note=""):
        Seating = self.env["bf.host.seating"]
        config = Seating._bf_config(config_id)
        party = Seating._bf_party(party_size)
        if not (guest_name or "").strip():
            raise UserError(_("Give a name to call the party."))
        now = fields.Datetime.now()
        ahead = self.sudo().search_count([("config_id", "=", config.id), ("state", "=", "waiting")])
        quoted = Seating._bf_quote(config, party, ahead, Seating._bf_table_states(config), now)
        wait = self.sudo().create({
            "config_id": config.id, "session_id": config.current_session_id.id,
            "guest_name": guest_name.strip()[:80], "party_size": party, "note": (note or "").strip()[:200] or False,
            "quoted_minutes": quoted,
        })
        return {"id": wait.id, "quoted": quoted}

    @api.model
    def bf_host_wait_quote(self, config_id, party_size):
        Seating = self.env["bf.host.seating"]
        config = Seating._bf_config(config_id)
        ahead = self.sudo().search_count([("config_id", "=", config.id), ("state", "=", "waiting")])
        return Seating._bf_quote(config, Seating._bf_party(party_size), ahead, Seating._bf_table_states(config),
                                 fields.Datetime.now())

    def bf_host_wait_left(self):
        wait = self.sudo()
        wait.ensure_one()
        self.env["bf.host.seating"]._bf_config(wait.config_id.id)
        if wait.state != "waiting":
            raise UserError(_("This party is no longer waiting."))
        wait.write({"state": "left"})
        return True

    @api.model
    def _bf_purge(self, domain):
        waits = self.sudo().search(domain + ["|", ("guest_name", "!=", False), ("note", "!=", False)])
        waits.write({"guest_name": False, "note": False})
        return waits

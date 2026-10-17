from odoo import _, api, fields, models


class BfKeyDashboard(models.TransientModel):
    """The few numbers a key manager watches, each one opening its list."""
    _name = "bf.key.dashboard"
    _description = "Keys overview"

    name = fields.Char(default=lambda self: _("Keys overview"))
    held_count = fields.Integer(string="Handed out", compute="_compute_counts")
    overdue_count = fields.Integer(string="Overdue", compute="_compute_counts")
    departed_count = fields.Integer(string="To recover (departures)", compute="_compute_counts")
    card_count = fields.Integer(string="Cards to deactivate", compute="_compute_counts")
    master_count = fields.Integer(string="Master keys out", compute="_compute_counts")
    incident_count = fields.Integer(string="Open losses", compute="_compute_counts")
    request_count = fields.Integer(string="Requests to approve", compute="_compute_counts")
    review_count = fields.Integer(string="Systems to review", compute="_compute_counts")
    last_audit_date = fields.Date(string="Last audit", compute="_compute_counts")

    # Each list: (field, model, domain builder). The domain is evaluated with the user's
    # rights, so an area's responsible sees the numbers of their areas only.
    def _domains(self):
        today = fields.Date.context_today(self)
        return {
            "held_count": ("bf.key.assignment", [("date_returned", "=", False)]),
            "overdue_count": ("bf.key.assignment", [("is_overdue", "=", True)]),
            "departed_count": ("bf.key.assignment", [
                ("date_returned", "=", False), ("employee_departed", "=", True)]),
            "card_count": ("bf.key.assignment", [
                ("kind", "in", ["card", "fob", "remote"]), ("card_deactivated", "=", False),
                "|", ("end_reason", "in", ["lost", "stolen"]),
                "&", ("date_returned", "=", False), ("employee_departed", "=", True)]),
            "master_count": ("bf.key.assignment", [
                ("date_returned", "=", False), ("type_id.is_master", "=", True)]),
            "incident_count": ("bf.key.incident", [("state", "=", "open")]),
            "request_count": ("bf.key.request", [("state", "=", "submitted")]),
            "review_count": ("bf.key.system", [("next_review_date", "<=", today)]),
        }

    def _compute_counts(self):
        domains = self._domains()
        is_manager = self.env.user.has_group("bf_key_management.group_key_area_manager")
        for board in self:
            for field, (model, domain) in domains.items():
                if field == "review_count" and not self.env.user.has_group(
                        "bf_key_management.group_key_manager"):
                    board[field] = 0
                    continue
                board[field] = self.env[model].with_context(active_test=False).search_count(
                    domain) if is_manager else 0
            audit = self.env["bf.key.audit"].search([("state", "=", "done")], limit=1, order="date desc") \
                if self.env.user.has_group("bf_key_management.group_key_manager") else False
            board.last_audit_date = audit.date if audit else False

    @api.model
    def action_open_overview(self):
        """⚠️ Opened on a saved record: on an unsaved form the web client asks for the values
        through onchange, which leaves these computed counts at zero."""
        board = self.create({})
        return {
            "type": "ir.actions.act_window", "name": _("Overview"), "res_model": self._name,
            "res_id": board.id, "view_mode": "form", "target": "current",
        }

    def _open(self, field, title):
        model, domain = self._domains()[field]
        return {
            "type": "ir.actions.act_window", "name": title, "res_model": model,
            "view_mode": "list,form", "domain": domain,
            "context": {"active_test": False},
        }

    def action_open_held(self):
        return self._open("held_count", _("Handed out"))

    def action_open_overdue(self):
        return self._open("overdue_count", _("Overdue"))

    def action_open_departed(self):
        return self._open("departed_count", _("To recover (departures)"))

    def action_open_cards(self):
        return self._open("card_count", _("Cards to deactivate"))

    def action_open_masters(self):
        return self._open("master_count", _("Master keys out"))

    def action_open_incidents(self):
        return self._open("incident_count", _("Open losses"))

    def action_open_requests(self):
        return self._open("request_count", _("Requests to approve"))

    def action_open_reviews(self):
        return self._open("review_count", _("Systems to review"))

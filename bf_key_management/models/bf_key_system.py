from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

from .bf_key_type import schedule_activity


class BfKeySystem(models.Model):
    """A master key system: the keys of one keyway, arranged in levels.

    The keying schedule (which cylinder takes which key) comes from the
    locksmith; it is attached here and read by the key managers only.
    """
    _name = "bf.key.system"
    _description = "Master key system"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    levels = fields.Selection(
        [("2", "2 levels"), ("3", "3 levels"), ("4", "4 levels")],
        string="Levels", required=True, default="3", tracking=True,
        help="2: masters and change keys. 3: a grand master above the masters. 4: a great grand "
             "master above the grand masters. Security guides advise against more than four.")
    keyway = fields.Char(help="Profile of the blanks (manufacturer's reference).")
    restricted = fields.Boolean(
        string="Restricted keyway", tracking=True,
        help="Blanks are sold only by the manufacturer or an authorized locksmith. This, and "
             "not a \"Do not duplicate\" stamp, is what prevents copies.")
    locksmith_id = fields.Many2one("res.partner", string="Locksmith")
    schedule_file = fields.Binary(string="Keying schedule", attachment=True)
    schedule_filename = fields.Char(string="Schedule file name")
    last_rekey_date = fields.Date(string="Last rekeyed", tracking=True)
    review_months = fields.Integer(
        string="Review every (months)", default=60,
        help="Guides advise reviewing the keys and cylinders at least every five years.")
    next_review_date = fields.Date(string="Next review", compute="_compute_next_review_date", store=True)
    type_ids = fields.One2many("bf.key.type", "system_id", string="Key types")
    door_ids = fields.One2many("bf.key.door", "system_id", string="Doors")
    type_count = fields.Integer(string="Key types", compute="_compute_counts")
    door_count = fields.Integer(string="Doors", compute="_compute_counts")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    active = fields.Boolean(default=True)
    note = fields.Text()

    @api.depends("last_rekey_date", "review_months", "create_date")
    def _compute_next_review_date(self):
        for system in self:
            start = system.last_rekey_date or (system.create_date and system.create_date.date())
            if start and system.review_months > 0:
                system.next_review_date = start + relativedelta(months=system.review_months)
            else:
                system.next_review_date = False

    def _compute_counts(self):
        for system in self:
            system.type_count = len(system.type_ids)
            system.door_count = len(system.door_ids)

    def action_view_types(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": _("Key types"), "res_model": "bf.key.type",
            "view_mode": "list,form", "domain": [("system_id", "=", self.id)],
            "context": {"default_system_id": self.id},
        }

    def action_view_doors(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": _("Doors"), "res_model": "bf.key.door",
            "view_mode": "list,form", "domain": [("system_id", "=", self.id)],
            "context": {"default_system_id": self.id},
        }

    @api.model
    def _cron_review(self):
        """One activity per system whose review date has passed, while none is open."""
        today = fields.Date.context_today(self)
        todo = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        for system in self.search([("next_review_date", "<=", today)]):
            manager = system.company_id.bf_key_manager_id
            if not manager or system.activity_ids.filtered(lambda a: a.user_id == manager):
                continue
            # ⚠️ A scheduled job has no language: write in the reader's.
            env = system.with_context(lang=manager.lang or "en_US").env
            schedule_activity(
                system,
                activity_type_id=todo.id if todo else False,
                user_id=manager.id,
                summary=env._("Review the master key system"),
                note=env._("Check the keys in circulation, the cylinders and the losses since "
                           "the last rekeying. Record the date in \"Last rekeyed\" once done."),
            )

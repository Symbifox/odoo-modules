"""Les départs du foyer : qui est parti, comment, et quand ses fiches s'effacent.

Personne ne coupe à un autre adulte
l'accès à ses propres données. Un adulte part de lui-même (« Quitter le foyer »,
après l'export de ses fiches), ou Blue Fox le retire sur demande, avec un délai
pendant lequel il garde son accès pour exporter. Un responsable du foyer ne retire
qu'un ado ou un compte jamais utilisé.

Un compte parti est archivé : ses fiches privées restent en base, fermées à tous,
jusqu'à leur effacement par la procédure de Blue Fox, dû 30 jours après le
départ. Ce registre est celui de Blue Fox : seule l'administration le lit.
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

#: Le délai d'un retrait demandé à Blue Fox, comme le non-renouvellement.
REMOVAL_NOTICE_DAYS = 30
#: L'effacement des fiches d'un compte parti est dû après ce délai.
ERASE_AFTER_DAYS = 30


class HouseholdDeparture(models.Model):
    _name = "bf.household.departure"
    _description = "Household departure"
    _order = "requested_on desc, id desc"
    _rec_name = "user_id"

    user_id = fields.Many2one(
        "res.users", string="Account", required=True, ondelete="cascade", index=True,
        context={"active_test": False})
    kind = fields.Selection(
        [("left", "Left the household"),
         ("removed", "Removed by a household manager"),
         ("scheduled", "Removal requested from Blue Fox")],
        string="How", required=True)
    requested_by_id = fields.Many2one("res.users", string="Requested by", ondelete="set null")
    requested_on = fields.Datetime(string="Requested on", default=fields.Datetime.now, required=True)
    effective_on = fields.Date(string="Account closed on", required=True)
    erase_due = fields.Date(
        string="Records to erase by", compute="_compute_erase_due", store=True,
        help="The departed account's private records are erased by Blue Fox's procedure.")
    state = fields.Selection(
        [("scheduled", "Scheduled"), ("done", "Done"), ("cancelled", "Cancelled")],
        string="State", default="scheduled", required=True)
    note = fields.Text(string="Note")

    @api.depends("effective_on")
    def _compute_erase_due(self):
        for rec in self:
            rec.erase_due = rec.effective_on and rec.effective_on + timedelta(days=ERASE_AFTER_DAYS)

    def action_cancel(self):
        for rec in self:
            if rec.state != "scheduled":
                raise UserError(_("Only a scheduled removal can be cancelled."))
        self.write({"state": "cancelled"})
        return True

    @api.model
    def _cron_apply_departures(self):
        today = fields.Date.context_today(self)
        dues = self.sudo().search([("state", "=", "scheduled"), ("effective_on", "<=", today)])
        for depart in dues:
            if depart.user_id.active:
                depart.user_id._bf_household_archive()
            depart.state = "done"
        return len(dues)

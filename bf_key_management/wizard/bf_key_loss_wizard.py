from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.bf_key_type import ELECTRONIC, schedule_activity


class BfKeyLossWizard(models.TransientModel):
    """Declare a copy lost or stolen: close the hand-out, record the incident, freeze the
    doors it opened, and hand the decision to the key manager."""
    _name = "bf.key.loss.wizard"
    _description = "Declare a key lost or stolen"

    copy_id = fields.Many2one("bf.key.copy", string="Copy", required=True)
    holder_label = fields.Char(related="copy_id.holder_label")
    incident_type = fields.Selection([("lost", "Lost"), ("stolen", "Stolen")], string="What happened",
                                     required=True, default="lost")
    date = fields.Date(required=True, default=fields.Date.context_today)
    circumstances = fields.Text()
    police_report = fields.Char(string="Police report number")
    compromised_count = fields.Integer(string="Doors it opens", compute="_compute_compromised")
    compromised_names = fields.Char(string="Among them", compute="_compute_compromised")

    @api.depends("copy_id")
    def _compute_compromised(self):
        for wizard in self:
            doors = wizard.copy_id.type_id.sudo()._doors_opened() if wizard.copy_id else []
            wizard.compromised_count = len(doors)
            wizard.compromised_names = ", ".join(doors[:8].mapped("name")) if doors else False

    def action_declare(self):
        self.ensure_one()
        copy = self.copy_id
        if copy.state not in ("issued", "available"):
            raise UserError(_("%s is not in use.", copy.display_name))
        assignment = copy.current_assignment_id
        when = fields.Datetime.now()
        if assignment:
            assignment._close(self.incident_type, when)
        copy.disposition = self.incident_type
        key_type = copy.type_id
        electronic = key_type.kind in ELECTRONIC
        incident = self.env["bf.key.incident"].create({
            "copy_id": copy.id,
            "assignment_id": assignment.id,
            "incident_type": self.incident_type,
            "date": self.date,
            "circumstances": self.circumstances,
            "police_report": self.police_report,
            "compromised_door_ids": [(6, 0, key_type.sudo()._doors_opened().ids)],
            "decision": "deactivate" if electronic else "pending",
            "fee_charged": key_type.sudo().loss_fee,
        })
        user = assignment._responsible() if assignment else (
            copy.company_id.bf_key_manager_id or self.env.user)
        if user:
            env = incident.with_context(lang=user.lang or "en_US").env
            if electronic:
                summary = env._("Deactivate %s in the access system", copy.display_name)
                what = dict(self.with_env(env)._fields["incident_type"]._description_selection(env))
                note = env._("Reported %s. Switch it off now; it can be switched back on if it "
                             "turns up.", what[self.incident_type].lower())
            else:
                summary = env._("Decide: rekey after the loss of %s", copy.display_name)
                note = env._("%(count)s doors open with this key. Decide whether to rekey them, "
                             "and record the decision on the incident.",
                             count=len(incident.sudo().compromised_door_ids))
            schedule_activity(incident, act_type_xmlid="mail.mail_activity_data_todo",
                              user_id=user.id, summary=summary, note=note)
        return {
            "type": "ir.actions.act_window", "res_model": "bf.key.incident",
            "res_id": incident.id, "view_mode": "form", "target": "current",
        }

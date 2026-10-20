# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class BfOutreachExcludeWizard(models.TransientModel):
    _name = "bf.outreach.exclude.wizard"
    _description = "Do not contact"

    target_ids = fields.Many2many("bf.outreach.target", string="Targets")
    partner_ids = fields.Many2many("res.partner", string="Contacts")
    reason = fields.Char(
        string="Reason",
        help="What the person asked for, in their own words. Kept as "
             "evidence that the request was acted on.",
    )
    log_touch = fields.Boolean(
        string="Log the interaction",
        default=True,
        help="Records the refusal as an interaction on the targets "
             "concerned, so the request shows up in the history.",
    )
    kind = fields.Selection(
        [
            ("call", "Call"),
            ("email", "Email"),
            ("letter", "Letter"),
            ("sms", "Text message"),
            ("other", "Other"),
        ],
        string="Received by",
        default="call",
        required=True,
    )

    def action_exclude(self):
        self.ensure_one()
        targets = self.target_ids
        # Une exclusion posée depuis un contact vaut pour toutes ses cibles.
        if self.partner_ids:
            self.partner_ids.write(
                {
                    "outreach_opt_out": True,
                    "outreach_opt_out_reason": self.reason,
                }
            )
            targets |= self.env["bf.outreach.target"].search(
                [("partner_id", "in", self.partner_ids.ids)]
            )
        if not targets and not self.partner_ids:
            raise UserError(_("No target or contact selected."))

        if self.log_touch and targets:
            self.env["bf.outreach.touch"].create(
                [
                    {
                        "target_id": target.id,
                        "kind": self.kind,
                        "direction": "in",
                        "outcome": "not_interested",
                        "summary": _("Request not to be contacted again"),
                        "note": self.reason or False,
                    }
                    for target in targets
                ]
            )
        if targets:
            targets.write(
                {"do_not_contact": True, "do_not_contact_reason": self.reason}
            )
        return {"type": "ir.actions.act_window_close"}

# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class BfOutreachExcludeWizard(models.TransientModel):
    _name = "bf.outreach.exclude.wizard"
    _description = "Ne plus contacter"

    target_ids = fields.Many2many("bf.outreach.target", string="Cibles")
    partner_ids = fields.Many2many("res.partner", string="Contacts")
    reason = fields.Char(
        string="Motif",
        help="Ce que la personne a demandé, dans ses mots. Conservé comme preuve "
        "du traitement de la demande.",
    )
    log_touch = fields.Boolean(
        string="Journaliser l'interaction",
        default=True,
        help="Consigne le refus comme interaction sur les cibles concernées, "
        "afin que la demande apparaisse dans l'historique.",
    )
    kind = fields.Selection(
        [
            ("call", "Appel"),
            ("email", "Courriel"),
            ("letter", "Lettre"),
            ("sms", "Texto"),
            ("other", "Autre"),
        ],
        string="Reçue par",
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
            raise UserError(_("Aucune cible ni contact sélectionné."))

        if self.log_touch and targets:
            self.env["bf.outreach.touch"].create(
                [
                    {
                        "target_id": target.id,
                        "kind": self.kind,
                        "direction": "in",
                        "outcome": "not_interested",
                        "summary": _("Demande de ne plus être contacté"),
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

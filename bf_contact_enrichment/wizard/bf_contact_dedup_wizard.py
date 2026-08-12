from odoo import _, fields, models

from ..tools import matching


class BfContactDedupWizard(models.TransientModel):
    _name = "bf.contact.dedup.wizard"
    _description = "Détecteur de doublons de contacts"

    line_ids = fields.One2many(
        "bf.contact.dedup.line", "wizard_id", string="Doublons détectés")
    scanned = fields.Boolean(default=False)

    def action_scan(self):
        self.ensure_one()
        self.line_ids.unlink()
        Line = self.env["bf.contact.dedup.line"]
        for group in matching.find_duplicate_groups(self.env, limit=200):
            partners = self.env["res.partner"].browse(group["partner_ids"]).exists()
            if len(partners) < 2:
                continue
            Line.create({
                "wizard_id": self.id,
                "key": group["key"],
                "reason": group["reason"],
                "partner_ids": [(6, 0, partners.ids)],
                "count": len(partners),
            })
        self.scanned = True
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }


class BfContactDedupLine(models.TransientModel):
    _name = "bf.contact.dedup.line"
    _description = "Groupe de doublons"
    _order = "count desc"

    wizard_id = fields.Many2one("bf.contact.dedup.wizard", ondelete="cascade")
    key = fields.Char(string="Clé")
    reason = fields.Char(string="Motif")
    count = fields.Integer(string="Nombre")
    partner_ids = fields.Many2many("res.partner", string="Contacts")

    def action_open_partners(self):
        """Open the duplicate set so the user can select + use the native
        Contacts "Fusionner" action from the list."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Doublons : %s") % self.key,
            "res_model": "res.partner",
            "view_mode": "list,form",
            "domain": [("id", "in", self.partner_ids.ids)],
            "target": "current",
        }

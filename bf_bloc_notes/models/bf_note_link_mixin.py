from odoo import api, fields, models


class BfNoteLinkMixin(models.AbstractModel):
    """Adds bf_note_count + open action to any model that has notes attached.

    Inherit on the target model:
        class ResPartner(models.Model):
            _inherit = ["res.partner", "bf.note.link.mixin"]

    The compute uses a single read_group → no N+1 on listviews/kanban.
    """

    _name = "bf.note.link.mixin"
    _description = "Mixin: smart button vers les notes liées"

    bf_note_count = fields.Integer(compute="_compute_bf_note_count")

    def _compute_bf_note_count(self):
        if not self.ids:
            for rec in self:
                rec.bf_note_count = 0
            return
        rows = self.env["bf.note.link"].sudo().read_group(
            [("res_model", "=", self._name), ("res_id", "in", self.ids)],
            ["res_id"],
            ["res_id"],
        )
        counts = {r["res_id"]: r["res_id_count"] for r in rows}
        for rec in self:
            rec.bf_note_count = counts.get(rec.id, 0)

    def action_open_bf_notes(self):
        self.ensure_one()
        Link = self.env["bf.note.link"]
        note_ids = Link.search([("res_model", "=", self._name), ("res_id", "=", self.id)]).mapped("note_id").ids
        return {
            "type": "ir.actions.act_window",
            "name": "Notes",
            "res_model": "bf.note",
            "view_mode": "kanban,list,form",
            "domain": [("id", "in", note_ids)],
            "context": {
                "default_link_ids": [(0, 0, {"res_model": self._name, "res_id": self.id})],
            },
        }

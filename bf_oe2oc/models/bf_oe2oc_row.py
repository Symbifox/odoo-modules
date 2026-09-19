# Part of bf_oe2oc. See LICENSE file for full copyright and licensing details.
"""One row of one Enterprise table, kept whether or not it found a home."""

from odoo import _, api, fields, models


class BfOe2ocRow(models.Model):
    _name = "bf.oe2oc.row"
    _description = "Rang Enterprise repris"
    _order = "table_id, source_id, id"

    table_id = fields.Many2one(
        "bf.oe2oc.table", required=True, ondelete="cascade", index=True,
        string="Table")
    bundle_id = fields.Many2one(
        related="table_id.bundle_id", store=True, index=True, string="Reprise")
    source_id = fields.Integer(
        string="Identifiant d'origine", index=True,
        help="La clé primaire du rang dans la base Enterprise.")
    payload = fields.Json(string="Valeurs d'origine")
    summary = fields.Char(compute="_compute_summary", string="Aperçu")

    state = fields.Selection(
        [("pending", "En attente"), ("rehomed", "Relogé"),
         ("skipped", "Sans objet"), ("failed", "Échec")],
        default="pending", required=True, index=True, string="État")
    target_id = fields.Integer(string="Identifiant créé", readonly=True)
    target_model = fields.Char(
        related="table_id.target_model", string="Modèle d'arrivée")
    message = fields.Text(string="Note", readonly=True)

    @api.depends("payload", "table_id.columns")
    def _compute_summary(self):
        for row in self:
            columns = row.table_id.columns or []
            values = row.payload or []
            pairs = []
            # Whatever reads like a label first, then fill up in column order.
            preferred = [c for c in ("name", "title", "subject", "display_name",
                                     "partner_name", "code") if c in columns]
            order = preferred + [c for c in columns if c not in preferred]
            for column in order:
                try:
                    value = values[columns.index(column)]
                except (ValueError, IndexError):
                    continue
                if value in (None, ""):
                    continue
                pairs.append(f"{column}={str(value)[:40]}")
                if len(pairs) >= 4:
                    break
            row.summary = " · ".join(pairs) or _("(rang vide)")

    def action_open_target(self):
        """Open the record this row became."""
        self.ensure_one()
        if not (self.target_id and self.target_model):
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": self.target_model,
            "res_id": self.target_id,
            "view_mode": "form",
        }


class BfOe2ocPreview(models.TransientModel):
    _name = "bf.oe2oc.preview"
    _description = "Aperçu du relogement"

    table_id = fields.Many2one("bf.oe2oc.table", string="Table", readonly=True)
    target_model = fields.Char(string="Modèle d'arrivée", readonly=True)
    body = fields.Text(string="Ce qui serait écrit", readonly=True)

    def action_rehome(self):
        self.ensure_one()
        self.table_id.action_rehome()
        return {"type": "ir.actions.act_window_close"}

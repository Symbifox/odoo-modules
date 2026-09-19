# Part of bf_oe2oc. See LICENSE file for full copyright and licensing details.
"""One Enterprise table, and what became of it."""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .handlers import handler_for

_logger = logging.getLogger(__name__)


class BfOe2ocTable(models.Model):
    _name = "bf.oe2oc.table"
    _description = "Table Enterprise reprise"
    _order = "row_count desc, name"

    bundle_id = fields.Many2one(
        "bf.oe2oc.bundle", required=True, ondelete="cascade", index=True,
        string="Reprise")
    name = fields.Char(string="Table Enterprise", required=True, index=True)
    columns = fields.Json(string="Colonnes")
    truncated = fields.Boolean(
        string="Tronquée à l'export",
        help="L'export a plafonné le nombre de rangs pour cette table.")

    row_ids = fields.One2many("bf.oe2oc.row", "table_id", string="Rangs repris")
    row_count = fields.Integer(compute="_compute_counts", store=True, string="Rangs")
    rehomed_count = fields.Integer(
        compute="_compute_counts", store=True, string="Relogés")

    # Un seul calcul pour un champ stocké et deux non stockés fait râler le
    # registre : lire le libellé recalculerait et réécrirait le stocké. Deux
    # méthodes, donc.
    target_model = fields.Char(
        compute="_compute_target_model", store=True, string="Modèle d'arrivée")
    target_label = fields.Char(
        compute="_compute_target_labels", string="Correspondance")
    caveat = fields.Text(
        compute="_compute_target_labels", string="Ce qui ne suit pas")
    target_available = fields.Boolean(
        compute="_compute_target_labels", string="Cible installée",
        help="Le module qui porte le modèle d'arrivée est-il installé ici ?")

    state = fields.Selection(
        [("kept", "Conservée"), ("rehomed", "Relogée"), ("partial", "Partielle"),
         ("failed", "Échec")],
        compute="_compute_state", store=True, string="État")
    message = fields.Text(string="Journal", readonly=True)

    _sql_constraints = [
        ("bundle_name_uniq", "unique(bundle_id, name)",
         "Une table ne peut figurer qu'une fois dans une reprise."),
    ]

    @api.depends("row_ids", "row_ids.state")
    def _compute_counts(self):
        for table in self:
            table.row_count = len(table.row_ids)
            table.rehomed_count = len(table.row_ids.filtered(
                lambda r: r.state == "rehomed"))

    @api.depends("name")
    def _compute_target_model(self):
        for table in self:
            handler = handler_for(table.name)
            table.target_model = handler.target_model if handler else False

    @api.depends("name")
    def _compute_target_labels(self):
        for table in self:
            handler = handler_for(table.name)
            table.target_label = handler.label if handler else False
            table.caveat = handler.caveat if handler else False
            table.target_available = bool(
                handler and handler.target_model in self.env)

    @api.depends("row_count", "rehomed_count", "target_model")
    def _compute_state(self):
        for table in self:
            if not table.target_model:
                table.state = "kept"
            elif table.rehomed_count == 0:
                table.state = "kept"
            elif table.rehomed_count < table.row_count:
                table.state = "partial"
            else:
                table.state = "rehomed"

    # ── Relogement ────────────────────────────────────────────────────────

    def action_preview(self):
        """Show what the first rows would become, without writing anything."""
        self.ensure_one()
        handler = self._handler()
        lang = self.env.context.get("lang") or "fr_CA"
        lines = []
        for row in self.row_ids[:10]:
            values, notes = handler.prepare(self.env, self.columns or [],
                                            row.payload or [], lang)
            shown = ", ".join(
                f"{k} = {str(v)[:60]}" for k, v in sorted(values.items()))
            lines.append(f"• #{row.source_id or '—'} → {shown or '(rien à écrire)'}")
            if notes:
                lines.append("    écarté : " + " ; ".join(notes))
        body = "\n".join(lines) or _("Aucun rang à montrer.")
        return {
            "type": "ir.actions.act_window",
            "name": _("Aperçu du relogement"),
            "res_model": "bf.oe2oc.preview",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_table_id": self.id,
                "default_body": body,
                "default_target_model": self.target_model,
            },
        }

    def action_rehome(self):
        """Create the target records. Rows already done are left alone."""
        for table in self:
            table._rehome()
        return True

    @api.private
    def _rehome(self):
        self.ensure_one()
        handler = self._handler()
        lang = self.env.context.get("lang") or "fr_CA"
        target = self.env[handler.target_model]
        created = failed = 0
        problems = []

        for row in self.row_ids.filtered(lambda r: r.state != "rehomed"):
            values, notes = handler.prepare(self.env, self.columns or [],
                                            row.payload or [], lang)
            if not values:
                row.write({"state": "skipped",
                           "message": _("Aucune valeur exploitable dans ce rang.")})
                continue
            # One savepoint per row: a single bad row must not cost the others.
            try:
                with self.env.cr.savepoint():
                    record = target.create(values)
                    handler.post_create(self.env, record, self.columns or [],
                                        row.payload or [])
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                failed += 1
                reason = str(exc).splitlines()[0][:200]
                problems.append(f"#{row.source_id or '—'} : {reason}")
                row.write({"state": "failed", "message": reason})
                continue
            created += 1
            row.write({
                "state": "rehomed",
                "target_id": record.id,
                "message": " ; ".join(notes) if notes else False,
            })

        summary = [_("%(n)s créés dans %(model)s.",
                     n=created, model=handler.target_model)]
        if failed:
            summary.append(_("%s en échec :", failed))
            summary.extend(problems[:20])
            if len(problems) > 20:
                summary.append(_("… et %s autres.", len(problems) - 20))
        self.message = "\n".join(summary)
        self.bundle_id.message_post(body=_(
            "Relogement de %(table)s : %(created)s créés, %(failed)s en échec.",
            table=self.name, created=created, failed=failed))

    @api.private
    def _handler(self):
        handler = handler_for(self.name)
        if not handler:
            raise UserError(_(
                "Aucune correspondance n'est définie pour « %s ». Ses rangs "
                "restent consultables ici, mais ce module ne sait pas où les "
                "écrire.", self.name))
        if handler.target_model not in self.env:
            raise UserError(_(
                "La correspondance vise le modèle « %(model)s », qui n'est pas "
                "installé. Installez le module qui le porte, puis reprenez.",
                model=handler.target_model))
        return handler

    def action_open_rows(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": "bf.oe2oc.row",
            "view_mode": "list,form",
            "domain": [("table_id", "=", self.id)],
            "context": {"default_table_id": self.id},
        }

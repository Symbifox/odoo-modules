# Part of bf_oe2oc. See LICENSE file for full copyright and licensing details.
"""The export file, once it is inside the instance it belongs to."""

import base64
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

EXPECTED_FORMAT = "odoo18-ee2ce/leftovers"
SUPPORTED_VERSIONS = (1,)
#: Past this, loading row by row stops being reasonable in a form. The cap is
#: generous: a real leftovers file is tickets and articles, not journal items.
MAX_ROWS = 50000


class BfOe2ocBundle(models.Model):
    _name = "bf.oe2oc.bundle"
    _description = "Reprise de données Odoo Enterprise"
    _inherit = ["mail.thread"]
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, tracking=True)
    file = fields.Binary(string="Fichier d'export", required=True, attachment=True)
    file_name = fields.Char(string="Nom du fichier")
    state = fields.Selection(
        [("draft", "Déposé"), ("loaded", "Lu"), ("done", "Traité")],
        default="draft", required=True, tracking=True, string="État")

    source_db = fields.Char(string="Base d'origine", readonly=True)
    source_version = fields.Char(string="Version d'origine", readonly=True)
    source_dump = fields.Char(string="Fichier source", readonly=True)
    generated_at = fields.Char(string="Exporté le", readonly=True)
    target_db = fields.Char(string="Base d'arrivée", readonly=True)

    table_ids = fields.One2many(
        "bf.oe2oc.table", "bundle_id", string="Tables reprises")
    table_count = fields.Integer(compute="_compute_counts", string="Tables")
    row_count = fields.Integer(compute="_compute_counts", string="Rangs")
    rehomed_count = fields.Integer(compute="_compute_counts", string="Relogés")
    mappable_count = fields.Integer(compute="_compute_counts", string="Relogeables")

    company_id = fields.Many2one(
        "res.company", string="Société", default=lambda self: self.env.company)

    @api.depends("table_ids", "table_ids.row_count", "table_ids.rehomed_count",
                 "table_ids.target_model")
    def _compute_counts(self):
        for bundle in self:
            tables = bundle.table_ids
            bundle.table_count = len(tables)
            bundle.row_count = sum(tables.mapped("row_count"))
            bundle.rehomed_count = sum(tables.mapped("rehomed_count"))
            bundle.mappable_count = len(tables.filtered("target_model"))

    # ── Lecture du fichier ────────────────────────────────────────────────

    def action_load(self):
        """Read the attached file and create one record per table and row."""
        for bundle in self:
            bundle._load()
        return True

    @api.private
    def _load(self):
        self.ensure_one()
        payload = self._parse()
        source = payload.get("source") or {}
        self.write({
            "source_db": source.get("db_name") or "",
            "source_version": source.get("version") or "",
            "source_dump": source.get("dump") or "",
            "generated_at": payload.get("generated_at") or "",
            "target_db": payload.get("target_db") or "",
        })
        self.table_ids.unlink()

        tables = payload.get("tables") or {}
        total = sum(len(t.get("rows") or []) for t in tables.values())
        if total > MAX_ROWS:
            raise UserError(_(
                "Ce fichier porte %(total)s rangs, au-delà de la limite de "
                "%(cap)s. Un export de reprise normal en compte quelques "
                "centaines : un fichier de cette taille veut probablement dire "
                "que des tables métier y sont tombées par erreur.",
                total=f"{total:,}".replace(",", " "), cap=f"{MAX_ROWS:,}".replace(",", " ")))

        Table = self.env["bf.oe2oc.table"]
        for name in sorted(tables, key=lambda n: (-len(tables[n].get("rows") or []), n)):
            spec = tables[name]
            Table.create({
                "bundle_id": self.id,
                "name": name,
                "columns": spec.get("columns") or [],
                "truncated": bool(spec.get("truncated")),
                "row_ids": [
                    (0, 0, {"payload": row, "source_id": self._source_id(spec, row)})
                    for row in (spec.get("rows") or [])
                ],
            })
        self.state = "loaded"
        self.message_post(body=_(
            "Fichier lu : %(tables)s tables, %(rows)s rangs.",
            tables=len(tables), rows=total))

    @api.private
    def _parse(self):
        """Decode and validate the attached JSON."""
        self.ensure_one()
        if not self.file:
            raise UserError(_("Aucun fichier n'est déposé sur cette reprise."))
        try:
            raw = base64.b64decode(self.file)
        except Exception as exc:
            raise UserError(_("Le fichier n'a pas pu être décodé : %s", exc)) from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise UserError(_(
                "Ce fichier n'est pas du JSON lisible. Attendu : le fichier "
                "« enterprise-leftovers.json » écrit par odoo18-ee2ce. "
                "Détail : %s", exc)) from exc
        if not isinstance(payload, dict):
            raise UserError(_("Ce fichier ne porte pas un objet JSON."))
        if payload.get("format") != EXPECTED_FORMAT:
            raise UserError(_(
                "Ce fichier annonce le format « %(got)s ». Ce module lit "
                "« %(want)s », écrit par la phase 9 d'odoo18-ee2ce.",
                got=payload.get("format") or "—", want=EXPECTED_FORMAT))
        version = payload.get("format_version")
        if version not in SUPPORTED_VERSIONS:
            raise UserError(_(
                "Version de format %(got)s non gérée : ce module lit %(want)s.",
                got=version, want=", ".join(str(v) for v in SUPPORTED_VERSIONS)))
        return payload

    @api.private
    def _source_id(self, spec, row):
        """The row's own primary key in the Enterprise database, if it had one."""
        columns = spec.get("columns") or []
        if "id" not in columns:
            return 0
        try:
            return int(row[columns.index("id")])
        except (TypeError, ValueError, IndexError):
            return 0

    # ── Relogement ────────────────────────────────────────────────────────

    def action_rehome_all(self):
        """Re-home every table that has a handler and has not been done yet."""
        self.ensure_one()
        pending = self.table_ids.filtered(
            lambda t: t.target_model and t.state != "rehomed")
        # Une correspondance peut viser un modèle que cette instance n'a pas
        # installé. Ce n'est pas une erreur du lot : c'est une table de moins
        # à traiter ici. La bloquer toutes pour une seule serait absurde.
        todo = pending.filtered("target_available")
        unavailable = pending - todo

        if not pending:
            raise UserError(_(
                "Aucune table en attente de relogement. Les tables sans "
                "correspondance restent consultables telles quelles."))
        if not todo:
            raise UserError(_(
                "Les %(n)s tables en attente visent des modèles qui ne sont pas "
                "installés ici : %(models)s. Installez les modules qui les "
                "portent, puis reprenez.",
                n=len(unavailable),
                models=", ".join(sorted(set(unavailable.mapped("target_model"))))))

        todo.action_rehome()
        if unavailable:
            self.message_post(body=_(
                "%(n)s tables laissées de côté, leur modèle d'arrivée n'étant "
                "pas installé : %(models)s.",
                n=len(unavailable),
                models=", ".join(sorted(set(unavailable.mapped("target_model"))))))
        remaining = self.table_ids.filtered(
            lambda t: t.target_model and t.target_available and t.state != "rehomed")
        if not remaining:
            self.state = "done"
        return True

    def action_open_tables(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Tables reprises"),
            "res_model": "bf.oe2oc.table",
            "view_mode": "list,form",
            "domain": [("bundle_id", "=", self.id)],
            "context": {"default_bundle_id": self.id},
        }

import base64
import logging
import traceback

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

KEEP_ARCHIVES = 3  # ZIP files kept per template; older runs keep their log only
# What a person may say when asking for an export. The rest (who asked, the
# outcome, the archive) is the export's to write: set by hand, ``attachment_id``
# would hand any file of the database to the pruning, which runs as superuser.
REQUEST_FIELDS = ("template_id", "document_ids")
# Written by the export alone, whoever asks: a reader set to the superuser would
# read past every rule, and an archive set by hand is deleted by the pruning.
EXPORT_FIELDS = ("template_id", "document_ids", "user_id", "trigger", "state", "date_start",
                 "date_end", "document_count", "file_count", "warning_count", "log",
                 "attachment_id", "delivery_state", "delivery_log")


class BfDocumentExportRun(models.Model):
    """One export of the registry: who asked, what came out, what went wrong."""

    _name = "bf.document.export.run"
    _description = "Registry export"
    _inherit = ["mail.thread"]
    _order = "id desc"

    name = fields.Char(compute="_compute_name", store=True)
    template_id = fields.Many2one(
        "bf.document.export.template", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="template_id.company_id", store=True)
    user_id = fields.Many2one(
        "res.users", string="Requested by", required=True, default=lambda self: self.env.user
    )
    trigger = fields.Selection(
        [("manual", "On demand"), ("release", "Publication")], required=True, default="manual"
    )
    document_ids = fields.Many2many(
        "project.document",
        "bf_document_export_run_document_rel",
        string="Selected documents",
        help="Leave empty to export every document the template covers.",
    )
    state = fields.Selection(
        [("queued", "Queued"), ("running", "Running"), ("done", "Done"), ("failed", "Failed")],
        required=True,
        default="queued",
        tracking=True,
    )
    date_start = fields.Datetime(readonly=True)
    date_end = fields.Datetime(readonly=True)
    document_count = fields.Integer(readonly=True)
    file_count = fields.Integer(readonly=True)
    warning_count = fields.Integer(readonly=True)
    log = fields.Text(readonly=True)
    attachment_id = fields.Many2one("ir.attachment", string="Archive", readonly=True)
    archive_name = fields.Char(related="attachment_id.name")
    delivery_state = fields.Selection(
        [
            ("none", "Not delivered"),
            ("done", "Delivered"),
            ("partial", "Delivered, with files left untouched"),
            ("failed", "Delivery failed"),
        ],
        string="Delivery",
        required=True,
        default="none",
        readonly=True,
        tracking=True,
        help="Set by the target bridges (Nextcloud, Microsoft 365, Google Drive). "
             "The archive above stays available whatever the delivery became.",
    )
    delivery_log = fields.Text(readonly=True)

    @api.depends("template_id", "create_date", "user_id")
    def _compute_name(self):
        for run in self:
            # Stamped in the requester's time zone, like the archive and the copy.
            local = run.with_context(tz=run.user_id.tz or self.env.user.tz or "UTC")
            stamp = fields.Datetime.context_timestamp(local, run.create_date or fields.Datetime.now())
            run.name = f"{run.template_id.name or ''} — {stamp.strftime('%Y-%m-%d %H:%M')}"

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            vals_list = [
                dict({key: vals[key] for key in REQUEST_FIELDS if key in vals},
                     user_id=self.env.uid, trigger="manual")
                for vals in vals_list
            ]
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and set(vals) & set(EXPORT_FIELDS):
            raise AccessError(self.env._(
                "An export's request and outcome are written by the export itself."))
        return super().write(vals)

    # ------------------------------------------------------------------ queue

    def _queue(self):
        cron = self.env.ref("bf_document_export.ir_cron_process_export_queue", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    @api.model
    def _cron_process_queue(self, limit=5):
        runs = self.sudo().search([("state", "=", "queued")], order="id", limit=limit)
        for run in runs:
            run._execute()
            self.env.cr.commit()

    def action_run_now(self):
        """Run a queued export in the current request, for small selections and tests."""
        for run in self.filtered(lambda r: r.state in ("queued", "failed")):
            # An old export asked for the superuser would read past every rule.
            if run.user_id._is_superuser() or not run.user_id.active:
                raise UserError(self.env._(
                    "This export was asked for the superuser or an inactive user: ask for it again."))
            run.sudo()._execute()
        return True

    def action_download(self):
        self.ensure_one()
        if not self.attachment_id:
            return False
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{self.attachment_id.id}?download=true",
            "target": "self",
        }

    # ------------------------------------------------------------------ work

    def _documents(self):
        """The documents to export, read with the requesting person's rights."""
        self.ensure_one()
        template = self.template_id
        Document = self.env["project.document"].with_user(self.user_id)
        domain = template._document_domain()
        if self.document_ids:
            domain = domain + [("id", "in", self.document_ids.ids)]
        return Document.search(domain).sudo()

    def _execute(self):
        self.ensure_one()
        self.write({"state": "running", "date_start": fields.Datetime.now(), "log": False,
                    "delivery_state": "none", "delivery_log": False})
        try:
            # The savepoint lets a failed export still record its failure.
            with self.env.cr.savepoint():
                documents = self._documents()
                content, manifest = self.template_id._build_archive(documents, run=self)
                local = self.with_context(tz=self.user_id.tz or self.env.user.tz or "UTC")
                stamp = fields.Datetime.context_timestamp(local, fields.Datetime.now()).strftime("%Y-%m-%d %H%M")
                root = self.template_id.root_name or "export"
                attachment = self.env["ir.attachment"].sudo().create({
                    "name": f"{root} - {stamp}.zip",
                    "datas": base64.b64encode(content),
                    "mimetype": "application/zip",
                    "res_model": self._name,
                    "res_id": self.id,
                })
                self.write({
                    "state": "done",
                    "date_end": fields.Datetime.now(),
                    "document_count": len(manifest["documents"]),
                    "file_count": manifest["file_count"],
                    "warning_count": len(manifest["warnings"]),
                    "log": "\n".join(manifest["warnings"]) or False,
                    "attachment_id": attachment.id,
                })
                self._prune_archives()
        except Exception:
            _logger.exception("Registry export %s failed", self.id)
            self.write({
                "state": "failed",
                "date_end": fields.Datetime.now(),
                "log": traceback.format_exc(limit=5),
            })
            self._notify(failed=True)
            return
        # The archive is built and kept: a target that is down does not take it away.
        try:
            with self.env.cr.savepoint():
                self._deliver(content, manifest)
        except Exception as error:
            _logger.exception("Delivery of registry export %s failed", self.id)
            self.write({"delivery_state": "failed", "delivery_log": str(error)})
        self._notify(failed=False)

    def _deliver(self, content, manifest):
        """Hook for target bridges (WebDAV, Microsoft Graph, Google Drive).

        A bridge sets ``delivery_state`` and ``delivery_log``. It raises only when
        nothing could be delivered; a file it could not write is logged instead,
        so that what it did write stays recorded.
        """
        return True

    def _prune_archives(self):
        older = self.search([
            ("template_id", "=", self.template_id.id),
            ("state", "=", "done"),
            ("attachment_id", "!=", False),
        ], order="id desc", offset=KEEP_ARCHIVES)
        for run in older:
            attachment = run.attachment_id
            run.attachment_id = False
            # Only the archive the export made: the field alone proves nothing.
            if attachment.res_model == self._name and attachment.res_id == run.id:
                attachment.unlink()

    def _notify(self, failed):
        partner = self.user_id.partner_id
        if failed:
            body = self.env._("The registry export « %s » failed. See the log on the export.", self.name)
        else:
            body = self.env._(
                "The registry export « %(name)s » is ready: %(docs)s documents, %(files)s files, "
                "%(warnings)s warnings.",
                name=self.name, docs=self.document_count, files=self.file_count,
                warnings=self.warning_count,
            )
        if not failed and self.delivery_state in ("partial", "failed"):
            body += " " + self.env._("Delivery: %s. See the delivery log on the export.",
                                     dict(self._fields["delivery_state"]._description_selection(self.env))
                                     [self.delivery_state])
        self.message_post(body=body, partner_ids=partner.ids, subtype_xmlid="mail.mt_note")

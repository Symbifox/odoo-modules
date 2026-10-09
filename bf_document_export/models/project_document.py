import logging
import os

from markupsafe import Markup

from odoo import api, fields, models
from odoo.tools import is_html_empty

_logger = logging.getLogger(__name__)


class ProjectDocument(models.Model):
    _inherit = "project.document"

    classification_ids = fields.Many2many(
        "project.document.classification",
        "project_document_classification_rel",
        "document_id",
        "classification_id",
        string="Classification",
        help="Where this document sits in each classification plan: its process, "
             "its records function, its compliance theme. Export templates that "
             "file by plan read it.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        documents = super().create(vals_list)
        documents._bf_check_linkable(documents.matrix_id)
        return documents

    def write(self, vals):
        # Per record: one document already linked to the matrix proves nothing for the others.
        if vals.get("matrix_id") and any(d.matrix_id.id != vals["matrix_id"] for d in self):
            self._bf_check_linkable(self.env["project.knowledge.matrix"].browse(vals["matrix_id"]))
        return super().write(vals)

    def _bf_check_linkable(self, records):
        """A person only links what they can read themselves.

        The export prints a document's matrix and files with the rights of the
        person it reads for, often a manager: linked by someone who cannot read
        them, they would reach the published copy through that person's rights.
        """
        if records and not self.env.su:
            if records._name == "ir.attachment":
                records.check("read")
            else:
                records.check_access("read")

    def _bf_export_readable(self, records, cache):
        """The part of ``records`` the export's reader may read.

        What is left out is given as the reason in ``cache``, for the exporter
        to report.
        """
        reader = (cache or {}).get("reader")
        if not records:
            return records
        if not reader:
            return records.browse()  # nobody to read for: nothing is read
        if records._name == "ir.attachment":
            allowed = records.with_user(reader)._filter_attachment_access(records.ids)
        else:
            allowed = records.with_user(reader)._filtered_access("read")
        allowed = records.browse(allowed.ids)
        if allowed != records:
            cache["reason"] = (self.env._(
                "an item of the document that %(reader)s cannot read was left out (%(what)s)",
                reader=reader.name, what=self.env["ir.model"]._get(records._name).name), False)
        return allowed

    def _bf_export_current_version(self):
        """The version in force: the released one, most recent first."""
        self.ensure_one()
        released = self.version_ids.filtered(lambda v: v.state == "released")
        return released.sorted(
            lambda v: (v.release_date or v.create_date, v.id), reverse=True
        )[:1]

    def _bf_export_external_file(self, version=None, cache=None):
        """Return (filename, bytes) for a document whose body is an external file.

        The core only knows files attached to the version. Bridges override this
        to fetch the live file from where it is kept (Nextcloud, for one), and
        fall back to super() when they have nothing.

        ``cache`` is shared by every document of one export. A bridge that
        cannot read a file sets ``cache["reason"] = (text, shared)``; ``shared``
        means the cause is not the file but its source (a configuration that
        cannot authenticate), and the exporter then reports it once.
        """
        self.ensure_one()
        version = version or self._bf_export_current_version()
        attachments = version.attachment_id | version.attachment_ids if version else False
        if attachments:
            attachments = self._bf_export_readable(attachments, cache)
        if attachments:
            attachment = attachments.sorted("id", reverse=True)[0]
            return attachment.name, attachment.raw
        return None

    def _bf_export_matrix_print_context(self):
        """Print context for a document whose body lives in a knowledge matrix.

        The matrix's own report is a progress table (item, status, assignee):
        exported as the document, it carried none of its text. Here each item
        with content becomes a section of the registry's document page, in the
        matrix's order. The matrix is not frozen at release, so the page says
        that its text is the one of the day of the export.
        """
        self.ensure_one()
        version = self.env["project.document.version"]
        if self.state != "draft":
            version = self._bf_export_current_version()
        rows = []
        items = self.matrix_id.item_ids.filtered(lambda i: i.state != "na")
        for item in items.sorted(lambda i: (i.sequence, i.decision_id or "", i.id)):
            body = item.content_html if not is_html_empty(item.content_html) else item.decision_text
            if is_html_empty(body):
                continue
            rows.append({"title": item.name, "code": item.decision_id, "html": Markup(body), "page_break": False})
        note = self.env._(
            "The text of this document lives in its knowledge matrix « %s »: it is printed as it "
            "stands on the day of the export.", self.matrix_id.name)
        return self._report_ctx("unfrozen", version, rows, note=note, **self._report_state_marks(version))

    def _bf_export_source_name(self):
        """The original file name of an external body, if known."""
        self.ensure_one()
        path = getattr(self, "nc_file_path", False) or ""
        return os.path.basename(path) if path else ""

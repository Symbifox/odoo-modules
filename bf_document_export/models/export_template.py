import base64
import hashlib
import html
import io
import json
import logging
import os
import re
import unicodedata
import zipfile
from urllib.parse import quote

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Characters refused by Windows, OneDrive and SharePoint in a file or folder name.
FORBIDDEN_CHARS = '"*:<>?/\\|'
RESERVED_NAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(10)}
    | {f"LPT{i}" for i in range(10)}
)
REPORT_VERSION = "project_knowledge_matrix.action_report_document_version_body"
REPORT_DRAFT = "project_knowledge_matrix.action_report_document_draft"
REPORT_MATRIX_BODY = "bf_document_export.action_report_document_matrix_body"


def clean_component(value):
    """Make one path component safe on Windows, SharePoint, Google Drive and Nextcloud."""
    value = unicodedata.normalize("NFC", value or "")
    # A tab or a line break separates words: keep a space, not nothing.
    value = re.sub(r"\s", " ", value)
    value = re.sub(r"\s+:\s+", " - ", value)
    value = "".join(
        "-" if char in FORBIDDEN_CHARS else char
        for char in value
        if char >= " " and char != "\x7f"
    )
    value = re.sub(r"\s+", " ", value).strip().rstrip(". ")
    if value.startswith("~$"):
        value = value[2:].lstrip()
    stem = value.split(".")[0].strip().upper()
    lowered = value.lower()
    if stem in RESERVED_NAMES or lowered in (".lock", "desktop.ini") or lowered.startswith("_vti_"):
        value = "_" + value
    return value or "_"


def clean_extension(name):
    """The extension of a file name, kept only when it is plain (« .pdf »)."""
    extension = os.path.splitext(name or "")[1].lower()
    return extension if re.fullmatch(r"\.[a-z0-9]{1,8}", extension) else ".bin"


def strip_code_prefix(title, code):
    """« POL-004 — Sécurité » becomes « Sécurité » when the code is already in the name."""
    if not code or not title:
        return title or ""
    pattern = r"^\s*" + re.escape(code) + r"\s*[-—–:.]?\s*"
    stripped = re.sub(pattern, "", title, count=1, flags=re.IGNORECASE)
    return stripped or title


class BfDocumentExportTemplate(models.Model):
    """How the registry is laid out as files: folders, filing rule, naming, content."""

    _name = "bf.document.export.template"
    _description = "Registry export template"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, copy=False)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    description = fields.Html(translate=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    lang = fields.Selection(
        selection="_lang_selection",
        string="Language of the tree",
        default=lambda self: self.env.lang or "fr_CA",
        help="Language used for the master list, the index and the readme.",
    )
    layout = fields.Selection(
        [
            ("type", "By document type"),
            ("classification", "By classification plan"),
            ("mirror", "Full mirror (transfer file)"),
        ],
        required=True,
        default="type",
    )
    scheme_id = fields.Many2one(
        "project.document.classification.scheme",
        string="Classification plan",
        help="Plan whose classifications decide the folder of each document.",
    )
    split_by_type = fields.Boolean(
        string="Type subfolders",
        help="Inside each classification folder, put each document type in its own subfolder.",
    )
    folder_ids = fields.One2many(
        "bf.document.export.folder", "template_id", string="Folders", copy=True
    )
    root_name = fields.Char(required=True, default="Politiques et procédures")
    unclassified_folder = fields.Char(required=True, default="98 - À classer")
    archive_folder = fields.Char(required=True, default="99 - Archives")
    drafts_folder = fields.Char(required=True, default="_Brouillons")
    master_list_name = fields.Char(required=True, default="00 - Liste maîtresse")
    readme_name = fields.Char(required=True, default="LISEZMOI")
    name_pattern = fields.Char(
        string="Current file name",
        required=True,
        default="{code} - {title}",
        help="Placeholders: {code}, {title}, {version}, {date}, {lang}.",
    )
    archive_name_pattern = fields.Char(
        string="Archived file name",
        required=True,
        default="{code} - {title} - v{version} - {date}",
    )
    max_path_length = fields.Integer(
        default=200,
        help="Longest path allowed, root included. SharePoint refuses beyond 400 "
             "characters and a Windows desktop without long paths beyond 260.",
    )
    document_scope = fields.Selection(
        [("internal", "Internal documents"), ("all", "Internal and client documents")],
        required=True,
        default="internal",
    )
    excluded_type_ids = fields.Many2many(
        "project.document.type",
        "bf_document_export_template_excluded_type_rel",
        string="Excluded types",
    )
    excluded_type_codes = fields.Char(
        string="Excluded type codes",
        help="Comma-separated type codes, for types created by hand without an identifier.",
    )
    confidential_type_ids = fields.Many2many(
        "project.document.type",
        "bf_document_export_template_confidential_type_rel",
        string="Confidential types",
        help="Registers and confidential documents may hold personal information (Law 25).",
    )
    confidential_mode = fields.Selection(
        [("exclude", "Leave out"), ("restricted", "Export, flagged restricted")],
        required=True,
        default="exclude",
    )
    drafts_mode = fields.Selection(
        [("exclude", "Leave out"), ("mark", "Export apart, marked as drafts")],
        required=True,
        default="exclude",
    )
    archives_mode = fields.Selection(
        [("none", "None"), ("superseded", "Superseded versions"), ("all", "Every frozen version")],
        required=True,
        default="none",
    )
    include_sections = fields.Boolean(
        help="Also write the body of documents written in Odoo, one HTML file per section.",
    )
    include_record = fields.Boolean(
        string="Include a record file",
        help="Also write each document's metadata and version history as JSON.",
    )
    index_xlsx = fields.Boolean(string="Master list (XLSX)", default=True)
    index_html = fields.Boolean(string="Browsable index (HTML)", default=True)
    index_json = fields.Boolean(string="Manifest (JSON)", default=True)
    deploy_mode = fields.Selection(
        [("manual", "On demand"), ("on_release", "On each publication")],
        string="Trigger",
        required=True,
        default="manual",
        help="On each publication, a new export is prepared after a version is "
             "released, and delivered to the template's target when it has one.",
    )
    user_id = fields.Many2one(
        "res.users",
        string="Export as",
        domain=[("share", "=", False)],
        help="Exports triggered by a publication read the registry with this "
             "person's rights and notify them. A target bridge only replaces the "
             "published copy with an export run as this person.",
    )
    run_ids = fields.One2many("bf.document.export.run", "template_id", string="Exports")
    last_run_id = fields.Many2one("bf.document.export.run", compute="_compute_last_run")

    _sql_constraints = [
        ("code_company_unique", "unique(code, company_id)", "A template code must be unique."),
    ]

    @api.model
    def _lang_selection(self):
        return self.env["res.lang"].get_installed()

    def _compute_last_run(self):
        for template in self:
            template.last_run_id = template.run_ids.filtered(
                lambda r: r.state == "done"
            ).sorted("id", reverse=True)[:1]

    @api.constrains("layout", "scheme_id")
    def _check_scheme(self):
        for template in self:
            if template.layout == "classification" and not template.scheme_id:
                raise ValidationError(
                    self.env._("A template that files by classification needs a plan.")
                )

    @api.constrains("user_id", "deploy_mode")
    def _check_export_as(self):
        for template in self:
            user = template.user_id
            if template.deploy_mode == "on_release" and not user:
                raise ValidationError(self.env._(
                    "A template that follows publications needs an « Export as » person."))
            if user and (user._is_superuser() or not user.active or user.share):
                raise ValidationError(self.env._(
                    "« Export as » must be an active internal user, not the superuser: the "
                    "export reads the registry with that person's rights."))

    def _bf_export_cache(self, reader):
        """State shared by the document hooks during one export.

        ``reader`` is the person the export reads for. What a document pulls in
        (a file, a matrix) is checked against that person's rights. Bridges add
        what they need.
        """
        return {"reader": reader}

    # ------------------------------------------------------------------ actions

    def action_export(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Export the registry"),
            "res_model": "bf.document.export.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_template_id": self.id},
        }

    def action_view_runs(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "bf_document_export.action_bf_document_export_run"
        )
        action["domain"] = [("template_id", "=", self.id)]
        action["context"] = {"default_template_id": self.id}
        return action

    # ------------------------------------------------------------------ scope

    def _excluded_types(self):
        self.ensure_one()
        codes = {c.strip() for c in (self.excluded_type_codes or "").split(",") if c.strip()}
        types = self.excluded_type_ids
        if codes:
            types |= self.env["project.document.type"].with_context(active_test=False).search(
                [("code", "in", list(codes))]
            )
        return types

    def _document_domain(self, for_release=False):
        """Documents this template covers. ``for_release`` drops the state test:
        a document published for the first time is still a draft at that moment."""
        self.ensure_one()
        states = ["active"]
        if self.drafts_mode == "mark":
            states.append("draft")
        domain = [("company_id", "=", self.company_id.id)]
        if not for_release:
            domain.append(("state", "in", states))
        if self.document_scope == "internal":
            domain.append(("is_internal", "=", True))
        excluded = self._excluded_types()
        if excluded:
            domain.append(("type_id", "not in", excluded.ids))
        if self.confidential_mode == "exclude" and self.confidential_type_ids:
            domain.append(("type_id", "not in", self.confidential_type_ids.ids))
        return domain

    # ------------------------------------------------------------------ filing

    def _folder_for(self, document):
        """Return (folder record or False, folder path parts) for a document."""
        self.ensure_one()
        if self.layout == "mirror":
            return False, [clean_component(document.type_id.display_name or "Autre")]
        if self.layout == "type":
            for folder in self.folder_ids.sorted("sequence"):
                if folder._matches_type(document.type_id):
                    return folder, [clean_component(folder.name)]
            return False, [clean_component(self.unclassified_folder)]
        # By classification plan: the first classification of the document in
        # this plan, then its nearest ancestor mapped to a folder.
        own = document.classification_ids.filtered(lambda c: c.scheme_id == self.scheme_id)
        for classification in own.sorted(lambda c: (c.complete_code, c.id)):
            ancestry = self.env["project.document.classification"].browse(
                [int(i) for i in classification.parent_path.strip("/").split("/")]
            )
            for folder in self.folder_ids.sorted("sequence"):
                if folder.classification_ids & ancestry:
                    parts = [clean_component(folder.name)]
                    if self.split_by_type:
                        parts.append(clean_component(document.type_id.display_name or "Autre"))
                    return folder, parts
        return False, [clean_component(self.unclassified_folder)]

    def _is_confidential(self, document):
        return document.type_id in self.confidential_type_ids

    # ------------------------------------------------------------------ naming

    def _file_name(self, document, version, extension, archive=False):
        self.ensure_one()
        pattern = self.archive_name_pattern if archive else self.name_pattern
        code = document.code or ""
        values = {
            "code": code,
            "title": strip_code_prefix(document.name, code),
            "version": (version.version_number if version else "") or "",
            "date": str((version.effective_date or (version.release_date and version.release_date.date())) or "")
            if version else "",
            "lang": document.language or "",
        }
        try:
            stem = pattern.format(**values)
        except (KeyError, IndexError, ValueError):
            stem = "{code} - {title}".format(**values)
        # An empty placeholder leaves « - - » or a lone « v » behind: drop those segments.
        segments = [s.strip() for s in stem.split(" - ")]
        stem = " - ".join(s for s in segments if s and s.lower() != "v")
        return clean_component(stem), extension

    # ------------------------------------------------------------------ preview

    def preview_filing(self):
        """Where each document would land, without rendering anything.

        Read with the caller's rights, by the same rules as the export: one row
        per document the template covers, and the documents its rules leave out,
        counted by reason. Used to fit a template to a registry before the first
        export (and callable over XML-RPC for that).
        """
        self.ensure_one()
        Document = self.env["project.document"]
        root = clean_component(self.root_name)
        limit = max(self.max_path_length or 0, 60)
        covered = Document.search(self._document_domain(), order="code, id")
        rows = []
        for document in covered:
            folder, parts = self._folder_for(document)
            draft = document.state == "draft"
            if draft:
                parts = [clean_component(self.drafts_folder)] + parts
            current = document._bf_export_current_version()
            if document.body_source == "internal" or document.matrix_id:
                extension = ".pdf"
            else:
                extension = clean_extension(document._bf_export_source_name() or "x.pdf")
            stem, _ext = self._file_name(document, current, extension)
            if self.layout == "mirror":
                parts = parts + [stem]
            path = "/".join([root] + parts + [stem + extension])
            has_source = (
                document.body_source == "internal" or bool(document.matrix_id)
                or bool(getattr(document, "nc_file_path", False))
                or bool(current and (current.attachment_id or current.attachment_ids))
            )
            rows.append({
                "id": document.id,
                "code": document.code or "",
                "title": document.name,
                "type": document.type_id.display_name or "",
                "state": document.state,
                "folder": "/".join(parts),
                "filed": bool(folder) or self.layout == "mirror",
                "file_name": stem + extension,
                "path_length": len(path),
                "too_long": len(path) > limit,
                "confidential": self._is_confidential(document) or bool(folder and folder.restricted),
                "body_source": document.body_source,
                "has_source": has_source,
                "classifications": [c.complete_code for c in document.classification_ids
                                    if not self.scheme_id or c.scheme_id == self.scheme_id],
            })
        others = Document.search([("company_id", "=", self.company_id.id), ("id", "not in", covered.ids)])
        excluded = self._excluded_types()
        left_out = {}
        for document in others:
            if self.document_scope == "internal" and not document.is_internal:
                reason = "client_document"
            elif document.type_id in excluded:
                reason = "excluded_type"
            elif self.confidential_mode == "exclude" and document.type_id in self.confidential_type_ids:
                reason = "confidential"
            else:
                reason = document.state  # draft (drafts left out) or archived
            left_out[reason] = left_out.get(reason, 0) + 1
        return {"template": self.code, "max_path_length": limit, "rows": rows, "left_out": left_out}

    # ------------------------------------------------------------------ build

    def _build_archive(self, documents, run=None):
        """Lay the documents out as files and return (zip bytes, manifest dict)."""
        self.ensure_one()
        builder = _ArchiveBuilder(self, run)
        # Type and classification names in the tree's language, not in the
        # language of whoever runs the export (the cron's user is in English).
        documents = documents.with_context(lang=self.lang or self.env.lang)
        for document in documents.sorted(lambda d: (d.code or "", d.id)):
            written = set(builder.files)
            try:
                # A savepoint keeps a database error on one document from
                # leaving the transaction unusable for the next ones.
                with self.env.cr.savepoint():
                    builder.add_document(document)
            except Exception as error:  # one broken document must not sink the export
                _logger.exception("Export of document %s failed", document.id)
                for path in set(builder.files) - written:
                    del builder.files[path]
                builder.unread.append(document.code or document.name)  # no withdrawal either
                builder.warn(document, self.env._("not exported: %s", str(error)))
        return builder.finish()


class BfDocumentExportFolder(models.Model):
    _name = "bf.document.export.folder"
    _description = "Registry export folder"
    _order = "template_id, sequence, id"

    template_id = fields.Many2one(
        "bf.document.export.template", required=True, ondelete="cascade", index=True
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, translate=True, help="Full folder name, prefix included.")
    type_ids = fields.Many2many(
        "project.document.type",
        "bf_document_export_folder_type_rel",
        string="Document types",
    )
    type_codes = fields.Char(
        help="Comma-separated type codes, matched as well as the types above. "
             "Reaches types created by hand without an identifier.",
    )
    classification_ids = fields.Many2many(
        "project.document.classification",
        "bf_document_export_folder_classification_rel",
        string="Classifications",
        help="A document lands here when one of its classifications, or one of "
             "their ancestors, is listed.",
    )
    restricted = fields.Boolean(
        help="Flagged restricted in the manifest and the readme: protect it once unpacked.",
    )

    def _matches_type(self, doc_type):
        self.ensure_one()
        if not doc_type:
            return False
        if doc_type in self.type_ids:
            return True
        codes = {c.strip().upper() for c in (self.type_codes or "").split(",") if c.strip()}
        return bool(doc_type.code and doc_type.code.upper() in codes)


class _ArchiveBuilder:
    """Accumulates files in memory, then writes the ZIP with its indexes."""

    def __init__(self, template, run):
        self.template = template
        self.env = template.env
        self.run = run
        self.root = clean_component(template.root_name)
        self.files = {}          # path inside the zip -> bytes
        self.entries = []        # manifest rows
        self.warnings = []
        self.restricted = set()
        # The person the export reads for: the requester, or the template's
        # « Export as » person for an export triggered by a publication.
        self.reader = run.user_id if run else template.env.user
        self.cache = template._bf_export_cache(self.reader)  # shared with the document hooks
        self.left_out = {}       # shared cause -> codes of the documents it kept out
        self.unread = []         # codes of the documents in scope whose body could not be read
        # path -> fingerprint of what a rendered file shows, export date aside.
        # Two renders of the same version differ (the PDF stamps its creation
        # time, the footer the day of the export): a target compares this
        # instead of the bytes, so an unchanged document is not written again.
        self.fingerprints = {}
        # Dates printed on the copy follow the time zone of the person who asked
        # for it, not the one of the server or of the cron's user.
        local = template.with_context(tz=(run and run.user_id.tz) or template.env.user.tz or "UTC")
        now = fields.Datetime.context_timestamp(local, fields.Datetime.now())
        self.export_date = now.strftime("%Y-%m-%d")
        self.generated = now.strftime("%Y-%m-%d %H:%M")

    # -- helpers -----------------------------------------------------------

    def _(self, text, *args, **kwargs):
        """Translate into the template's language. Named ``_`` on purpose: Odoo's
        term extractor only collects ``_()`` and ``_lt()`` calls in Python."""
        env = self.env(context=dict(self.env.context, lang=self.template.lang or self.env.lang))
        return env._(text, *args, **kwargs)

    def warn(self, document, message):
        label = (document.code or document.name) if document else ""
        line = f"{label} : {message}" if label else message
        # A document's current file and each of its archives may hit the same
        # limit: report it once per document.
        if line not in self.warnings:
            self.warnings.append(line)

    def _fit(self, parts, stem, extension, document):
        """Join the path, shortening the title when the path would be too long."""
        limit = max(self.template.max_path_length or 0, 60)
        prefix = "/".join([self.root] + parts) + "/"
        name = f"{stem}{extension}"
        if len(prefix) + len(name) > limit:
            room = limit - len(prefix) - len(extension)
            if room < 12:
                self.warn(document, self._("path too long even shortened, kept as is"))
            else:
                stem = clean_component(stem[:room].rstrip())
                name = f"{stem}{extension}"
                self.warn(document, self._("name shortened to keep the path under %s characters", limit))
        path = prefix + name
        counter = 2
        while path in self.files:
            path = f"{prefix}{stem} ({counter}){extension}"
            counter += 1
        if counter > 2:
            self.warn(document, self._("same name as another document, numbered"))
        return path

    def _put(self, path, content, fingerprint=None):
        self.files[path] = content if isinstance(content, bytes) else content.encode("utf-8")
        if fingerprint:
            self.fingerprints[path] = fingerprint
        return path

    def _footer(self, document, version=None, draft=False, dated=True):
        """The line an exported PDF carries on every page."""
        parts = [document.code or document.name]
        if draft:
            parts.append(self._("draft, not in force"))
        elif version:
            parts.append(f"v{version.version_number}")
            effective = version.effective_date or (version.release_date and version.release_date.date())
            if effective:
                parts.append(self._("effective %s", effective))
        parts.append(self._("Copy exported on %s: the version in force is in the registry.",
                             self.export_date if dated else "-"))
        return " · ".join(parts)

    def _render(self, report, record, document, version=None, draft=False):
        """Render a report with the export footer: (content, extension, fingerprint).

        The fingerprint hashes the report's HTML with an undated footer: stable
        from one export to the next, and changed by anything the page shows.
        """
        # The page reads the documents in the template's language, as the tree does,
        # whatever the language of the user running the export (the cron's, say).
        Report = self.env["ir.actions.report"].sudo().with_context(lang=self.template.lang or self.env.lang)
        footer = self._footer(document, version, draft)
        content, report_type = Report.with_context(bf_export_footer=footer)._render_qweb_pdf(
            report, res_ids=record.ids)
        html = Report.with_context(bf_export_footer=self._footer(document, version, draft, dated=False)
                                   )._render_qweb_html(report, record.ids)[0]
        fingerprint = hashlib.sha256(f"{report}|{self.template.lang}|".encode() + html).hexdigest()
        extension = ".pdf" if report_type == "pdf" else ".html"
        return content, extension, fingerprint

    # -- documents ---------------------------------------------------------

    def add_document(self, document):
        template = self.template
        folder, parts = template._folder_for(document)
        restricted = bool(folder and folder.restricted) or template._is_confidential(document)
        if not folder and template.layout != "mirror":
            if template.layout == "type":
                self.warn(document, self._("type « %s » has no folder", document.type_id.display_name or "?"))
            else:
                self.warn(document, self._("no classification in plan « %s »", template.scheme_id.display_name))
        draft = document.state == "draft"
        if draft:
            parts = [clean_component(template.drafts_folder)] + parts
        current = document._bf_export_current_version()
        if template.layout == "mirror":
            stem, _ext = template._file_name(document, current, "")
            parts = parts + [stem]
        main_path = self._write_current(document, current, parts, draft)
        if restricted:
            self.restricted.add("/".join([self.root] + parts[:1]))
        if main_path and (template.archives_mode != "none" or template.layout == "mirror"):
            self._write_archives(document, current, parts)
        if template.include_sections or template.layout == "mirror":
            self._write_sections(document, parts, current)
        if template.include_record or template.layout == "mirror":
            self._write_record(document, parts)
        if main_path:
            content = self.files[main_path]
            self.entries.append({
                "code": document.code or "",
                "title": document.name,
                "type": document.type_id.display_name or "",
                "state": document.state,
                "version": (current.version_number if current else "") or "",
                "effective_date": str(current.effective_date or "") if current else "",
                "review_date": str(document.review_date or ""),
                "owner": document.owner_id.name or "",
                "language": document.language or "",
                "classification": [c.complete_name for c in document.classification_ids],
                "confidential": restricted,
                "path": main_path,
                "format": os.path.splitext(main_path)[1].lstrip(".").lower(),
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            })

    def _write_current(self, document, current, parts, draft):
        template = self.template
        if document.body_source == "internal":
            if draft or not current:
                if not draft:
                    self.warn(document, self._("active without a released version, exported as a draft"))
                content, extension, fingerprint = self._render(REPORT_DRAFT, document, document, draft=True)
            else:
                content, extension, fingerprint = self._render(REPORT_VERSION, current, document, current)
            stem, _e = template._file_name(document, current, extension)
            if draft:
                stem = f"{stem} - {self._('DRAFT')}"
            return self._put(self._fit(parts, stem, extension, document), content, fingerprint)
        self.cache.pop("reason", None)
        found = document._bf_export_external_file(current, self.cache)
        fingerprint = None
        # A matrix the reader cannot read leaves the document out, its reason in the cache.
        if not found and document.matrix_id and document._bf_export_readable(document.matrix_id, self.cache):
            # The body lives in a knowledge matrix:
            # its items are printed as the document's sections. The matrix's own
            # report is a progress table, without the text.
            if document._bf_export_matrix_print_context()["sections"]:
                if not draft and not current:
                    self.warn(document, self._("active without a released version, exported as a draft"))
                content, extension, fingerprint = self._render(
                    REPORT_MATRIX_BODY, document, document, current, draft)
                found = (f"matrix{extension}", content)
            else:
                self.cache["reason"] = (self._(
                    "knowledge matrix « %s » has no text, document left out", document.matrix_id.name), False)
        if not found:
            # Not read this time is no withdrawal: a target keeps what it holds of it.
            self.unread.append(document.code or document.name)
            reason, shared = self.cache.pop("reason", (None, False))
            if shared:
                self.left_out.setdefault(reason, []).append(document.code or document.name)
            else:
                self.warn(document, reason or self._("source file not found, document left out"))
            return False
        filename, content = found
        extension = clean_extension(filename or document._bf_export_source_name())
        stem, _e = template._file_name(document, current, extension)
        if draft:
            stem = f"{stem} - {self._('DRAFT')}"
        return self._put(self._fit(parts, stem, extension, document), content, fingerprint)

    def _write_archives(self, document, current, parts):
        template = self.template
        states = ("superseded",) if template.archives_mode == "superseded" else ("superseded", "withdrawn", "released")
        if template.layout == "mirror":
            states = ("superseded", "withdrawn", "released")
        versions = document.version_ids.filtered(lambda v: v.state in states)
        if template.layout != "mirror":
            versions -= current
        for version in versions.sorted(lambda v: (v.sequence_index or 0, v.id)):
            if document.body_source == "internal":
                if not version.section_ids:
                    continue
                content, extension, fingerprint = self._render(REPORT_VERSION, version, document, version)
            else:
                fingerprint = None
                attachments = document._bf_export_readable(
                    version.attachment_id | version.attachment_ids, self.cache)
                if not attachments:
                    continue
                attachment = attachments.sorted("id", reverse=True)[0]
                content = attachment.raw
                extension = clean_extension(attachment.name)
            stem, _e = template._file_name(document, version, extension, archive=True)
            if template.layout == "mirror":
                target = parts + ["versions"]
            else:
                target = [clean_component(template.archive_folder)] + parts
            self._put(self._fit(target, stem, extension, document), content, fingerprint)

    def _write_sections(self, document, parts, current):
        # The sections of the version in force, frozen at its release. A draft, or
        # a document without a released version, gives its working copy, like its
        # main file (exported as a draft, and marked).
        sections = current.section_ids if current and document.state != "draft" else document.section_ids
        if document.body_source != "internal" or not sections:
            return
        target = parts + (["sections"] if self.template.layout == "mirror" else
                          [clean_component(f"{document.code} - sections")])
        for index, section in enumerate(sections.sorted(lambda s: (s.sequence, s.id)), 1):
            if section.content_kind != "html" or not section.content:
                continue
            body = (
                "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
                f"<title>{html.escape(section.name or '')}</title></head><body>"
                f"<h1>{html.escape(section.name or '')}</h1>{section.content}</body></html>"
            )
            stem = clean_component(f"{index:02d}-{section.code or 'SECTION'}")
            self._put(self._fit(target, stem, ".html", document), body)

    def _write_record(self, document, parts):
        record = {
            "code": document.code,
            "title": document.name,
            "type": document.type_id.display_name,
            "state": document.state,
            "language": document.language,
            "owner": document.owner_id.name,
            "author": document.author_id.name if "author_id" in document._fields else None,
            "body_source": document.body_source,
            "source_file": document._bf_export_source_name() or None,
            "review_interval_months": document.review_interval_months,
            "review_date": str(document.review_date or ""),
            "classification": [c.complete_name for c in document.classification_ids],
            "versions": [
                {
                    "number": v.version_number,
                    "state": v.state,
                    "change_type": v.change_type,
                    "summary": v.change_summary,
                    "changelog": v.changelog,
                    "effective_date": str(v.effective_date or ""),
                    "release_date": str(v.release_date or ""),
                }
                for v in document.version_ids.sorted(lambda v: (v.sequence_index or 0, v.id))
            ],
        }
        target = parts if self.template.layout == "mirror" else parts + [clean_component(f"{document.code} - fiche")]
        self._put(self._fit(target, "fiche", ".json", document),
                  json.dumps(record, ensure_ascii=False, indent=2, default=str))

    # -- indexes -----------------------------------------------------------

    def _headers(self):
        return [
            self._("Code"), self._("Title"), self._("Type"), self._("Version"),
            self._("Effective date"), self._("Next review"), self._("Owner"),
            self._("Language"), self._("Classification"), self._("Restricted"), self._("Path"),
        ]

    def _row(self, entry):
        return [
            entry["code"], entry["title"], entry["type"], entry["version"],
            entry["effective_date"], entry["review_date"], entry["owner"], entry["language"],
            "; ".join(entry["classification"]), self._("yes") if entry["confidential"] else "",
            entry["path"][len(self.root) + 1:],
        ]

    def _master_list(self):
        import xlsxwriter
        stream = io.BytesIO()
        # A title is text: « =… » must not become a formula, nor « http… » a link.
        book = xlsxwriter.Workbook(stream, {"in_memory": True, "strings_to_formulas": False,
                                            "strings_to_urls": False})
        sheet = book.add_worksheet(self._("Documents")[:31])
        bold = book.add_format({"bold": True, "bg_color": "#E6EDF3", "border": 1})
        for col, title in enumerate(self._headers()):
            sheet.write(0, col, title, bold)
        for row, entry in enumerate(sorted(self.entries, key=lambda e: e["path"]), 1):
            for col, value in enumerate(self._row(entry)):
                # write_string: « {=…} » would still become an array formula.
                sheet.write_string(row, col, str(value))
        sheet.freeze_panes(1, 0)
        sheet.autofilter(0, 0, max(len(self.entries), 1), len(self._headers()) - 1)
        for col, width in enumerate((14, 60, 22, 9, 14, 14, 22, 9, 40, 10, 80)):
            sheet.set_column(col, col, width)
        book.close()
        return stream.getvalue()

    def _index_html(self, generated):
        rows = []
        for entry in sorted(self.entries, key=lambda e: e["path"]):
            relative = entry["path"][len(self.root) + 1:]
            link = "/".join(quote(part) for part in relative.split("/"))
            cells = self._row(entry)
            cells[1] = f'<a href="{link}">{html.escape(entry["title"])}</a>'
            rows.append("<tr>" + "".join(
                f"<td>{cell if i == 1 else html.escape(str(cell))}</td>" for i, cell in enumerate(cells)
            ) + "</tr>")
        headers = "".join(f"<th>{html.escape(h)}</th>" for h in self._headers())
        title = html.escape(self.template.root_name)
        notice = html.escape(self._(
            "Copy exported on %s. The version in force is the one in the registry.", generated))
        return (
            "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
            f"<title>{title}</title><style>"
            "body{font-family:sans-serif;margin:2em}table{border-collapse:collapse;width:100%}"
            "th,td{border:1px solid #ccc;padding:4px 8px;text-align:left;font-size:13px}"
            "th{background:#E6EDF3}</style></head><body>"
            f"<h1>{title}</h1><p>{notice}</p><table><thead><tr>{headers}</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></body></html>"
        )

    def _readme(self, generated):
        template = self.template
        lines = [
            template.root_name,
            "",
            self._("Copy exported on %s from the registry of %s.", generated, template.company_id.name),
            self._("The version in force is the one in the registry: this copy is not authoritative."),
            self._("Template: %s", template.name),
            self._("Documents: %s", len(self.entries)),
            "",
        ]
        if self.restricted:
            lines.append(self._("Restricted folders, to protect once unpacked:"))
            lines += [f"  - {path[len(self.root) + 1:]}" for path in sorted(self.restricted)]
            lines.append("")
        if self.warnings:
            lines.append(self._("Warnings:"))
            lines += [f"  - {warning}" for warning in self.warnings]
        return "\n".join(lines) + "\n"

    def finish(self):
        template = self.template
        for reason, codes in self.left_out.items():
            shown = ", ".join(codes[:5]) + (" …" if len(codes) > 5 else "")
            self.warnings.append(self._("%(reason)s: %(count)s documents left out (%(codes)s)",
                                         reason=reason, count=len(codes), codes=shown))
        generated = self.generated
        manifest = {
            "generated_at": generated,
            "template": template.code,
            "company": template.company_id.name,
            "documents": self.entries,
            "restricted_folders": sorted(self.restricted),
            "warnings": self.warnings,
        }
        if template.index_xlsx:
            self._put(f"{self.root}/{clean_component(template.master_list_name)}.xlsx", self._master_list())
        if template.index_html:
            self._put(f"{self.root}/index.html", self._index_html(generated))
        if template.index_json:
            self._put(f"{self.root}/manifest.json",
                      json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
        self._put(f"{self.root}/{clean_component(template.readme_name)}.txt", self._readme(generated))
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(self.files):
                archive.writestr(path, self.files[path])
        manifest["file_count"] = len(self.files)
        manifest["fingerprints"] = dict(self.fingerprints)
        # Documents in scope whose body could not be read this time: a target must
        # not take their absence for a withdrawal.
        manifest["unread_documents"] = list(self.unread)
        return stream.getvalue(), manifest

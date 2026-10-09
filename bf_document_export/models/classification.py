from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ProjectDocumentClassificationScheme(models.Model):
    """A classification plan: one way of cutting the registry into domains.

    A document can sit in several plans at once (a process, a records function,
    an ISO/IEC 27001 control theme). Each export template picks the plan it
    files by.
    """

    _name = "project.document.classification.scheme"
    _description = "Document classification plan"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    kind = fields.Selection(
        [
            ("process", "Process (ISO 9001 §4.4)"),
            ("function", "Records function (ISO 15489)"),
            ("compliance", "Compliance framework"),
            ("other", "Other"),
        ],
        required=True,
        default="other",
    )
    description = fields.Text(translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    classification_ids = fields.One2many(
        "project.document.classification", "scheme_id", string="Classifications"
    )
    classification_count = fields.Integer(compute="_compute_classification_count")

    _sql_constraints = [
        ("code_unique", "unique(code)", "A classification plan code must be unique."),
    ]

    @api.depends("classification_ids")
    def _compute_classification_count(self):
        for scheme in self:
            scheme.classification_count = len(scheme.classification_ids)


class ProjectDocumentClassification(models.Model):
    """One domain inside a classification plan, possibly nested."""

    _name = "project.document.classification"
    _description = "Document classification"
    _parent_store = True
    _order = "scheme_id, complete_code, sequence, name"
    _rec_name = "complete_name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    scheme_id = fields.Many2one(
        "project.document.classification.scheme",
        string="Plan",
        required=True,
        ondelete="cascade",
        index=True,
    )
    parent_id = fields.Many2one(
        "project.document.classification",
        string="Parent",
        index=True,
        ondelete="cascade",
        domain="[('scheme_id', '=', scheme_id)]",
    )
    child_ids = fields.One2many("project.document.classification", "parent_id")
    parent_path = fields.Char(index=True, unaccent=False)
    complete_name = fields.Char(compute="_compute_complete", store=True, recursive=True)
    complete_code = fields.Char(compute="_compute_complete", store=True, recursive=True)
    description = fields.Text(translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    document_ids = fields.Many2many(
        "project.document",
        "project_document_classification_rel",
        "classification_id",
        "document_id",
        string="Documents",
    )
    document_count = fields.Integer(compute="_compute_document_count")

    _sql_constraints = [
        (
            "code_scheme_unique",
            "unique(scheme_id, code)",
            "A classification code must be unique within its plan.",
        ),
    ]

    @api.depends("name", "code", "parent_id.complete_name", "parent_id.complete_code")
    def _compute_complete(self):
        for rec in self:
            label = f"{rec.code} - {rec.name}" if rec.code else rec.name
            if rec.parent_id:
                rec.complete_name = f"{rec.parent_id.complete_name} / {label}"
                rec.complete_code = f"{rec.parent_id.complete_code}/{rec.code}"
            else:
                rec.complete_name = label
                rec.complete_code = rec.code or ""

    @api.depends("document_ids")
    def _compute_document_count(self):
        for rec in self:
            rec.document_count = len(rec.document_ids)

    @api.constrains("parent_id", "scheme_id")
    def _check_parent_scheme(self):
        for rec in self:
            if rec.parent_id and rec.parent_id.scheme_id != rec.scheme_id:
                raise ValidationError(
                    self.env._("A classification and its parent must belong to the same plan.")
                )
        if self._has_cycle():
            raise ValidationError(self.env._("A classification cannot be its own ancestor."))

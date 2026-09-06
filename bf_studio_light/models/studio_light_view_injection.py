import logging
import re

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# 'replace' deliberately omitted — too dangerous, replaces core elements.
POSITION_CHOICES = [
    ("after", "After target"),
    ("before", "Before target"),
    ("inside", "Inside target (append)"),
]

VIEW_TYPE_CHOICES = [
    ("form", "Form"),
    ("list", "List"),
    ("kanban", "Kanban"),
    ("search", "Search"),
]

# Identifier regex for target_field — refuses anything but a valid
# Python/Odoo field name.
FIELD_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Whitelist of XML tags allowed in arch_snippet. Anything else is
# refused — `<button>` and `<header>` are deliberately excluded to
# prevent injection of action calls into the form view.
ALLOWED_ARCH_TAGS = frozenset(
    {
        "field",
        "group",
        "newline",
        "separator",
        "label",
        "div",
        "span",
        "page",
        "notebook",
        "filter",
        "searchpanel",
    }
)

# Attributes we never want on injected elements (action handlers,
# raw HTML, JS hooks).
FORBIDDEN_ARCH_ATTRS = frozenset(
    {
        "on_change",
        "onchange",
        "data-action",
        "data-method",
        "t-on-click",
        "t-att-onclick",
        "t-call",
        "t-esc",
        "t-raw",
    }
)


# Tags allowed *only* when the injection is owned by a trusted source
# (currently: a studio.light.smart.button row). This is how smart buttons
# get to inject a <widget> element without opening that escape hatch to
# every admin-typed arch_snippet.
TRUSTED_ARCH_EXTRA_TAGS = frozenset({"widget"})


def _validate_arch_snippet(snippet, allow_trusted=False):
    """Parse the snippet and ensure every element is on the whitelist
    and carries no forbidden attribute. Raises ValidationError on issue.

    When ``allow_trusted`` is True the whitelist is broadened with
    ``TRUSTED_ARCH_EXTRA_TAGS``. Callers must verify the trusted bit via
    *both* a context flag and a verified back-pointer — see
    ``StudioLightViewInjection._check_arch_snippet``.
    """
    if not snippet:
        return
    allowed = ALLOWED_ARCH_TAGS | (
        TRUSTED_ARCH_EXTRA_TAGS if allow_trusted else frozenset()
    )
    # Wrap in a root so multi-element snippets parse.
    try:
        root = etree.fromstring(f"<root>{snippet}</root>")
    except etree.XMLSyntaxError as e:
        raise ValidationError(_("Arch snippet is not valid XML: %s") % e)
    for el in root.iter():
        if el.tag == "root":
            continue
        if not isinstance(el.tag, str):
            # comments/CDATA — refuse outright
            raise ValidationError(_("Comments and CDATA are not allowed."))
        if el.tag not in allowed:
            raise ValidationError(
                _(
                    "Element <%s> is not allowed in arch snippets. Allowed: %s"
                )
                % (el.tag, ", ".join(sorted(allowed)))
            )
        for attr in el.attrib:
            if attr in FORBIDDEN_ARCH_ATTRS:
                raise ValidationError(
                    _("Attribute %s is not allowed on injected arch.") % attr
                )


class StudioLightViewInjection(models.Model):
    """Persisted view inheritance for Forge fields.

    Each row generates one ``ir.ui.view`` (mode='extension'). On uninstall
    of the parent module the ir.ui.view may disappear; we recreate it from
    the stored xpath and arch.
    """

    _name = "studio.light.view.injection"
    _description = "Forge — View injection"
    _order = "model_name, view_type, sequence"

    name = fields.Char(required=True, default="Forge injection")
    studio_field_id = fields.Many2one(
        "studio.light.field",
        ondelete="cascade",
        index=True,
        help="Field this injection exposes (optional — free-form arch is allowed too).",
    )
    model_id = fields.Many2one(
        "ir.model", required=True, ondelete="cascade", index=True
    )
    model_name = fields.Char(related="model_id.model", store=True, index=True)
    view_type = fields.Selection(VIEW_TYPE_CHOICES, required=True, default="form")
    parent_view_id = fields.Many2one(
        "ir.ui.view",
        string="Parent view",
        help="If empty, the primary view of the model+type is used.",
    )

    # Targeting
    target_field = fields.Char(
        help="Name of the existing field to anchor on, e.g. 'email'.",
    )
    custom_xpath = fields.Char(
        help="Override xpath. If set, takes precedence over target_field. "
        "Restricted to simple //field[@name=...] / //group[@name=...] / "
        "/list / /search / //templates patterns.",
    )
    position = fields.Selection(POSITION_CHOICES, default="after", required=True)

    # Content
    arch_snippet = fields.Text(
        help="Raw XML to inject. Restricted to a whitelist of safe elements "
        "(no <button>, no t-* directives, no onchange).",
    )

    sequence = fields.Integer(default=20)
    active = fields.Boolean(default=True)
    failed_count = fields.Integer(
        default=0, readonly=True, help="Consecutive integrity-recovery failures."
    )
    last_failure_message = fields.Char(readonly=True)
    ir_view_id = fields.Many2one(
        "ir.ui.view", string="Generated view", readonly=True, ondelete="set null"
    )

    # Back-pointer set when this injection is owned by a smart-button
    # record. The trusted-arch escape (which lets <widget> through the
    # whitelist) only applies when this M2o is set AND the
    # ``studio_light_trusted_arch`` context flag is present at
    # create/write time. Keep both checks — neither alone is sufficient.
    studio_smart_button_id = fields.Many2one(
        "studio.light.smart.button",
        string="Owning smart button",
        ondelete="cascade",
        index=True,
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    # `model_id` is in the trigger list so the constraint fires on
    # every create even when the user leaves both target_field and
    # custom_xpath empty — without it, @api.constrains skips the check
    # entirely (see Odoo's "constrains is lazy on create" behaviour).
    @api.constrains("target_field", "custom_xpath", "model_id")
    def _check_anchor(self):
        for rec in self:
            if not rec.target_field and not rec.custom_xpath:
                raise ValidationError(
                    _("Specify either a target field or a custom xpath.")
                )
            if rec.target_field and not FIELD_IDENT_RE.match(rec.target_field):
                raise ValidationError(
                    _(
                        "target_field %r is not a valid identifier. "
                        "Use a field name like 'email'."
                    )
                    % rec.target_field
                )
            if rec.custom_xpath:
                # Must compile as XPath, AND be limited to a small grammar
                # of safe expressions. We refuse logical operators and union
                # operators that could broaden the match unexpectedly.
                xp = rec.custom_xpath.strip()
                forbidden_tokens = (" or ", " and ", "|", "::", "*[", "//*")
                for tok in forbidden_tokens:
                    if tok in xp:
                        raise ValidationError(
                            _(
                                "custom_xpath contains forbidden token %r. "
                                "Only simple element-path predicates allowed."
                            )
                            % tok
                        )
                try:
                    etree.XPath(xp)
                except etree.XPathSyntaxError as e:
                    raise ValidationError(
                        _("custom_xpath did not compile: %s") % e
                    )

    @api.constrains("arch_snippet", "studio_smart_button_id")
    def _check_arch_snippet(self):
        for rec in self:
            allow_trusted = bool(
                rec.studio_smart_button_id
                and self.env.context.get("studio_light_trusted_arch")
            )
            _validate_arch_snippet(
                rec.arch_snippet or "", allow_trusted=allow_trusted
            )

    # ------------------------------------------------------------------
    # Build the inheriting view
    # ------------------------------------------------------------------
    def _resolve_parent_view(self):
        self.ensure_one()
        if self.parent_view_id:
            return self.parent_view_id
        view = self.env["ir.ui.view"].search(
            [
                ("model", "=", self.model_name),
                ("type", "=", self.view_type),
                ("inherit_id", "=", False),
                ("mode", "=", "primary"),
            ],
            order="priority,id",
            limit=1,
        )
        if not view:
            raise UserError(
                _("No primary %s view found for model %s.")
                % (self.view_type, self.model_name)
            )
        return view

    def _build_xpath(self):
        self.ensure_one()
        if self.custom_xpath:
            return self.custom_xpath
        # Quoting via etree's f-string is safe here because target_field
        # has been regex-validated to be an identifier.
        return f"//field[@name='{self.target_field}']"

    def _build_inner_arch(self):
        self.ensure_one()
        if self.arch_snippet:
            return self.arch_snippet.strip()
        if self.studio_field_id:
            # studio_field_id.name has been regex-validated server-side.
            # `image` fields land in ir.model.fields as ttype='binary' but
            # need widget="image" to render as a thumbnail rather than a
            # download link.
            extras = []
            sf = self.studio_field_id
            if sf.field_type == "image":
                extras.append('widget="image"')
            # Conditional modifiers (Tier A7). Each expression has been
            # AST-validated server-side; emit verbatim so Odoo's view
            # engine evaluates it at render time. XML-escape the value
            # to keep `<` / `>` / `&` operators safe in the arch.
            from xml.sax.saxutils import quoteattr
            for attr_name, expr in (
                ("invisible", sf.invisible_expr),
                ("required", sf.required_expr),
                ("readonly", sf.readonly_expr),
            ):
                if expr and expr.strip():
                    extras.append(f"{attr_name}={quoteattr(expr.strip())}")
            extra = (" " + " ".join(extras)) if extras else ""
            return f'<field name="{sf.name}"{extra}/>'
        raise UserError(
            _("Either provide arch_snippet or link a studio_field_id.")
        )

    def _build_full_arch(self):
        self.ensure_one()
        xpath = self._build_xpath()
        inner = self._build_inner_arch()
        arch = (
            f"<data>"
            f'<xpath expr="{xpath}" position="{self.position}">'
            f"{inner}"
            f"</xpath>"
            f"</data>"
        )
        # Validate XML well-formedness AND xpath compileability.
        try:
            etree.fromstring(arch)
        except etree.XMLSyntaxError as e:
            raise ValidationError(_("Generated arch is not valid XML: %s") % e)
        try:
            etree.XPath(xpath)
        except etree.XPathSyntaxError as e:
            raise ValidationError(_("Generated xpath is invalid: %s") % e)
        return arch

    def _ensure_ir_view(self):
        """Create or refresh the ir.ui.view; return it."""
        Views = self.env["ir.ui.view"].sudo()
        for rec in self:
            parent = rec._resolve_parent_view()
            arch = rec._build_full_arch()
            vals = {
                "name": f"studio_light.{rec.model_name}.{rec.view_type}.{rec.id}",
                "model": rec.model_name,
                "type": rec.view_type,
                "inherit_id": parent.id,
                "mode": "extension",
                "arch": arch,
                "active": rec.active,
                "priority": 99,
            }
            view = rec.ir_view_id
            if view and not view.exists():
                view = False
                rec.ir_view_id = False
            if view:
                view.write(vals)
            else:
                view = Views.create(vals)
                rec.ir_view_id = view.id
                _logger.info(
                    "Forge: created ir.ui.view for %s on %s",
                    rec.model_name,
                    parent.name,
                )
        return self.mapped("ir_view_id")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._ensure_ir_view()
        return records

    def write(self, vals):
        res = super().write(vals)
        rebuild_keys = {
            "model_id",
            "view_type",
            "parent_view_id",
            "target_field",
            "custom_xpath",
            "position",
            "arch_snippet",
            "studio_field_id",
            "active",
        }
        # `studio_light_skip_provision` lets the failure-backoff path
        # write `active=False` without re-entering `_ensure_ir_view` —
        # otherwise the same error that triggered the deactivation
        # would re-fire and the auto-deactivation would never persist.
        if (
            any(k in vals for k in rebuild_keys)
            and not self.env.context.get("studio_light_skip_provision")
        ):
            self._ensure_ir_view()
        return res

    def unlink(self):
        for rec in self:
            if rec.ir_view_id:
                try:
                    rec.ir_view_id.sudo().unlink()
                except Exception as e:
                    _logger.warning(
                        "Forge: could not unlink ir.ui.view %s: %s",
                        rec.ir_view_id.id,
                        e,
                    )
        return super().unlink()

    # ------------------------------------------------------------------
    # Integrity (called from hook + cron)
    # ------------------------------------------------------------------
    @api.model
    def _ensure_all_provisioned(self):
        for rec in self.search([("active", "=", True)]):
            try:
                rec._ensure_ir_view()
                rec.failed_count = 0
                rec.last_failure_message = False
            except Exception as e:
                rec.failed_count = (rec.failed_count or 0) + 1
                rec.last_failure_message = str(e)[:512]
                _logger.error(
                    "Forge: integrity provisioning failed for view "
                    "injection %s (attempt %s): %s",
                    rec.id,
                    rec.failed_count,
                    e,
                )
                if rec.failed_count >= 3:
                    rec.with_context(
                        studio_light_skip_provision=True
                    ).active = False
                    _logger.warning(
                        "Forge: auto-deactivated view injection %s "
                        "after 3 failures",
                        rec.id,
                    )
        self._cleanup_orphan_views()

    @api.model
    def _cleanup_orphan_views(self):
        """Drop ``ir.ui.view`` rows we generated whose injection record
        no longer claims them.

        ``unlink()`` on this model swallows view-unlink errors (so a
        broken parent view doesn't block deleting the injection), which
        can leave orphan rows behind. The cron picks them up on its
        next run and removes them so they don't keep affecting the
        combined arch."""
        Views = self.env["ir.ui.view"].sudo()
        claimed_ids = set(
            self.with_context(active_test=False).search([])
            .mapped("ir_view_id").ids
        )
        candidates = Views.search([("name", "=like", "studio_light.%")])
        orphans = candidates.filtered(lambda v: v.id not in claimed_ids)
        if not orphans:
            return
        _logger.info(
            "Forge: cleaning %s orphan ir.ui.view rows: %s",
            len(orphans), orphans.ids,
        )
        try:
            orphans.unlink()
        except Exception as e:
            _logger.warning(
                "Forge: orphan view cleanup failed: %s", e,
            )

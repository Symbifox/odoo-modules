import ast
import logging
import re
from xml.sax.saxutils import quoteattr

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .studio_light_field import (
    PATH_PART_RE,
    SENSITIVE_FIELD_NAMES,
    is_model_locked,
)

_logger = logging.getLogger(__name__)

# Domain operators we accept on smart-button extra-domain clauses. Kept
# narrow on purpose: the count and the action both apply this domain, so
# anything that broadens scope or invokes server-side magic is refused.
DOMAIN_OPERATOR_WHITELIST = frozenset(
    {
        "=",
        "!=",
        ">",
        "<",
        ">=",
        "<=",
        "in",
        "not in",
        "like",
        "ilike",
        "not like",
        "not ilike",
        "=ilike",
        "=?",
    }
)

ICON_RE = re.compile(r"^fa-[a-z0-9-]{1,40}$")
LABEL_FORBIDDEN_TOKENS = ("<", ">", "&#")

COLOR_CHOICES = [
    ("text-primary", "Primary (blue)"),
    ("text-success", "Success (green)"),
    ("text-info", "Info (cyan)"),
    ("text-warning", "Warning (orange)"),
    ("text-danger", "Danger (red)"),
    ("text-muted", "Muted (gray)"),
]


class StudioLightSmartButton(models.Model):
    """Persistent definition of a smart button injected on a form view.

    A smart button counts records on a target model that point back to the
    form's record via a Many2one relation field, then opens an act_window
    showing those records. Both the count (via JSON controller) and the
    action (static dict) are computed without generating any Python code,
    so the surface stays free of RCE risk.

    The actual rendering goes through one (sometimes two) ``studio.light.
    view.injection`` rows owned by this record. The injection's
    ``arch_snippet`` whitelist normally refuses ``<widget>``; this model
    is allowed past that whitelist via a context flag *and* a verified
    back-pointer (see ``_check_arch_snippet`` in the injection model).
    """

    _name = "studio.light.smart.button"
    _description = "Studio Light — Smart button"
    _order = "source_model_name, sequence, id"

    name = fields.Char(required=True, default="Smart button")
    sequence = fields.Integer(default=10)

    source_model_id = fields.Many2one(
        "ir.model",
        required=True,
        ondelete="cascade",
        index=True,
        string="Source model",
        domain="[('transient', '=', False)]",
        help="The model whose form view will receive the button.",
    )
    source_model_name = fields.Char(
        related="source_model_id.model", store=True, index=True
    )

    target_model_id = fields.Many2one(
        "ir.model",
        required=True,
        ondelete="cascade",
        index=True,
        string="Target model",
        domain="[('transient', '=', False)]",
        help="The related model whose records are counted.",
    )
    target_model_name = fields.Char(
        related="target_model_id.model", store=True, index=True
    )

    relation_field_id = fields.Many2one(
        "ir.model.fields",
        required=True,
        ondelete="cascade",
        string="Relation field on target",
        domain="[('model_id', '=', target_model_id), ('ttype', '=', 'many2one')]",
        help=(
            "The Many2one field on the target model that points back to a "
            "record of the source model."
        ),
    )

    domain = fields.Char(
        string="Extra domain (optional)",
        help=(
            "Optional Python-literal list of 3-tuples narrowing the count. "
            "Example: [('state', '=', 'active')]"
        ),
    )

    label = fields.Char(
        required=True,
        translate=True,
        help="Text displayed under the count on the button.",
    )
    icon = fields.Char(
        default="fa-list",
        help="FontAwesome icon class (e.g. fa-star, fa-list).",
    )
    color = fields.Selection(
        COLOR_CHOICES,
        default="text-primary",
        required=True,
        help="Bootstrap text-* class applied to the icon.",
    )
    open_on_click = fields.Boolean(
        default=True,
        help="Open the related records list when the button is clicked.",
    )

    active = fields.Boolean(default=True)
    failed_count = fields.Integer(
        default=0, readonly=True, help="Consecutive integrity-recovery failures."
    )
    last_failure_message = fields.Char(readonly=True)

    view_injection_id = fields.Many2one(
        "studio.light.view.injection",
        string="Generated view injection (button)",
        readonly=True,
        ondelete="set null",
    )
    box_injection_id = fields.Many2one(
        "studio.light.view.injection",
        string="Generated view injection (button_box fallback)",
        readonly=True,
        ondelete="set null",
        help=(
            "If the source form lacks a button_box, this injection adds one "
            "before //sheet."
        ),
    )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def _is_unlocked(self):
        return self.env.user.has_group(
            "bf_studio_light.group_studio_light_unlocked"
        )

    @api.constrains("source_model_id")
    def _check_source_not_locked(self):
        for rec in self:
            if is_model_locked(rec.source_model_name) and not rec._is_unlocked():
                raise ValidationError(
                    _(
                        "Source model %s is locked. The 'Studio Light: Bypass "
                        "model lock' group is required to override."
                    )
                    % rec.source_model_name
                )

    @api.constrains("target_model_id")
    def _check_target_not_locked(self):
        for rec in self:
            if is_model_locked(rec.target_model_name) and not rec._is_unlocked():
                raise ValidationError(
                    _(
                        "Target model %s is locked. The 'Studio Light: Bypass "
                        "model lock' group is required to override."
                    )
                    % rec.target_model_name
                )

    @api.constrains("relation_field_id", "source_model_id", "target_model_id")
    def _check_relation_field(self):
        for rec in self:
            f = rec.relation_field_id
            if not f:
                continue
            if f.model != rec.target_model_name:
                raise ValidationError(
                    _(
                        "Relation field %s belongs to %s, expected target "
                        "model %s."
                    )
                    % (f.name, f.model, rec.target_model_name)
                )
            if f.ttype != "many2one":
                raise ValidationError(
                    _("Relation field must be a Many2one (got %s).") % f.ttype
                )
            if f.relation != rec.source_model_name:
                raise ValidationError(
                    _(
                        "Relation field %s on %s points to %s, expected %s."
                    )
                    % (f.name, f.model, f.relation, rec.source_model_name)
                )
            if f.name in SENSITIVE_FIELD_NAMES:
                raise ValidationError(
                    _("Relation field %s is on the sensitive denylist.")
                    % f.name
                )

    @api.constrains("domain")
    def _check_domain(self):
        for rec in self:
            if not rec.domain:
                continue
            try:
                parsed = ast.literal_eval(rec.domain)
            except (ValueError, SyntaxError) as e:
                raise ValidationError(
                    _(
                        "Domain must be a Python literal "
                        "(list of 3-tuples). Parse error: %s"
                    )
                    % e
                )
            if not isinstance(parsed, list):
                raise ValidationError(
                    _("Domain must be a list (got %s).")
                    % type(parsed).__name__
                )
            for clause in parsed:
                _validate_domain_clause(clause)

    @api.constrains("label")
    def _check_label_plaintext(self):
        for rec in self:
            for tok in LABEL_FORBIDDEN_TOKENS:
                if tok in (rec.label or ""):
                    raise ValidationError(
                        _("Label may not contain %r (plain text only).")
                        % tok
                    )

    @api.constrains("icon")
    def _check_icon(self):
        for rec in self:
            if rec.icon and not ICON_RE.match(rec.icon):
                raise ValidationError(
                    _("Icon %r must match pattern %s.")
                    % (rec.icon, ICON_RE.pattern)
                )

    # ------------------------------------------------------------------
    # Helpers used by the controller
    # ------------------------------------------------------------------
    def _parse_domain(self):
        """Return the validated user domain as a fresh Python list."""
        self.ensure_one()
        if not self.domain:
            return []
        # Already validated by ``_check_domain``; literal_eval is safe.
        return list(ast.literal_eval(self.domain))

    def _build_action_dict(self, source_id):
        """Build the static ``ir.actions.act_window`` for a click.

        The relation filter is prepended so the user-supplied extra domain
        cannot widen scope past records related to ``source_id``.
        """
        self.ensure_one()
        domain = [(self.relation_field_id.name, "=", int(source_id))]
        domain.extend(self._parse_domain())
        return {
            "type": "ir.actions.act_window",
            "res_model": self.target_model_name,
            "name": self.label or self.name,
            "view_mode": "list,form",
            "domain": domain,
            "target": "current",
        }

    # ------------------------------------------------------------------
    # View provisioning
    # ------------------------------------------------------------------
    def _form_has_button_box(self):
        """Return True if the source model's primary form already has a
        ``<div name='button_box'>`` to anchor on."""
        self.ensure_one()
        view = self.env["ir.ui.view"].sudo().search(
            [
                ("model", "=", self.source_model_name),
                ("type", "=", "form"),
                ("inherit_id", "=", False),
                ("mode", "=", "primary"),
            ],
            order="priority,id",
            limit=1,
        )
        if not view:
            return False
        try:
            tree = etree.fromstring(view.arch_db or view.arch or "<form/>")
        except (etree.XMLSyntaxError, ValueError):
            return False
        return bool(tree.xpath("//div[@name='button_box']"))

    def _build_button_arch(self):
        """Generate the ``<widget .../>`` snippet for the smart button."""
        self.ensure_one()
        return (
            '<widget name="studio_light_smart_button_widget" '
            f"smart_button_id={quoteattr(str(self.id))} "
            f"label={quoteattr(self.label or '')} "
            f"icon={quoteattr(self.icon or 'fa-list')} "
            f"color={quoteattr(self.color or 'text-primary')} "
            f"open_on_click={quoteattr('1' if self.open_on_click else '0')}/>"
        )

    def _ensure_provisioned(self):
        """Create or refresh the underlying view-injection rows."""
        Injection = self.env["studio.light.view.injection"].with_context(
            studio_light_trusted_arch=True
        )
        for rec in self:
            arch = rec._build_button_arch()
            has_box = rec._form_has_button_box()

            base_vals = {
                "model_id": rec.source_model_id.id,
                "view_type": "form",
                "studio_smart_button_id": rec.id,
                "active": rec.active,
            }

            # 1) Optional button_box fallback row.
            if not has_box:
                box_vals = dict(
                    base_vals,
                    name=f"Studio Light box for {rec.name}",
                    custom_xpath="//sheet",
                    position="before",
                    arch_snippet=(
                        '<div class="oe_button_box" name="button_box"/>'
                    ),
                    sequence=10,
                )
                inj = rec.box_injection_id
                if inj and inj.exists():
                    inj.with_context(
                        studio_light_trusted_arch=True
                    ).write(box_vals)
                else:
                    new_inj = Injection.create(box_vals)
                    rec.box_injection_id = new_inj.id
            else:
                if rec.box_injection_id and rec.box_injection_id.exists():
                    rec.box_injection_id.unlink()
                rec.box_injection_id = False

            # 2) Mandatory button row.
            btn_vals = dict(
                base_vals,
                name=f"Studio Light button {rec.name}",
                custom_xpath="//div[@name='button_box']",
                position="inside",
                arch_snippet=arch,
                sequence=20,
            )
            inj = rec.view_injection_id
            if inj and inj.exists():
                inj.with_context(
                    studio_light_trusted_arch=True
                ).write(btn_vals)
            else:
                new_inj = Injection.create(btn_vals)
                rec.view_injection_id = new_inj.id
        return True

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._ensure_provisioned()
        return records

    def write(self, vals):
        res = super().write(vals)
        rebuild_keys = {
            "source_model_id",
            "target_model_id",
            "relation_field_id",
            "domain",
            "label",
            "icon",
            "color",
            "open_on_click",
            "active",
            "name",
        }
        # The integrity recovery path uses this flag to bypass the
        # rebuild — otherwise an auto-deactivate write would re-enter
        # _ensure_provisioned and re-raise the same error that caused
        # the deactivation, losing the active=False persistence and
        # stopping the failure backoff from working.
        if (
            any(k in vals for k in rebuild_keys)
            and not self.env.context.get("studio_light_skip_provision")
        ):
            self._ensure_provisioned()
        return res

    def unlink(self):
        for rec in self:
            for inj in (rec.view_injection_id, rec.box_injection_id):
                if inj and inj.exists():
                    try:
                        inj.unlink()
                    except Exception as e:
                        _logger.warning(
                            "Studio Light: could not unlink injection "
                            "%s: %s",
                            inj.id,
                            e,
                        )
        return super().unlink()

    # ------------------------------------------------------------------
    # Integrity (called from cron via studio.light.field cascade)
    # ------------------------------------------------------------------
    @api.model
    def _ensure_all_provisioned(self):
        for rec in self.search([("active", "=", True)]):
            try:
                rec._ensure_provisioned()
                rec.failed_count = 0
                rec.last_failure_message = False
            except Exception as e:
                rec.failed_count = (rec.failed_count or 0) + 1
                rec.last_failure_message = str(e)[:512]
                _logger.error(
                    "Studio Light: smart-button provisioning failed for "
                    "%s (attempt %s): %s",
                    rec.id,
                    rec.failed_count,
                    e,
                )
                if rec.failed_count >= 3:
                    rec.with_context(
                        studio_light_skip_provision=True
                    ).active = False
                    _logger.warning(
                        "Studio Light: auto-deactivated smart button %s "
                        "after 3 failures",
                        rec.id,
                    )


def _validate_domain_clause(clause):
    """Raise ValidationError if a single domain clause is unsafe."""
    if not (isinstance(clause, (tuple, list)) and len(clause) == 3):
        raise ValidationError(
            _(
                "Each domain clause must be a 3-tuple "
                "(field, operator, value); got %r."
            )
            % (clause,)
        )
    field_path, op, _value = clause
    if not isinstance(field_path, str) or not field_path:
        raise ValidationError(
            _("Domain field must be a non-empty string; got %r.") % (field_path,)
        )
    if op not in DOMAIN_OPERATOR_WHITELIST:
        raise ValidationError(
            _("Operator %r is not allowed. Whitelisted: %s")
            % (op, ", ".join(sorted(DOMAIN_OPERATOR_WHITELIST)))
        )
    for part in field_path.split("."):
        if not PATH_PART_RE.match(part):
            raise ValidationError(
                _("Domain field path segment %r is not a valid identifier.")
                % part
            )
        if part in SENSITIVE_FIELD_NAMES:
            raise ValidationError(
                _("Domain field path traverses sensitive field %r.") % part
            )

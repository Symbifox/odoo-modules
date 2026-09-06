import ast
import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)

FIELD_NAME_RE = re.compile(r"^x_studio_[a-z0-9_]+$")
PATH_PART_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

SUPPORTED_TYPES = [
    ("char", "Text (single line)"),
    ("text", "Text (multi-line)"),
    ("html", "HTML"),
    ("integer", "Integer"),
    ("float", "Decimal"),
    ("monetary", "Monetary"),
    ("boolean", "Checkbox"),
    ("date", "Date"),
    ("datetime", "Date & time"),
    ("selection", "Selection"),
    ("many2one", "Link to record"),
    ("many2many", "Tags / multi-select"),
    ("reference", "Polymorphic link (any of N models)"),
    ("binary", "Attachment / file"),
    ("image", "Image"),
]

# Forge surfaces ``image`` as a separate user-facing type, but at
# the ir.model.fields level Odoo only accepts ttype='binary' — ``image``
# is a Python-side ``fields.Image`` wrapper around binary plus a
# widget="image" hint at display time. Anywhere we hit the schema we
# normalise image → binary; the view-injection layer adds the widget.
TTYPE_NORMALISATION = {"image": "binary"}

# Models we refuse to touch by default (covers field creation and related
# path traversal). Bypass requires the dedicated unlocked group, NOT the
# regular admin group, NOT a context flag.
LOCKED_MODEL_PREFIXES = (
    "ir.",
    "base.",
    "auth_",
    "auth.",
    "bus.",
    "mail.",
    "account.",
    "payment.",
    "res.config.",
)
LOCKED_MODELS_EXACT = {
    "res.users",
    "res.groups",
    "res.users.log",
    "res.users.apikeys",
    "res.users.identitycheck",
    "ir.ui.view",
    "ir.model",
    "ir.model.fields",
    "ir.model.fields.selection",
    "ir.model.access",
    "ir.rule",
    "ir.actions.actions",
    "ir.actions.act_window",
    "ir.actions.server",
    "ir.cron",
    "ir.config_parameter",
    "ir.attachment",
    "ir.module.module",
    "studio.light.field",
    "studio.light.field.selection",
    "studio.light.view.injection",
    "studio.light.wizard",
    "studio.light.smart.button",
    "studio.light.smart.button.wizard",
}

# Field names whose contents should never leak through a related field,
# regardless of which model they live on.
SENSITIVE_FIELD_NAMES = frozenset(
    {
        "password",
        "password_crypt",
        "new_password",
        "totp_secret",
        "api_key",
        "key",
        "secret",
        "client_secret",
        "private_key",
        "access_token",
        "refresh_token",
        "oauth_access_token",
        "oauth_refresh_token",
        "smtp_pass",
        "imap_password",
        "consumer_secret",
        "webhook_secret",
    }
)


# --- Modifier expression validator (Tier A7) ---
#
# Conditional modifiers (`invisible="..."`, `required="..."`, `readonly="..."`)
# accept Python-like boolean expressions that Odoo evaluates with `safe_eval`
# at render time. We accept the standard idiom (`state == 'draft'`,
# `partner_id and partner_id.is_company`) but refuse anything that can call
# code: function calls, comprehensions, subscripts, starred/lambdas, etc.
# The whitelist below is intentionally narrow — extend deliberately.
_SAFE_MODIFIER_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.UnaryOp,
    ast.BinOp,
    ast.Compare,
    ast.Constant,
    ast.Name,
    ast.Attribute,
    ast.Tuple,
    ast.List,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
    ast.Load,
    ast.USub,
    ast.UAdd,
)
_SAFE_MODIFIER_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod)


def validate_modifier_expression(expr):
    """Refuse anything beyond `field op literal` / `field in [...]` style.

    Raises :class:`ValidationError` on the first forbidden construct.
    """
    if not expr or not expr.strip():
        return
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValidationError(
            _("Modifier expression is not valid Python: %s") % e
        )
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp):
            if not isinstance(node.op, _SAFE_MODIFIER_BINOPS):
                raise ValidationError(
                    _("Modifier expression: operator %s is not allowed.")
                    % type(node.op).__name__
                )
            continue
        if not isinstance(node, _SAFE_MODIFIER_NODES):
            raise ValidationError(
                _(
                    "Modifier expression contains a forbidden construct: %s. "
                    "Allowed: comparisons, boolean logic, field references, "
                    "literals."
                )
                % type(node).__name__
            )


def is_model_locked(model_name):
    if not model_name:
        return True
    if model_name in LOCKED_MODELS_EXACT:
        return True
    return any(model_name.startswith(p) for p in LOCKED_MODEL_PREFIXES)


def slugify_field_name(label):
    """Build a valid x_studio_* field name from a free-text label."""
    s = (label or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "field"
    candidate = f"x_studio_{s}"[:63]
    return candidate


class StudioLightFieldSelection(models.Model):
    _name = "studio.light.field.selection"
    _description = "Forge — Selection value"
    _order = "sequence, id"

    field_id = fields.Many2one(
        "studio.light.field",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    key = fields.Char(required=True, help="Stored value, e.g. 'gold'.")
    label = fields.Char(
        required=True, translate=True, help="Displayed label, e.g. 'Gold member'."
    )

    _sql_constraints = [
        (
            "unique_key_per_field",
            "unique(field_id, key)",
            "Selection keys must be unique within a field.",
        ),
    ]

    @api.constrains("key")
    def _check_key_chars(self):
        # Selection keys go straight into ir.model.fields.selection.value and
        # are then referenced from view arch. Restrict to identifier chars to
        # avoid quoting headaches and downstream injection.
        for rec in self:
            if not re.match(r"^[a-zA-Z0-9_-]+$", rec.key or ""):
                raise ValidationError(
                    _("Selection keys must use letters, digits, _ or -.")
                )


class StudioLightField(models.Model):
    """Persistent metadata for a custom field added through Forge.

    The corresponding ``ir.model.fields`` record is recreated on demand
    (post_init, integrity cron) so the field survives ``-u all`` upgrades.
    """

    _name = "studio.light.field"
    _description = "Forge — Custom field"
    _order = "model_id, name"

    name = fields.Char(
        required=True,
        index=True,
        help="Technical name. Must start with x_studio_ and use a-z 0-9 _ only.",
    )
    label = fields.Char(required=True, translate=True)
    help_text = fields.Char(string="Help", translate=True)
    model_id = fields.Many2one(
        "ir.model",
        required=True,
        ondelete="cascade",
        index=True,
        domain="[('transient', '=', False)]",
    )
    model_name = fields.Char(related="model_id.model", store=True, index=True)

    field_type = fields.Selection(SUPPORTED_TYPES, required=True, default="char")
    required = fields.Boolean(default=False)

    # Type-specific extras
    selection_ids = fields.One2many(
        "studio.light.field.selection",
        "field_id",
        string="Selection values",
    )
    relation_model_id = fields.Many2one(
        "ir.model",
        string="Related model (Many2one)",
        help="Required for type = many2one.",
    )
    relation_ondelete = fields.Selection(
        [("set null", "Set NULL"), ("restrict", "Restrict"), ("cascade", "Cascade")],
        default="set null",
    )
    reference_model_ids = fields.Many2many(
        "ir.model",
        "studio_light_field_reference_model_rel",
        "field_id",
        "model_id",
        string="Allowed reference models",
        help="For type = reference: the whitelist of target models the "
        "user can pick from when populating this field. Each model is "
        "lock-checked at field creation; locked models require the "
        "'Bypass model lock' group.",
    )

    # Conditional modifiers (Tier A7). Python-style expressions evaluated
    # by Odoo's view engine at render time, e.g.
    # ``state == 'draft'`` or ``partner_id and not partner_id.is_company``.
    # Validation refuses function calls, subscripts, comprehensions, etc.
    # to prevent the expressions from doing anything other than reading
    # field values and comparing them.
    invisible_expr = fields.Char(
        string="Invisible when",
        help="Python-style expression: when truthy, the field is hidden in "
        "every Forge view injection. Example: state == 'draft'.",
    )
    required_expr = fields.Char(
        string="Required when",
        help="Python-style expression: when truthy, the field is required.",
    )
    readonly_expr = fields.Char(
        string="Readonly when",
        help="Python-style expression: when truthy, the field is readonly.",
    )

    # Related field support (Tier 2.2)
    is_related = fields.Boolean(
        string="Computed from another field",
        help="If checked, this field reads its value from a related path "
        "(e.g. partner_id.email) instead of being stored directly.",
    )
    related_path = fields.Char(
        string="Related path",
        help="Dotted path from this model to the source field, e.g. partner_id.email.",
    )

    active = fields.Boolean(default=True)
    failed_count = fields.Integer(
        default=0, readonly=True, help="Consecutive integrity-recovery failures."
    )
    last_failure_message = fields.Char(readonly=True)
    ir_model_field_id = fields.Many2one(
        "ir.model.fields",
        string="Underlying ir.model.fields",
        readonly=True,
        ondelete="set null",
    )
    view_injection_ids = fields.One2many(
        "studio.light.view.injection",
        "studio_field_id",
    )

    _sql_constraints = [
        (
            "unique_name_per_model",
            "unique(model_id, name)",
            "A custom field with this technical name already exists on this model.",
        ),
    ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def _is_unlocked(self):
        return self.env.user.has_group(
            "bf_studio_light.group_studio_light_unlocked"
        )

    @api.constrains("name")
    def _check_name_format(self):
        for rec in self:
            if not FIELD_NAME_RE.match(rec.name or ""):
                raise ValidationError(
                    _("Field name %s is invalid. Must match x_studio_[a-z0-9_]+.")
                    % rec.name
                )

    @api.constrains(
        "model_id", "relation_model_id", "reference_model_ids", "field_type"
    )
    def _check_model_allowed(self):
        for rec in self:
            if is_model_locked(rec.model_name) and not rec._is_unlocked():
                raise ValidationError(
                    _(
                        "Model %s is locked by Forge. The dedicated "
                        "'Forge: Bypass model lock' group is required "
                        "to override (and must be granted by a sysadmin)."
                    )
                    % rec.model_name
                )
            # Target model on relational fields must also clear the lock.
            # Without this check, a non-unlocked admin could create
            # x_studio_link_to_users pointing at res.users and bypass the
            # locked-model intent in one direction.
            if (
                rec.field_type in ("many2one", "many2many")
                and not rec.is_related
                and rec.relation_model_id
                and is_model_locked(rec.relation_model_id.model)
                and not rec._is_unlocked()
            ):
                raise ValidationError(
                    _(
                        "Target model %s is locked by Forge. "
                        "Relational fields cannot point at locked models "
                        "without the 'Bypass model lock' group."
                    )
                    % rec.relation_model_id.model
                )
            # Every model in a reference whitelist must clear the lock.
            if (
                rec.field_type == "reference"
                and not rec.is_related
                and not rec._is_unlocked()
            ):
                bad = [
                    m.model
                    for m in rec.reference_model_ids
                    if is_model_locked(m.model)
                ]
                if bad:
                    raise ValidationError(
                        _(
                            "Reference whitelist includes locked model(s) %s. "
                            "Remove them or request the 'Bypass model lock' "
                            "group."
                        )
                        % ", ".join(sorted(bad))
                    )

    @api.constrains(
        "field_type", "selection_ids", "relation_model_id", "reference_model_ids"
    )
    def _check_type_extras(self):
        for rec in self:
            if rec.is_related:
                # Related fields don't need selection/relation declarations;
                # the target field's own metadata is reused.
                continue
            if rec.field_type == "selection" and not rec.selection_ids:
                raise ValidationError(
                    _("Selection fields must define at least one value.")
                )
            if rec.field_type in ("many2one", "many2many") and not rec.relation_model_id:
                raise ValidationError(
                    _("%s fields must specify a related model.") % rec.field_type
                )
            if rec.field_type == "reference" and not rec.reference_model_ids:
                raise ValidationError(
                    _(
                        "Reference fields must whitelist at least one target "
                        "model — pick the models that the user is allowed to "
                        "point this field at."
                    )
                )

    @api.constrains("invisible_expr", "required_expr", "readonly_expr")
    def _check_modifier_expressions(self):
        for rec in self:
            for expr in (rec.invisible_expr, rec.required_expr, rec.readonly_expr):
                validate_modifier_expression(expr)

    @api.constrains("is_related", "related_path", "model_id", "field_type")
    def _check_related_path(self):
        for rec in self:
            if not rec.is_related:
                continue
            if not rec.related_path:
                raise ValidationError(
                    _("Tick 'Computed from another field' requires a related path.")
                )
            try:
                rec._resolve_related_target()
            except (KeyError, AccessError) as e:
                raise ValidationError(
                    _("Related path %s is invalid or forbidden: %s")
                    % (rec.related_path, e)
                )

    def _resolve_related_target(self):
        """Walk related_path step by step and return the final ir.model.fields.

        Refuses to traverse into locked models or to expose sensitive fields,
        even if the user is in the unlocked group.
        """
        self.ensure_one()
        IMF = self.env["ir.model.fields"].sudo()
        current_model = self.model_name
        parts = self.related_path.split(".")
        last = None
        for i, part in enumerate(parts):
            if not PATH_PART_RE.match(part):
                raise KeyError(f"path segment {part!r} is not a valid identifier")
            if part in SENSITIVE_FIELD_NAMES:
                raise AccessError(
                    f"field {current_model}.{part} is on the sensitive denylist"
                )
            f = IMF.search(
                [("model", "=", current_model), ("name", "=", part)], limit=1
            )
            if not f:
                raise KeyError(f"{current_model}.{part} does not exist")
            last = f
            if i < len(parts) - 1:
                if f.ttype not in ("many2one", "one2many", "many2many"):
                    raise KeyError(
                        f"{current_model}.{part} is {f.ttype}, cannot traverse further"
                    )
                if is_model_locked(f.relation):
                    raise AccessError(
                        f"cannot traverse into locked model {f.relation}"
                    )
                current_model = f.relation
        return last

    # ------------------------------------------------------------------
    # Field provisioning
    # ------------------------------------------------------------------
    def _build_ir_model_fields_vals(self):
        self.ensure_one()
        ttype = self.field_type
        if self.is_related:
            target = self._resolve_related_target()
            ttype = target.ttype
        ttype = TTYPE_NORMALISATION.get(ttype, ttype)
        vals = {
            "name": self.name,
            "model_id": self.model_id.id,
            "field_description": self.label,
            "ttype": ttype,
            "state": "manual",
            "required": self.required and not self.is_related,
        }
        if self.is_related:
            vals["related"] = self.related_path
            vals["readonly"] = True
            target = self._resolve_related_target()
            if target.ttype in ("many2one", "one2many", "many2many"):
                vals["relation"] = target.relation
        if self.help_text:
            vals["help"] = self.help_text
        if self.field_type == "many2one" and not self.is_related:
            vals["relation"] = self.relation_model_id.model
            vals["on_delete"] = self.relation_ondelete or "set null"
        if self.field_type == "many2many" and not self.is_related:
            # Manual M2M fields: setting `relation` is enough — Odoo
            # auto-generates the intermediate table name (with a hash
            # suffix when the natural name would exceed 63 chars). Don't
            # pre-populate relation_table or column1/column2 — letting
            # core handle it keeps us aligned with how Studio Enterprise
            # provisions the same field type.
            vals["relation"] = self.relation_model_id.model
        if self.field_type == "selection" and not self.is_related:
            vals["selection_ids"] = [
                (
                    0,
                    0,
                    {
                        "value": s.key,
                        "name": s.label,
                        "sequence": s.sequence or 10,
                    },
                )
                for s in self.selection_ids.sorted("sequence")
            ]
        if self.field_type == "reference" and not self.is_related:
            # Reference fields store the list of allowed target models in
            # ir.model.fields.selection_ids — each entry's `value` is the
            # model technical name (e.g. 'res.partner') and `name` is the
            # display label shown in the dropdown.
            vals["selection_ids"] = [
                (
                    0,
                    0,
                    {
                        "value": m.model,
                        "name": m.name,
                        "sequence": (i + 1) * 10,
                    },
                )
                for i, m in enumerate(
                    self.reference_model_ids.sorted("name")
                )
            ]
        return vals

    def _ensure_ir_model_field(self):
        """Create the ir.model.fields row if missing; return it."""
        IMF = self.env["ir.model.fields"].sudo()
        for rec in self:
            if rec.ir_model_field_id and rec.ir_model_field_id.exists():
                continue
            existing = IMF.search(
                [("model_id", "=", rec.model_id.id), ("name", "=", rec.name)],
                limit=1,
            )
            if existing:
                rec.ir_model_field_id = existing.id
                continue
            try:
                imf = IMF.create(rec._build_ir_model_fields_vals())
                rec.ir_model_field_id = imf.id
                rec.failed_count = 0
                rec.last_failure_message = False
                _logger.info(
                    "Forge: created ir.model.fields %s on %s",
                    rec.name,
                    rec.model_name,
                )
            except Exception as e:
                _logger.exception(
                    "Forge: failed to create field %s on %s",
                    rec.name,
                    rec.model_name,
                )
                raise UserError(
                    _("Failed to create field %s on %s: %s")
                    % (rec.name, rec.model_name, e)
                )
        return self.mapped("ir_model_field_id")

    def _sync_selection_values(self):
        """Push current selection_ids (selection type) or reference_model_ids
        (reference type) into ir.model.fields.selection rows. Both field
        types use the same selection storage on ir.model.fields — for
        selection it's user-defined keys, for reference it's the
        whitelisted model technical names."""
        IMFS = self.env["ir.model.fields.selection"].sudo()
        for rec in self:
            if rec.is_related:
                continue
            if not rec.ir_model_field_id:
                continue
            if rec.field_type == "selection":
                wanted = {
                    s.key: {"name": s.label, "sequence": s.sequence or 10}
                    for s in rec.selection_ids
                }
            elif rec.field_type == "reference":
                wanted = {
                    m.model: {"name": m.name, "sequence": (i + 1) * 10}
                    for i, m in enumerate(rec.reference_model_ids.sorted("name"))
                }
            else:
                continue
            current = {s.value: s for s in rec.ir_model_field_id.selection_ids}
            for value, sel in current.items():
                if value not in wanted:
                    sel.unlink()
            for value, wvals in wanted.items():
                existing = current.get(value)
                vals = {
                    "field_id": rec.ir_model_field_id.id,
                    "value": value,
                    "name": wvals["name"],
                    "sequence": wvals["sequence"],
                }
                if existing:
                    existing.write(vals)
                else:
                    IMFS.create(vals)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            label = vals.get("label")
            if not vals.get("name") and label:
                vals["name"] = slugify_field_name(label)
        records = super().create(vals_list)
        records._ensure_ir_model_field()
        records._sync_selection_values()
        return records

    def write(self, vals):
        forwarded = {}
        if "label" in vals:
            forwarded["field_description"] = vals["label"]
        if "help_text" in vals:
            forwarded["help"] = vals["help_text"]
        if "required" in vals and not any(r.is_related for r in self):
            forwarded["required"] = vals["required"]
        res = super().write(vals)
        if forwarded:
            for rec in self:
                if rec.ir_model_field_id:
                    rec.ir_model_field_id.sudo().write(forwarded)
        if "selection_ids" in vals or "reference_model_ids" in vals or any(
            k in vals for k in ("field_type", "relation_model_id")
        ):
            self._sync_selection_values()
        # Modifier expressions are baked into the inheriting view arch at
        # build time, so a change must propagate to the existing
        # ir.ui.view rows or it'll only take effect on the next
        # post-init cycle.
        if any(k in vals for k in ("invisible_expr", "required_expr", "readonly_expr")):
            self.mapped("view_injection_ids")._ensure_ir_view()
        return res

    def unlink(self):
        self.mapped("view_injection_ids").unlink()
        for rec in self:
            if rec.ir_model_field_id:
                try:
                    rec.ir_model_field_id.sudo().unlink()
                except Exception as e:
                    _logger.warning(
                        "Forge: could not unlink ir.model.fields %s: %s",
                        rec.name,
                        e,
                    )
        return super().unlink()

    # ------------------------------------------------------------------
    # Integrity (called from hook + cron)
    # ------------------------------------------------------------------
    @api.model
    def _ensure_all_provisioned(self):
        """Recreate any ir.model.fields whose backing record was lost.

        Records that fail 3 times in a row are auto-deactivated to keep
        logs sane.
        """
        for rec in self.search([("active", "=", True)]):
            try:
                rec._ensure_ir_model_field()
                rec._sync_selection_values()
            except Exception as e:
                rec.failed_count = (rec.failed_count or 0) + 1
                rec.last_failure_message = str(e)[:512]
                _logger.error(
                    "Forge: integrity provisioning failed for %s.%s "
                    "(attempt %s): %s",
                    rec.model_name,
                    rec.name,
                    rec.failed_count,
                    e,
                )
                if rec.failed_count >= 3:
                    rec.active = False
                    _logger.warning(
                        "Forge: auto-deactivated %s.%s after 3 failures",
                        rec.model_name,
                        rec.name,
                    )
        self.env["studio.light.view.injection"]._ensure_all_provisioned()
        self.env["studio.light.smart.button"]._ensure_all_provisioned()

    def action_toggle_active(self):
        for rec in self:
            rec.active = not rec.active
            if rec.active:
                rec.failed_count = 0
                rec.last_failure_message = False
        return True

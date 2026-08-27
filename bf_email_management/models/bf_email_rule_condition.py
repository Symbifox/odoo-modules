"""bf.email.rule.condition — one clause of a routing rule.

Outlook and Gmail both express a rule as *a set of clauses* plus *a set of
exceptions*. Before 18.0.9.11.0 ``bf.email.rule`` carried a single clause, held
in two Char fields on the rule itself; the migration turns each of those into
one row here, so no rule loses its meaning.

Evaluation is deliberately **not** a generic ``safe_eval`` over the record: the
catalogue below is the entire surface a clause can look at. Two escape hatches
(``partner_field`` and ``odoo_domain``) preserve what the old engine could do,
and they are the only two paths that reach ``safe_eval``.

Two notes on what a clause can honestly see:

- ``raw_headers`` is filled by the IMAP ingestion path. Chatter/gateway rows
  usually have none, so a header clause simply does not match them — it never
  raises. Same for ``body``: the engine reads ``body_preview``, the truncated
  copy, because the full body of an orphan IMAP row is reconstructed on demand
  and is far too expensive to render for every rule on every message.
- Every text comparison caps the haystack at ``_MAX_HAYSTACK`` characters
  before a regex touches it. Python's ``re`` has no timeout, and a rule is
  user-authored: an unbounded pattern over a 2 MB body is a worker that never
  comes back.
"""

import ast
import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)

# Longest string any operator will scan. Chosen well above a realistic subject
# or address list and a little above the stored body_preview.
_MAX_HAYSTACK = 20000

# ---------------------------------------------------------------------------
# Catalogue — (technical name, label, kind)
#
# ``kind`` decides which operators are offered and which value widget shows.
# The rule model, the quick-create recipes and the tests all read this list, so
# adding a matchable attribute is a one-line change here plus a getter below.
# ---------------------------------------------------------------------------
FIELD_CATALOGUE = [
    ("email_from", "Expéditeur", "text"),
    ("email_to", "Destinataires (À)", "text"),
    ("email_cc", "Copie conforme (c.c.)", "text"),
    ("recipients", "Destinataires (À ou c.c.)", "text"),
    ("subject", "Objet", "text"),
    ("body", "Corps du message", "text"),
    ("anywhere", "N'importe où (objet, corps, adresses)", "text"),
    ("header", "En-tête brut", "header"),
    ("attachment_count", "Nombre de pièces jointes", "number"),
    ("direction", "Direction", "direction"),
    ("category", "Catégorie", "category"),
    ("priority", "Priorité", "priority"),
    ("is_cc_to_me", "Je suis en copie conforme seulement", "bool"),
    ("is_to_me", "Je suis dans « À »", "bool"),
    ("is_from_me", "Envoyé par moi", "bool"),
    ("is_bulk", "Envoi de masse", "bool"),
    ("has_attachments", "A des pièces jointes", "bool"),
    ("is_internal_sender", "Expéditeur de mon organisation", "bool"),
    ("has_record", "Déjà rattaché à une fiche Odoo", "bool"),
    ("partner_field", "Champ du contact (avancé)", "expr"),
    ("odoo_domain", "Domaine Odoo (avancé)", "expr"),
]

FIELD_KIND = {name: kind for name, _label, kind in FIELD_CATALOGUE}

# Operators offered per kind. The form view hides the rest; ``_check_operator``
# refuses them server-side, because a view constraint is a suggestion.
OPERATORS_BY_KIND = {
    "text": [
        "contains", "not_contains", "contains_any", "not_contains_any",
        "equals", "not_equals", "starts_with", "ends_with",
        "regex", "not_regex", "is_set", "is_not_set",
    ],
    "header": [
        "contains", "not_contains", "regex", "not_regex",
        "is_set", "is_not_set",
    ],
    "number": ["num_eq", "num_ne", "num_gt", "num_lt"],
    "direction": ["equals", "not_equals"],
    "category": ["equals", "not_equals"],
    "priority": ["equals", "not_equals"],
    "bool": ["is_true", "is_false"],
    "expr": ["expr"],
}

# Operators that need no ``value`` — everything else does.
VALUELESS_OPERATORS = {"is_set", "is_not_set", "is_true", "is_false"}

# Kinds whose value lives in a typed Selection rather than in ``value``.
TYPED_VALUE_FIELD = {
    "direction": "value_direction",
    "category": "value_category",
    "priority": "value_priority",
}


def split_list(raw):
    """Split a ``contains_any`` value into needles.

    Commas, semicolons and newlines all separate; whitespace inside an entry is
    kept, so "facture pro forma, invoice" is two needles, not three.
    """
    if not raw:
        return []
    return [p.strip() for p in re.split(r"[,;\n]+", raw) if p.strip()]


class BfEmailRuleCondition(models.Model):
    _name = "bf.email.rule.condition"
    _description = "Condition d'une règle de courriel"
    _order = "sequence, id"

    # A clause belongs to a routing rule OR to an absence message, never to
    # both and never to neither. Two optional parents rather than two clause
    # models: the matching engine below is the same code either way, and a
    # second copy of it is a second place for the two to drift apart.
    rule_id = fields.Many2one(
        comodel_name="bf.email.rule",
        string="Règle",
        index=True,
        ondelete="cascade",
    )
    absence_reply_id = fields.Many2one(
        comodel_name="bf.email.absence.reply",
        string="Message d'absence",
        index=True,
        ondelete="cascade",
    )

    # The Python constraint below only fires when one of the two parents is in
    # the values being written — and neither is, when somebody creates an
    # orphan clause. The database check is what makes it unbreakable.
    _sql_constraints = [
        (
            "one_parent",
            "CHECK ((rule_id IS NULL) <> (absence_reply_id IS NULL))",
            "Une condition appartient soit à une règle, soit à un message "
            "d'absence — jamais aux deux, jamais à aucun.",
        ),
    ]
    sequence = fields.Integer(string="Séquence", default=10)
    kind = fields.Selection(
        selection=[
            ("condition", "Condition"),
            ("exception", "Exception"),
        ],
        string="Rôle",
        required=True,
        default="condition",
        help="Une exception qui s'applique annule la règle, quelles que "
             "soient les conditions satisfaites.",
    )

    field_name = fields.Selection(
        selection=[(name, label) for name, label, _kind in FIELD_CATALOGUE],
        string="Sur quoi",
        required=True,
        default="email_from",
    )
    field_kind = fields.Selection(
        selection=[
            ("text", "Texte"),
            ("header", "En-tête"),
            ("number", "Nombre"),
            ("direction", "Direction"),
            ("category", "Catégorie"),
            ("priority", "Priorité"),
            ("bool", "Oui / non"),
            ("expr", "Expression"),
        ],
        string="Nature",
        compute="_compute_field_kind",
        store=True,
        help="Déduit du champ choisi. Sert à n'afficher que les opérateurs "
             "et la zone de saisie qui ont du sens.",
    )

    operator = fields.Selection(
        selection=[
            ("contains", "contient"),
            ("not_contains", "ne contient pas"),
            ("contains_any", "contient l'un de"),
            ("not_contains_any", "ne contient aucun de"),
            ("equals", "est exactement"),
            ("not_equals", "n'est pas"),
            ("starts_with", "commence par"),
            ("ends_with", "se termine par"),
            ("regex", "correspond à l'expression régulière"),
            ("not_regex", "ne correspond pas à l'expression régulière"),
            ("is_set", "est renseigné"),
            ("is_not_set", "est vide"),
            ("num_eq", "="),
            ("num_ne", "≠"),
            ("num_gt", ">"),
            ("num_lt", "<"),
            ("is_true", "est vrai"),
            ("is_false", "est faux"),
            ("expr", "expression Python"),
        ],
        string="Opérateur",
        required=True,
        default="contains",
    )

    value = fields.Char(
        string="Valeur",
        help="Pour « contient l'un de », séparer par des virgules, des "
             "points-virgules ou des retours de ligne.",
    )
    header_name = fields.Char(
        string="Nom de l'en-tête",
        help="Ex. : List-Unsubscribe, Auto-Submitted, X-Spam-Flag. Laissé "
             "vide, la condition regarde le bloc d'en-têtes en entier.",
    )
    value_direction = fields.Selection(
        selection=[("in", "Entrant"), ("out", "Sortant")],
        string="Direction attendue",
    )
    value_category = fields.Selection(
        selection=[
            ("client", "Client"),
            ("internal", "Interne"),
            ("vendor", "Fournisseur"),
            ("notification", "Notification"),
            ("marketing", "Marketing"),
        ],
        string="Catégorie attendue",
    )
    value_priority = fields.Selection(
        selection=[
            ("0", "Normal"),
            ("1", "Faible"),
            ("2", "Élevée"),
            ("3", "Urgente"),
        ],
        string="Priorité attendue",
    )

    description = fields.Char(
        string="Résumé",
        compute="_compute_description",
        help="Phrase lisible reconstituée à partir des trois champs.",
    )

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends("field_name")
    def _compute_field_kind(self):
        for cond in self:
            cond.field_kind = FIELD_KIND.get(cond.field_name, "text")

    @api.depends(
        "field_name", "operator", "value", "header_name",
        "value_direction", "value_category", "value_priority",
    )
    def _compute_description(self):
        labels = dict(self._fields["field_name"]._description_selection(self.env))
        ops = dict(self._fields["operator"]._description_selection(self.env))
        for cond in self:
            subject = labels.get(cond.field_name, cond.field_name or "")
            if cond.field_name == "header" and cond.header_name:
                subject = "%s « %s »" % (subject, cond.header_name)
            parts = [subject, ops.get(cond.operator, cond.operator or "")]
            if cond.operator not in VALUELESS_OPERATORS:
                parts.append(cond._display_value() or "…")
            cond.description = " ".join(p for p in parts if p)

    def _display_value(self):
        self.ensure_one()
        typed = TYPED_VALUE_FIELD.get(self.field_kind)
        if typed:
            field = self._fields[typed]
            selection = dict(field._description_selection(self.env))
            return selection.get(self[typed], self[typed] or "")
        return self.value or ""

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    @api.onchange("field_name")
    def _onchange_field_name(self):
        """Snap the operator to one the new field actually supports."""
        for cond in self:
            allowed = OPERATORS_BY_KIND.get(
                FIELD_KIND.get(cond.field_name, "text"), [])
            if allowed and cond.operator not in allowed:
                cond.operator = allowed[0]

    @api.constrains("field_name", "operator")
    def _check_operator(self):
        for cond in self:
            allowed = OPERATORS_BY_KIND.get(cond.field_kind, [])
            if cond.operator not in allowed:
                raise ValidationError(_(
                    "L'opérateur « %(op)s » ne s'applique pas à « %(field)s ».",
                    op=cond.operator, field=cond.field_name,
                ))

    @api.constrains("operator", "value", "value_direction",
                    "value_category", "value_priority")
    def _check_value(self):
        for cond in self:
            if cond.operator in VALUELESS_OPERATORS:
                continue
            if not cond._display_value():
                raise ValidationError(_(
                    "La condition « %s » attend une valeur.",
                    cond.field_name,
                ))

    @api.constrains("operator", "value")
    def _check_regex(self):
        """A broken pattern must fail at save, not silently at 3 a.m."""
        for cond in self:
            if cond.operator not in ("regex", "not_regex") or not cond.value:
                continue
            try:
                re.compile(cond.value)
            except re.error as exc:
                raise ValidationError(_(
                    "Expression régulière invalide : %s", exc,
                )) from exc

    @api.constrains("field_name", "value")
    def _check_expression(self):
        """Refuse an escape-hatch expression that will not parse."""
        for cond in self:
            if cond.field_kind != "expr" or not cond.value:
                continue
            try:
                ast.parse(cond.value, mode="eval")
            except SyntaxError as exc:
                raise ValidationError(_(
                    "Expression Python invalide : %s", exc,
                )) from exc

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------
    def _match(self, record, ctx=None):
        """True when this clause holds for ``record`` (a bf.email).

        ``ctx`` carries what the engine resolved once per owner —
        ``self_addresses`` and ``self_domains``. It is optional so a clause
        stays testable on its own; the owner-dependent fields then resolve
        the addresses themselves.
        """
        self.ensure_one()
        ctx = ctx or {}
        try:
            kind = self.field_kind
            if kind == "bool":
                return self._match_bool(record, ctx)
            if kind == "number":
                return self._match_number(record)
            if kind in TYPED_VALUE_FIELD:
                return self._match_choice(record, kind)
            if kind == "expr":
                return self._match_expression(record)
            return self._match_text(record)
        except Exception:
            # A single broken clause must not stop the ingestion of a message.
            _logger.warning(
                "bf.email.rule.condition %s: évaluation impossible sur "
                "bf.email %s", self.id, record.id, exc_info=True,
            )
            return False

    # -- per-kind ------------------------------------------------------
    def _match_text(self, record):
        return self._apply_text_operator(self._haystack(record))

    def _match_number(self, record):
        actual = record.attachment_count or 0
        try:
            expected = int((self.value or "0").strip())
        except ValueError:
            return False
        return {
            "num_eq": actual == expected,
            "num_ne": actual != expected,
            "num_gt": actual > expected,
            "num_lt": actual < expected,
        }.get(self.operator, False)

    def _match_choice(self, record, kind):
        expected = self[TYPED_VALUE_FIELD[kind]]
        actual = {
            "direction": record.direction,
            "category": record.category,
            "priority": record.priority,
        }[kind]
        if self.operator == "equals":
            return actual == expected
        return actual != expected

    def _match_bool(self, record, ctx):
        actual = self._bool_value(record, ctx)
        return actual if self.operator == "is_true" else not actual

    def _match_expression(self, record):
        if self.field_name == "partner_field":
            partner = record.partner_id or record.author_id
            if not partner:
                return False
            return bool(safe_eval(self.value or "False", {
                "partner": partner,
                "p": partner,
            }))
        # odoo_domain: a list is re-searched against this row, anything else is
        # taken for its truthiness — the semantics the old engine had.
        result = safe_eval(self.value or "False", {
            "record": record,
            "rec": record,
            "uid": self.env.uid,
            "user": self.env.user,
        })
        if isinstance(result, list):
            return bool(record.search_count(result + [("id", "=", record.id)]))
        return bool(result)

    # -- helpers -------------------------------------------------------
    def _haystack(self, record):
        """The string this clause compares against, already length-capped."""
        self.ensure_one()
        name = self.field_name
        if name == "header":
            raw = self._header_haystack(record)
        elif name == "recipients":
            raw = " ".join(filter(None, [record.email_to, record.email_cc]))
        elif name == "anywhere":
            raw = " ".join(filter(None, [
                record.subject, record.body_preview, record.email_from,
                record.email_to, record.email_cc,
            ]))
        elif name == "body":
            raw = record.body_preview or ""
        else:
            raw = record[name] or ""
        return (raw or "")[:_MAX_HAYSTACK]

    def _header_haystack(self, record):
        """Values of ``header_name`` in ``raw_headers``, or the whole block.

        ``raw_headers`` is stored as ``Name: value`` lines, values still folded
        the way the sender wrote them — a continuation line starts with a space
        or a tab and belongs to the header above it.
        """
        blob = record.raw_headers or ""
        if not self.header_name:
            return blob
        wanted = self.header_name.strip().lower().rstrip(":")
        found = []
        current = None
        for line in blob.splitlines():
            if line[:1] in (" ", "\t"):
                if current is not None:
                    current.append(line.strip())
                continue
            if current is not None:
                found.append(" ".join(current))
                current = None
            name, sep, rest = line.partition(":")
            if sep and name.strip().lower() == wanted:
                current = [rest.strip()]
        if current is not None:
            found.append(" ".join(current))
        return "\n".join(found)

    def _bool_value(self, record, ctx):
        name = self.field_name
        if name == "has_record":
            return bool(record.res_model and record.res_id)
        if name == "is_internal_sender":
            domains = ctx.get("self_domains")
            if domains is None:
                domains = self._owner_domains(record)
            sender = (record.email_from or "").lower()
            match = re.search(r"@([^\s>,;]+)", sender)
            return bool(match) and match.group(1).strip(">") in domains
        # The remaining boolean fields are stored on bf.email under the very
        # same name — is_cc_to_me already means "in cc and NOT in to".
        return bool(record[name])

    def _owner_domains(self, record):
        """Domains the row's owner sends from. Fallback when ctx is absent."""
        owner = record.user_id or self.env.user
        addrs = record._get_self_addresses(user=owner)
        return {a.split("@", 1)[1] for a in addrs if "@" in a}

    def _apply_text_operator(self, haystack):
        op = self.operator
        if op == "is_set":
            return bool((haystack or "").strip())
        if op == "is_not_set":
            return not (haystack or "").strip()
        needle = self.value or ""
        low = (haystack or "").lower()
        if op == "contains":
            return needle.lower() in low
        if op == "not_contains":
            return needle.lower() not in low
        if op == "contains_any":
            return any(n.lower() in low for n in split_list(needle))
        if op == "not_contains_any":
            return not any(n.lower() in low for n in split_list(needle))
        if op == "equals":
            return low.strip() == needle.lower().strip()
        if op == "not_equals":
            return low.strip() != needle.lower().strip()
        if op == "starts_with":
            return low.strip().startswith(needle.lower())
        if op == "ends_with":
            return low.strip().endswith(needle.lower())
        if op == "regex":
            return bool(re.search(needle, haystack or "", re.IGNORECASE))
        if op == "not_regex":
            return not re.search(needle, haystack or "", re.IGNORECASE)
        return False

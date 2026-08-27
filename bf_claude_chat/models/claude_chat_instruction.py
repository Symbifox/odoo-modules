"""Steering instructions ("Consignes") composed into Claude's system prompt.

Each instruction is a short directive the user wants Claude to respect. An
instruction is either global (every conversation) or scoped to one Odoo model,
and either shared with everyone (no owner) or private to a single user.

Only active instructions reach the prompt. The composed block is built server
side by :meth:`_build_prompt_block` and shipped to the bridge inside the chat
payload, alongside the page context.
"""

import difflib
import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Two bodies whose normalized forms are at least this similar are reported as
# near-duplicates. Tuned so that a reworded copy trips it but two genuinely
# different directives on the same topic do not.
_DUPLICATE_RATIO = 0.82

# Directive pairs that contradict each other when they govern the same subject.
_NEGATION_PAIRS = [
    ("toujours", "jamais"),
    ("always", "never"),
    ("doit", "ne doit pas"),
    ("must", "must not"),
    ("utilise", "n'utilise pas"),
    ("use", "do not use"),
]

_WORD_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def _normalize(text):
    """Lowercase, strip punctuation and collapse whitespace, for comparison."""
    return _SPACE_RE.sub(" ", _WORD_RE.sub(" ", (text or "").lower())).strip()


class ClaudeChatInstruction(models.Model):
    _name = "claude.chat.instruction"
    _description = "Claude Steering Instruction"
    _order = "sequence, id"

    name = fields.Char(
        string="Title",
        required=True,
        help="Short label shown in the list. Not sent to Claude.",
    )
    body = fields.Text(
        string="Instruction",
        required=True,
        help="The directive itself, written as you would say it to a colleague. "
             "This text is composed into Claude's system prompt.",
    )
    active = fields.Boolean(default=True)
    sequence = fields.Integer(
        default=10,
        help="Order in which instructions are composed into the prompt.",
    )
    scope = fields.Selection(
        [("global", "Every conversation"), ("model", "One record type")],
        string="Scope",
        default="global",
        required=True,
    )
    model_id = fields.Many2one(
        "ir.model",
        string="Record Type",
        ondelete="cascade",
        help="Only applies when the user is working on this record type.",
    )
    res_model = fields.Char(
        related="model_id.model",
        store=True,
        index=True,
        string="Model Name",
    )
    user_id = fields.Many2one(
        "res.users",
        string="Owner",
        ondelete="cascade",
        help="Leave empty to apply the instruction to everyone. Set it to keep "
             "the instruction private to that user.",
    )

    @api.constrains("scope", "model_id")
    def _check_scope_model(self):
        for rec in self:
            if rec.scope == "model" and not rec.model_id:
                raise ValidationError(
                    _("Pick a record type, or set the scope back to "
                      "« Every conversation ».")
                )

    @api.onchange("scope")
    def _onchange_scope(self):
        if self.scope == "global":
            self.model_id = False

    # ------------------------------------------------------------------
    # Prompt composition
    # ------------------------------------------------------------------
    @api.model
    def _applicable(self, res_model=None):
        """Active instructions that apply to the current user, ordered.

        Global ones always apply; model-scoped ones only when ``res_model``
        matches the record the user is looking at.
        """
        scope_domain = ["|", ("scope", "=", "global")]
        if res_model:
            scope_domain += ["&", ("scope", "=", "model"),
                             ("res_model", "=", res_model)]
        else:
            # No record in context: keep the OR well-formed with a false leaf.
            scope_domain += [("id", "=", False)]
        owner_domain = ["|", ("user_id", "=", False),
                        ("user_id", "=", self.env.uid)]
        return self.search(owner_domain + scope_domain)

    @api.model
    def _build_prompt_block(self, res_model=None):
        """Compose the steering block, or an empty string when there is none."""
        instructions = self._applicable(res_model)
        if not instructions:
            return ""
        lines = []
        for instruction in instructions:
            body = _SPACE_RE.sub(" ", (instruction.body or "").strip())
            if body:
                lines.append(f"- {body}")
        if not lines:
            return ""
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Coherence
    # ------------------------------------------------------------------
    @staticmethod
    def _same_subject(body_a, body_b):
        """True when two normalized bodies talk about the same thing."""
        words_a = {w for w in body_a.split() if len(w) > 4}
        words_b = {w for w in body_b.split() if len(w) > 4}
        if not words_a or not words_b:
            return False
        overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
        return overlap >= 0.4

    @classmethod
    def _detect_conflict(cls, body_a, body_b):
        """Return the (positive, negative) pair that makes two bodies clash.

        Both members are normalized before matching, otherwise a pair written
        with an apostrophe ("n'utilise pas") never matches the normalized body.
        """
        if not cls._same_subject(body_a, body_b):
            return None
        for positive, negative in _NEGATION_PAIRS:
            pos, neg = _normalize(positive), _normalize(negative)
            a_positive = pos in body_a and neg not in body_a
            b_positive = pos in body_b and neg not in body_b
            if (a_positive and neg in body_b) or (b_positive and neg in body_a):
                return positive, negative
        return None

    def _coherence_findings(self):
        """Return (kind, first, second, detail) tuples for the given set.

        Two checks, both deterministic, and the order matters. A contradiction
        is by construction almost identical to what it contradicts: the two
        differ by a single negation, so their normalized forms score very high
        on similarity. Checking for duplicates first would therefore swallow
        every conflict. Conflicts are tested first for that reason.
        """
        records = self.filtered("active")
        findings = []
        pairs = [(a, b) for i, a in enumerate(records) for b in records[i + 1:]]

        for first, second in pairs:
            # Only compare instructions that can be active at the same time.
            if first.scope == "model" and second.scope == "model" \
                    and first.res_model != second.res_model:
                continue
            if first.user_id and second.user_id and first.user_id != second.user_id:
                continue

            body_a, body_b = _normalize(first.body), _normalize(second.body)
            if not body_a or not body_b:
                continue

            clash = self._detect_conflict(body_a, body_b)
            if clash:
                positive, negative = clash
                findings.append((
                    "conflict", first, second,
                    _("One says « %(positive)s », the other « %(negative)s », "
                      "about the same subject.",
                      positive=positive, negative=negative),
                ))
                continue

            ratio = difflib.SequenceMatcher(None, body_a, body_b).ratio()
            if ratio >= _DUPLICATE_RATIO:
                findings.append((
                    "duplicate", first, second,
                    _("Near-identical wording (%(pct)d%% match). Keep one.",
                      pct=round(ratio * 100)),
                ))
        return findings

    def action_check_coherence(self):
        """Scan the selected instructions (or all of them) and report."""
        records = self or self.search([])
        findings = records._coherence_findings()

        if not findings:
            body = _("No duplicate and no contradiction found across %s active "
                     "instruction(s).") % len(records.filtered("active"))
        else:
            chunks = []
            for kind, first, second, detail in findings:
                label = _("Duplicate") if kind == "duplicate" else _("Conflict")
                chunks.append(
                    f"{label} — « {first.name} » ↔ « {second.name} »\n  {detail}"
                )
            body = "\n\n".join(chunks)

        report = self.env["claude.chat.coherence.report"].create({"body": body})
        return {
            "type": "ir.actions.act_window",
            "name": _("Coherence Check"),
            "res_model": "claude.chat.coherence.report",
            "res_id": report.id,
            "view_mode": "form",
            "target": "new",
        }


class ClaudeChatCoherenceReport(models.TransientModel):
    _name = "claude.chat.coherence.report"
    _description = "Claude Steering Coherence Report"

    body = fields.Text(string="Findings", readonly=True)

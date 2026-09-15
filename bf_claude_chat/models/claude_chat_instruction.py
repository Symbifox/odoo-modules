"""Steering instructions ("Consignes") composed into Claude's system prompt.

Each instruction is a short directive the user wants Claude to respect. An
instruction is either global (every conversation) or scoped to one Odoo model,
and either shared with everyone (no owner) or private to a single user.

Only active instructions reach the prompt. The composed block is built server
side by :meth:`_build_prompt_block` and shipped to the bridge inside the chat
payload, alongside the page context.
"""

import difflib
import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Ceiling on the composed steering block. The controller and the bridge both
# cut at this length, so the block is fitted here, on a line boundary, and the
# cut is logged and shown rather than silent.
STEERING_MAX_CHARS = 4000

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
    block_chars = fields.Integer(
        compute="_compute_block_usage",
        string="Composed block (characters)",
        help="Size of the block Gen receives for this instruction's audience, "
             "this instruction included, against the ceiling.",
    )
    block_usage = fields.Char(
        compute="_compute_block_usage",
        string="Block usage",
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

    @staticmethod
    def _line(body):
        """One prompt line for an instruction body, or an empty string."""
        body = _SPACE_RE.sub(" ", (body or "").strip())
        return f"- {body}" if body else ""

    @api.model
    def _compose_lines(self, res_model=None):
        """One line per applicable instruction, in sequence order, no ceiling."""
        lines = (self._line(instruction.body)
                 for instruction in self._applicable(res_model))
        return [line for line in lines if line]

    @staticmethod
    def _fit_lines(lines, max_chars=STEERING_MAX_CHARS):
        """Keep whole lines, in order, until the next one would not fit.

        Cutting on a line boundary is the point: a block sliced mid-sentence
        hands Claude half a directive, which reads as a whole one. Returns
        ``(kept, dropped)``.
        """
        kept, total = [], 0
        for line in lines:
            extra = len(line) + (1 if kept else 0)
            if total + extra > max_chars:
                break
            kept.append(line)
            total += extra
        return kept, len(lines) - len(kept)

    @staticmethod
    def _block_size(lines):
        return sum(len(line) for line in lines) + max(len(lines) - 1, 0)

    @api.model
    def _build_prompt_block(self, res_model=None, max_chars=STEERING_MAX_CHARS):
        """Compose the steering block, or an empty string when there is none.

        Bounded to ``max_chars`` on a line boundary; what does not fit is left
        out, last in sequence first, and said in the log.
        """
        kept, dropped = self._fit_lines(self._compose_lines(res_model), max_chars)
        if dropped:
            _logger.warning(
                "bf_claude_chat: steering block for uid %s exceeds %s characters; "
                "%s instruction(s) left out, last in sequence first.",
                self.env.uid, max_chars, dropped,
            )
        return "\n".join(kept)

    # ------------------------------------------------------------------
    # Ceiling gauge
    # ------------------------------------------------------------------
    def _projected_lines(self):
        """The lines this record's audience would receive, with the record as
        it stands in the form, saved or not, in place of its database row."""
        self.ensure_one()
        res_model = self.res_model if self.scope == "model" else None
        origin_id = self._origin.id
        rows = []
        for instruction in self._applicable(res_model):
            if origin_id and instruction.id == origin_id:
                continue
            line = self._line(instruction.body)
            if line:
                rows.append((instruction.sequence, instruction.id, line))
        mine = self._line(self.body)
        applies = self.active and (
            not self.user_id or self.user_id.id == self.env.uid)
        if mine and applies:
            rows.append((self.sequence or 0, origin_id or 0, mine))
        rows.sort(key=lambda row: (row[0], row[1]))
        return [row[2] for row in rows]

    @api.depends("body", "active", "scope", "model_id", "user_id", "sequence")
    def _compute_block_usage(self):
        for rec in self:
            lines = rec._projected_lines()
            size = self._block_size(lines)
            _kept, dropped = self._fit_lines(lines)
            rec.block_chars = size
            if dropped:
                rec.block_usage = _(
                    "%(size)s / %(max)s characters: %(dropped)s instruction(s) "
                    "would be left out",
                    size=size, max=STEERING_MAX_CHARS, dropped=dropped)
            else:
                rec.block_usage = _("%(size)s / %(max)s characters",
                                    size=size, max=STEERING_MAX_CHARS)

    @api.onchange("body", "active", "scope", "model_id", "user_id", "sequence")
    def _onchange_block_ceiling(self):
        lines = self._projected_lines()
        size = self._block_size(lines)
        if size <= STEERING_MAX_CHARS:
            return None
        _kept, dropped = self._fit_lines(lines)
        return {"warning": {
            "title": _("Over the steering ceiling"),
            "message": _(
                "With this instruction, the block Gen receives would be "
                "%(size)s characters for a ceiling of %(max)s. The last "
                "%(dropped)s instruction(s) in sequence order would be left "
                "out. Shorten, reorder or archive.",
                size=size, max=STEERING_MAX_CHARS, dropped=dropped),
        }}

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

        lines = self._compose_lines(None)
        size = self._block_size(lines)
        _kept, dropped = self._fit_lines(lines)
        gauge = _("Global block for you: %(size)s / %(max)s characters.",
                  size=size, max=STEERING_MAX_CHARS)
        if dropped:
            gauge += " " + _("%(dropped)s instruction(s) are left out.",
                             dropped=dropped)
        body = gauge + "\n\n" + body
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

from odoo import api, fields, models


class BfDocumentExpiryPolicy(models.Model):
    """Attaches the expiry policy to the retention schedule.

    Without this bridge, `retention_days` is an orphan number: it sets an
    expiry date without saying which rule it derives from, under which legal
    basis, or what becomes of the document in the end. The retention schedule
    answers these three questions, and it is the one the destruction register
    entry will quote.
    """

    _inherit = "bf.document.expiry.policy"

    retention_calendar_id = fields.Many2one(
        "privacy.retention.calendar",
        string="Retention rule",
        ondelete="restrict",
        help="The retention schedule rule this policy applies. It carries the "
        "legal basis, the final disposition and the destruction method; it is "
        "the one quoted in the register.",
    )
    retention_calendar_days = fields.Integer(
        string="Rule retention (days)",
        related="retention_calendar_id.total_retention_days",
        readonly=True,
        help="Total retention period in days",
    )
    privacy_alignment_note = fields.Char(
        string="Alignment",
        compute="_compute_privacy_alignment_note",
        help="What the policy and its retention rule say about each other.",
    )

    @api.depends("retention_days", "retention_calendar_id",
                 "retention_calendar_id.total_retention_days")
    @api.depends_context("lang")
    def _compute_privacy_alignment_note(self):
        """Says when the two periods contradict each other, without deciding.

        ⚠️ Not a constraint. A schedule rule of 0 years is a NORMAL case here:
        it documents a regime whose actual period is carried by the policy, as
        for secure transfers. Refusing a policy longer than its rule would
        break that case. And when the two periods truly contradict each other,
        guessing which one is right means deciding in place of the person
        responsible for the processing.

        The note is computed text: it depends on the reader's language, or a
        value computed in one language would be served in the other.
        """
        _ = self.env._
        for rec in self:
            calendar = rec.retention_calendar_id
            if not calendar:
                rec.privacy_alignment_note = _(
                    "No retention rule attached: this period does not derive "
                    "from any documented rule.")
                continue
            total = calendar.total_retention_days
            if not total:
                rec.privacy_alignment_note = _(
                    "Rule \"%(code)s\" sets no period: this policy carries it "
                    "(%(days)s day(s)).",
                    code=calendar.code, days=rec.retention_days)
            elif rec.retention_days > total:
                rec.privacy_alignment_note = _(
                    "⚠️ %(days)s day(s) here, %(total)s under rule "
                    "\"%(code)s\". The document will live longer than the rule "
                    "provides.",
                    days=rec.retention_days, total=total, code=calendar.code)
            else:
                rec.privacy_alignment_note = _(
                    "%(days)s day(s), within the %(total)s of rule \"%(code)s\".",
                    days=rec.retention_days, total=total, code=calendar.code)

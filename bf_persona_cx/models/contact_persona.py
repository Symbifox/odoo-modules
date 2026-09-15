from datetime import timedelta

from odoo import _, api, fields, models

# A score below this share of its scale is a warning, whatever the kind:
# 6/10 on an NPS (a detractor), 3/5 on a satisfaction rating.
LOW_SCORE_SHARE = 0.7
# Feedback older than this no longer speaks for the relationship.
FEEDBACK_WINDOW_DAYS = 365
OPEN_COMPLAINT_STATES = ("received", "ack", "analysis")
COMMENT_EXCERPT = 160


class ContactPersona(models.Model):
    _inherit = "contact.persona"

    cx_feedback_ids = fields.Many2many(
        "bf.cx.feedback", compute="_compute_cx", string="Retours d'expérience",
    )
    cx_complaint_ids = fields.Many2many(
        "bf.cx.complaint", compute="_compute_cx", string="Plaintes",
    )
    cx_last_feedback_id = fields.Many2one(
        "bf.cx.feedback", compute="_compute_cx", string="Dernier retour",
    )
    cx_open_complaint_count = fields.Integer(compute="_compute_cx", string="Plaintes ouvertes")
    # Not a related field: older bf_cx releases have no exclusion field at all,
    # and a related field on a missing one stops the registry from loading.
    cx_excluded = fields.Boolean(
        compute="_compute_cx_excluded", string="Ne pas solliciter",
    )

    @api.depends("partner_id")
    def _compute_cx_excluded(self):
        Partner = self.env["res.partner"]
        field = next(
            (f for f in ("bf_cx_exclude_effective", "bf_cx_exclude") if f in Partner._fields),
            None,
        )
        for persona in self:
            persona.cx_excluded = bool(field and persona.partner_id and persona.partner_id[field])

    def _cx_partner_domain(self):
        """The contact itself, or the people of a company persona."""
        self.ensure_one()
        partner = self.partner_id
        if partner.is_company:
            return [("partner_id", "child_of", partner.id)]
        return [("partner_id", "=", partner.id)]

    @api.depends("partner_id")
    def _compute_cx(self):
        Feedback = self.env["bf.cx.feedback"].sudo()
        Complaint = self.env["bf.cx.complaint"].sudo()
        since = fields.Date.context_today(self) - timedelta(days=FEEDBACK_WINDOW_DAYS)
        for persona in self:
            if not persona.partner_id:
                persona.cx_feedback_ids = persona.cx_complaint_ids = False
                persona.cx_last_feedback_id = False
                persona.cx_open_complaint_count = 0
                continue
            domain = persona._cx_partner_domain()
            feedback = Feedback.search(domain + [("kind", "!=", "internal")], order="date desc, id desc")
            complaints = Complaint.search(domain, order="date_received desc, id desc")
            persona.cx_feedback_ids = feedback
            persona.cx_complaint_ids = complaints
            persona.cx_last_feedback_id = feedback.filtered(
                lambda f: f.date and f.date >= since and (f.score_max or f.comment)
            )[:1]
            persona.cx_open_complaint_count = len(
                complaints.filtered(lambda c: c.state in OPEN_COMPLAINT_STATES)
            )

    @staticmethod
    def _cx_excerpt(text):
        text = " ".join((text or "").split())
        if len(text) <= COMMENT_EXCERPT:
            return text
        return text[:COMMENT_EXCERPT].rsplit(" ", 1)[0] + "…"

    def _cx_score_label(self, feedback):
        label = "%s/%s" % (
            ("%g" % feedback.score), ("%g" % feedback.score_max),
        )
        if feedback.kind == "nps" and feedback.nps_bucket:
            bucket = dict(feedback._fields["nps_bucket"]._description_selection(self.env))[feedback.nps_bucket]
            return _("NPS %(score)s (%(bucket)s)") % {"score": label, "bucket": bucket.lower()}
        kind = dict(feedback._fields["kind"]._description_selection(self.env))[feedback.kind]
        return _("%(kind)s %(score)s") % {"kind": kind, "score": label}

    def _cx_is_low(self, feedback):
        if feedback.kind == "nps" and feedback.nps_bucket:
            return feedback.nps_bucket == "detractor"
        return bool(feedback.score_max) and feedback.score / feedback.score_max < LOW_SCORE_SHARE

    def _health_signals(self):
        signals = super()._health_signals()
        self.ensure_one()
        cx = []
        open_complaints = self.sudo().cx_complaint_ids.filtered(lambda c: c.state in OPEN_COMPLAINT_STATES)
        for complaint in open_complaints[:2]:
            cx.append((
                "degraded",
                _("plainte ouverte depuis le %(date)s : %(subject)s") % {
                    "date": fields.Date.to_string(complaint.date_received.date()) if complaint.date_received else "?",
                    "subject": self._cx_excerpt(complaint.name),
                },
            ))
        last = self.sudo().cx_last_feedback_id
        if last and last.score_max and self._cx_is_low(last):
            reason = _("%(score)s le %(date)s") % {
                "score": self._cx_score_label(last), "date": fields.Date.to_string(last.date),
            }
            if last.comment:
                reason += " : « %s »" % self._cx_excerpt(last.comment)
            cx.append(("watch", reason))
        # Most severe first, across base and bridge signals.
        return sorted(cx + signals, key=lambda s: 0 if s[0] == "degraded" else 1)

    def _summary_extra_lines(self):
        lines = super()._summary_extra_lines()
        self.ensure_one()
        last = self.sudo().cx_last_feedback_id
        if last and not (last.score_max and self._cx_is_low(last)):
            line = "Expérience client : "
            line += self._cx_score_label(last) if last.score_max else "commentaire"
            line += f" le {fields.Date.to_string(last.date)}"
            if last.comment:
                line += f" : « {self._cx_excerpt(last.comment)} »"
            lines.append(line + ".")
        if self.cx_excluded:
            lines.append("Ne pas solliciter (sondages, témoignages).")
        return lines

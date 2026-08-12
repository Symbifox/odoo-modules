"""Rating ingestion (continuous feedback).

Every consumed rating.rating on the database (project task ratings, future
helpdesk ratings, chatter ratings…) is mirrored into the unified feedback
registry, so email smiley feedback and NPS answers live in one place.

A rating becomes "real" when consumed=True with rating >= 1: either at
create (message_post(rating_value=...)) or at write (public /rate flow calls
rating_apply which writes rating+consumed). Both paths are hooked.
"""
import logging

from odoo import api, fields, models

from .bf_cx_feedback import param_is_true

_logger = logging.getLogger(__name__)


class RatingRating(models.Model):
    _inherit = "rating.rating"

    bf_cx_feedback_id = fields.Many2one(
        "bf.cx.feedback",
        string="Feedback d'expérience",
        readonly=True,
        copy=False,
    )

    @api.model_create_multi
    def create(self, vals_list):
        ratings = super().create(vals_list)
        ratings._bf_cx_ingest()
        return ratings

    def write(self, vals):
        res = super().write(vals)
        if "consumed" in vals or "rating" in vals:
            self._bf_cx_ingest()
        return res

    def _bf_cx_ingest(self):
        candidates = self.filtered(
            lambda r: r.consumed and r.rating >= 1 and not r.is_internal
        )
        if not candidates:
            return
        if not param_is_true(
            self.env, "bf_cx.ingest_ratings", default=True
        ):
            return
        Feedback = self.env["bf.cx.feedback"].sudo()
        for rating in candidates:
            try:
                if rating.bf_cx_feedback_id:
                    # The core REUSES the same rating.rating when the client
                    # changes their answer (rating_apply finds it by token):
                    # the mirror must follow, and a 5→1 correction must
                    # still trigger the closed loop.
                    rating._bf_cx_update_mirror()
                else:
                    rating._bf_cx_ingest_one(Feedback)
            except Exception:  # noqa: BLE001 - never break the rating flow
                _logger.exception(
                    "bf_cx: rating ingestion failed for rating %s",
                    rating.id,
                )

    def _bf_cx_update_mirror(self):
        self.ensure_one()
        feedback = self.bf_cx_feedback_id.sudo()
        needed_before = feedback._needs_followup()
        feedback.write(
            {
                "score": self.rating,
                "comment": self.feedback or "",
                "date": fields.Date.context_today(self),
            }
        )
        if not needed_before and feedback._needs_followup():
            feedback._run_closed_loop()

    def _bf_cx_ingest_one(self, Feedback):
        self.ensure_one()
        record = None
        if self.res_model and self.res_id and self.res_model in self.env:
            record = self.env[self.res_model].sudo().browse(self.res_id).exists()
        project = False
        company = self.env.company
        # String comparison only - no registry reference, so this stays safe
        # on tenants without the corresponding modules.
        source = "meeting" if self.res_model == "meeting.record" else "rating"
        if record is not None and record:
            if record._name == "project.task":
                project = record.project_id
            elif record._name == "project.project":
                project = record
            else:
                project = getattr(record, "project_id", False)
            record_company = getattr(record, "company_id", False)
            if record_company:
                company = record_company
        feedback = Feedback.create(
            {
                "partner_id": self.partner_id.id,
                "date": fields.Date.context_today(self),
                "kind": "csat",
                "source": source,
                "score": self.rating,
                "score_max": 5.0,
                "comment": self.feedback or "",
                "project_id": project and project.id,
                "company_id": company.id,
                "rating_ref_id": self.id,
                "user_id": self.rated_partner_id.user_ids[:1].id
                or self.partner_id.user_id.id,
            }
        )
        self.sudo().bf_cx_feedback_id = feedback
        feedback._run_closed_loop()

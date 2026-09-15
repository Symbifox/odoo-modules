import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class _PersonaRefreshMixin(models.AbstractModel):
    """Re-judge the relationship as soon as a feedback or a complaint moves.

    The measure runs daily; a complaint filed this morning should not wait
    for it to show on the persona list and in its stored reason.
    """

    _name = "bf.persona.cx.refresh.mixin"
    _description = "Rafraîchit le persona à chaque retour d'expérience"

    def _bf_persona_refresh(self):
        try:
            partners = self.sudo().partner_id
            partners |= partners.commercial_partner_id
            if not partners:
                return
            personas = self.env["contact.persona"].sudo().search([("partner_id", "in", partners.ids)])
            if personas:
                # The CX fields are computed without stored dependencies: drop
                # what this transaction already read before judging again.
                personas.invalidate_recordset([
                    "cx_feedback_ids", "cx_complaint_ids", "cx_last_feedback_id", "cx_open_complaint_count",
                ])
                personas._refresh_relationship_facts()
        except Exception as e:
            _logger.warning("persona refresh after %s change failed: %s", self._name, e)


class BfCxFeedback(models.Model):
    _name = "bf.cx.feedback"
    _inherit = ["bf.cx.feedback", "bf.persona.cx.refresh.mixin"]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._bf_persona_refresh()
        return records

    def write(self, vals):
        res = super().write(vals)
        if {"score", "score_max", "comment", "partner_id", "kind", "date"} & set(vals):
            self._bf_persona_refresh()
        return res


class BfCxComplaint(models.Model):
    _name = "bf.cx.complaint"
    _inherit = ["bf.cx.complaint", "bf.persona.cx.refresh.mixin"]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._bf_persona_refresh()
        return records

    def write(self, vals):
        res = super().write(vals)
        if {"state", "partner_id"} & set(vals):
            self._bf_persona_refresh()
        return res

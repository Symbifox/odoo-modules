"""Fox Quest XP when a complaint is resolved.

Hooks write() to detect the transition to state 'resolved' (set by
action_resolve or directly). The later 'closed' step awards nothing:
resolution is the milestone. A one-shot flag prevents re-awarding when a
complaint is reopened and resolved again, and everything runs inside
try/except so a gamification hiccup never blocks the complaint flow.
"""
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class BfCxComplaint(models.Model):
    _inherit = "bf.cx.complaint"

    gamification_xp_awarded = fields.Boolean(
        string="XP Fox Quest accordé",
        copy=False,
        help="Drapeau anti-double : l'XP de résolution n'est accordé "
             "qu'une seule fois par plainte.",
    )

    def write(self, vals):
        old_states = {rec.id: rec.state for rec in self}
        res = super().write(vals)
        if "state" in vals:
            for rec in self:
                old = old_states.get(rec.id)
                if old == rec.state or rec.state != "resolved":
                    continue
                self._bf_cx_award_complaint_xp(rec)
        return res

    def _bf_cx_award_complaint_xp(self, rec):
        """Award the resolution XP once, on the transition to 'resolved'."""
        if not self.env["ir.config_parameter"].sudo().get_param(
                "bf_gamification.gamification_enabled", "True") == "True":
            return
        try:
            if rec.gamification_xp_awarded:
                return
            user = rec.user_id or self.env.user
            if not user:
                return
            Profile = self.env["bf.gamification.profile"]
            profile = Profile._get_or_create_profile(user)
            Rule = self.env["bf.gamification.xp.rule"]
            rule = Rule.search([
                ("source", "=", "cx_complaint"),
                ("trigger", "=", "complete"),
                ("active", "=", True),
            ], limit=1)
            if rule:
                profile._award_xp(
                    rule.xp_amount, "cx_complaint",
                    "Plainte résolue : %s" % (rec.number or rec.name or ""),
                    reference=rec,
                )
                rec.write({"gamification_xp_awarded": True})
        except Exception:
            _logger.warning("Fox Quest: erreur XP plainte", exc_info=True)

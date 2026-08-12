"""Fox Quest XP when a closed-loop follow-up is completed.

Hooks write() to detect the transition to state 'done' (set by
action_mark_done or directly from a list view). XP goes to the follow-up
owner (user_id) or, failing that, to the user performing the action.
A one-shot flag on the record prevents re-awarding through
done -> new -> done cycles, and everything runs inside try/except so a
gamification hiccup never blocks the CX flow.
"""
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class BfCxFeedback(models.Model):
    _inherit = "bf.cx.feedback"

    gamification_xp_awarded = fields.Boolean(
        string="XP Fox Quest accordé",
        copy=False,
        help="Drapeau anti-double : l'XP de boucle fermée n'est accordé "
             "qu'une seule fois par feedback.",
    )

    def write(self, vals):
        old_states = {rec.id: rec.state for rec in self}
        res = super().write(vals)
        if "state" in vals:
            for rec in self:
                old = old_states.get(rec.id)
                if old == rec.state or rec.state != "done":
                    continue
                self._bf_cx_award_closed_loop_xp(rec, old)
        return res

    def _bf_cx_award_closed_loop_xp(self, rec, old_state):
        """Award the closed-loop XP once, on the transition to 'done'."""
        if not self.env["ir.config_parameter"].sudo().get_param(
                "bf_gamification.gamification_enabled", "True") == "True":
            return
        try:
            if rec.gamification_xp_awarded:
                return
            if old_state != "in_progress" and not rec.needs_followup:
                # Filing a record that never required a follow-up is
                # bookkeeping, not a closed loop: no XP.
                return
            user = rec.user_id or self.env.user
            if not user:
                return
            Profile = self.env["bf.gamification.profile"]
            profile = Profile._get_or_create_profile(user)
            Rule = self.env["bf.gamification.xp.rule"]
            rule = Rule.search([
                ("source", "=", "cx_feedback"),
                ("trigger", "=", "complete"),
                ("active", "=", True),
            ], limit=1)
            if rule:
                profile._award_xp(
                    rule.xp_amount, "cx_feedback",
                    "Boucle fermée complétée : %s" % (rec.display_name or ""),
                    reference=rec,
                )
                rec.write({"gamification_xp_awarded": True})
        except Exception:
            _logger.warning("Fox Quest: erreur XP boucle fermée", exc_info=True)

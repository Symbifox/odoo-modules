"""Partner smart button, feedback history and solicitation cooldown.

World-class CX programs cap how often a given contact is solicited
(over-surveying kills response rates and goodwill). Every outbound ask -
wave invitation, post-meeting rating, post-loss survey - goes through
_bf_cx_split_solicitable() and stamps _bf_cx_mark_solicited().
"""
from datetime import timedelta

from odoo import _, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    bf_cx_feedback_ids = fields.One2many(
        "bf.cx.feedback", "partner_id", string="Feedbacks d'expérience"
    )
    bf_cx_feedback_count = fields.Integer(
        compute="_compute_bf_cx_feedback_count"
    )
    bf_cx_unsubscribe_url = fields.Char(
        compute="_compute_bf_cx_unsubscribe_url",
        string="Lien de désabonnement CX",
        help="Lien signé (HMAC) inclus au pied des courriels de demande "
             "d'avis. Aboutit sur mail.blacklist, que tous les garde-fous "
             "du module respectent.",
    )
    bf_cx_last_solicited = fields.Datetime(
        string="Dernière sollicitation CX",
        copy=False,
        help="Dernière fois qu'une demande de feedback lui a été envoyée "
             "(vague, post-rencontre, post-perte). Sert au garde-fou "
             "anti-sursollicitation.",
    )

    def _compute_bf_cx_unsubscribe_url(self):
        from odoo.tools import hmac as _hmac

        for partner in self:
            token = _hmac(
                self.env(su=True), "bf_cx_unsubscribe", str(partner.id)
            )
            partner.bf_cx_unsubscribe_url = "/cx/unsubscribe/%s/%s" % (
                partner.id,
                token,
            )

    def _bf_cx_split_solicitable(self, days=None):
        """Split into (allowed, blocked) according to the outbound guards.

        Guards, in order: mail blacklist, active dunning (a client under a
        formal payment notice must never receive "rate us" the same week),
        then the solicitation cooldown. ``days`` overrides the global
        cooldown (per-program cadence: relational 90d vs transactional 30d);
        None reads the global parameter. The privacy bridge extends this
        with the do-not-contact list.
        """
        blocked = self.browse()

        # Mail blacklist (core mail): template sends bypass mass_mailing's
        # own blacklist handling, so it is enforced here.
        emails = [p.email_normalized for p in self if p.email_normalized]
        if emails:
            blacklisted = set(
                self.env["mail.blacklist"]
                .sudo()
                .search([("email", "in", emails)])
                .mapped("email")
            )
            if blacklisted:
                blocked |= self.filtered(
                    lambda p: p.email_normalized in blacklisted
                )

        # Active dunning (Blue Fox invoice follow-up module, runtime-
        # detected: no dependency). Blocked from 2nd reminder onwards.
        Move = (
            self.env["account.move"] if "account.move" in self.env else None
        )
        if Move is not None and "bf_followup_state" in Move._fields:
            remaining = self - blocked
            if remaining:
                dunned = Move.sudo()._read_group(
                    [
                        ("move_type", "=", "out_invoice"),
                        ("state", "=", "posted"),
                        ("payment_state", "in", ("not_paid", "partial")),
                        (
                            "bf_followup_state",
                            "in",
                            ("rappel_2", "mise_en_demeure"),
                        ),
                        (
                            "commercial_partner_id",
                            "in",
                            remaining.commercial_partner_id.ids,
                        ),
                    ],
                    ["commercial_partner_id"],
                    ["__count"],
                )
                dunned_ids = {partner.id for partner, _count in dunned}
                if dunned_ids:
                    blocked |= remaining.filtered(
                        lambda p: p.commercial_partner_id.id in dunned_ids
                    )

        # Solicitation cooldown.
        if days is None:
            raw = (
                self.env["ir.config_parameter"]
                .sudo()
                .get_param("bf_cx.solicitation_cooldown_days", "30")
            )
            try:
                days = int(raw)
            except (TypeError, ValueError):
                days = 30
        if days > 0:
            threshold = fields.Datetime.now() - timedelta(days=days)
            blocked |= (self - blocked).filtered(
                lambda p: p.bf_cx_last_solicited
                and p.bf_cx_last_solicited > threshold
            )
        return self - blocked, blocked

    def _bf_cx_mark_solicited(self):
        if self:
            self.sudo().write({"bf_cx_last_solicited": fields.Datetime.now()})

    def _compute_bf_cx_feedback_count(self):
        counts = {
            partner.id: count
            for partner, count in self.env["bf.cx.feedback"]._read_group(
                [("partner_id", "in", self.ids)], ["partner_id"], ["__count"]
            )
        }
        for partner in self:
            partner.bf_cx_feedback_count = counts.get(partner.id, 0)

    def action_view_bf_cx_feedback(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Feedbacks - %s") % self.display_name,
            "res_model": "bf.cx.feedback",
            "view_mode": "list,kanban,form,graph,pivot",
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }

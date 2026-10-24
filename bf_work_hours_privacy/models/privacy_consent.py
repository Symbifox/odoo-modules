from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.bf_work_hours.models.tools import acting_in_own_web_session

WORK_PURPOSES = ("bf_work_hours_privacy.purpose_work_hours", "bf_work_hours_privacy.purpose_work_insights",
                 "bf_work_hours_privacy.purpose_work_history")
# Never moved, never given an expiry, by anyone, on a mirror.
FROZEN = {"subject_partner_id", "purpose_id", "expires_at", "active", "company_id",
          "access_token_expires_at", "access_token", "given_by_partner_ids"}


class PrivacyConsent(models.Model):
    """The mirror of an activation follows the person, and only the person.

    The register is read by the people in charge of privacy: they may see that
    someone turned the feature on, and when (the notice says so). They may not
    turn it off for that person, nor may an expiry, the public link, a renewal
    or a copy. A withdrawal is accepted only from the person, in their own web
    session (not an API key, not an impersonation): it destroys their traces.
    """

    _inherit = "privacy.consent"

    def _bf_work_mirrors(self):
        purposes = [self.env.ref(x, raise_if_not_found=False) for x in WORK_PURPOSES]
        ids = [p.id for p in purposes if p]
        return self.filtered(lambda c: c.purpose_id.id in ids)

    def _bf_work_from_work(self):
        # 🔴 A context key comes from the client too: trusted under superuser only,
        # which is how the working-time module itself writes (work_consent.py).
        return self.env.su and self.env.context.get("bf_work_from_work")

    def _bf_work_check_subject(self, mirrors):
        for consent in mirrors:
            if (self.env.uid not in consent.subject_partner_id.user_ids.ids
                    or not acting_in_own_web_session(self.env)):
                raise UserError(_("This consent follows the person's own choice in Working Time: "
                                  "only they can withdraw it, from Working Time > My choices."))

    def write(self, vals):
        mirrors = self.browse() if self._bf_work_from_work() else self._bf_work_mirrors()
        if mirrors:
            if FROZEN & set(vals):
                raise UserError(_("This consent does not expire and cannot be moved to someone else."))
            if {"status", "withdrawn_at"} & set(vals):
                self._bf_work_check_subject(mirrors)
        res = super().write(vals)
        if mirrors and vals.get("status") in ("withdrawn", "expired", "refused"):
            work = self.env["bf.work.consent"].sudo().search([
                ("privacy_consent_id", "in", mirrors.ids), ("withdrawn_at", "=", False)])
            # The person withdrew from the register: same effect as from My choices.
            work.write({"withdrawn_at": fields.Datetime.now()})
        return res

    def copy(self, default=None):
        if self._bf_work_mirrors() and not self._bf_work_from_work():
            raise UserError(_("This consent mirrors an activation: it is not copied."))
        return super().copy(default)

    def unlink(self):
        if self._bf_work_mirrors() and not self._bf_work_from_work():
            raise UserError(_("This consent mirrors an activation: it is kept as evidence."))
        return super().unlink()

    def action_renew(self):
        if self._bf_work_mirrors():
            raise UserError(_("This consent does not expire: there is nothing to renew."))
        return super().action_renew()

"""Post-signature feedback request.

Hooks _finalize (called exactly once when the last signer signs; the core
row lock already serializes concurrent last-signer submissions). Opt-in via
bf_cx.sign_feedback, and the bf_cx anti-oversolicitation cooldown applies
per contact. The whole thing runs inside a savepoint: _finalize holds a row
lock inside the signing transaction, so a database error here would
otherwise poison the cursor and roll back the sealed document itself.
"""
import logging

from odoo import _, fields, models

from odoo.addons.bf_cx.models.bf_cx_feedback import param_is_true

_logger = logging.getLogger(__name__)


class BfSignRequest(models.Model):
    _inherit = "bf.sign.request"

    bf_cx_feedback_sent = fields.Boolean(
        string="Feedback post-signature envoyé", copy=False
    )

    def _rating_get_partner(self):
        """Return the main signer's contact (lowest sequence).

        The core rating implementation resolves a partner_id field, which
        bf.sign.request does not have (signers live on bf.sign.signer).
        Resolving the main signer here makes the central bf_cx solicitation
        guard, the rating access token and the mail template all target the
        right person.
        """
        signer = self.signer_ids.sorted(lambda s: (s.sequence, s.id))[:1]
        if signer and signer.partner_id:
            return signer.partner_id
        return super()._rating_get_partner()

    def _finalize(self):
        res = super()._finalize()
        try:
            with self.env.cr.savepoint():
                self._bf_cx_maybe_request_feedback()
        except Exception:  # noqa: BLE001 - never break the sealing flow
            _logger.exception(
                "bf_cx_sign: feedback request failed for request %s",
                self.ids,
            )
        return res

    def _bf_cx_maybe_request_feedback(self):
        """Send the 3-emoji rating request to the main signer, at most once.

        The central bf_cx guard on rating_send_request applies anyway; the
        explicit pre-check below only exists to leave a clean chatter trace
        when the contact is in cooldown.
        """
        self.ensure_one()
        if self.state != "signed" or self.bf_cx_feedback_sent:
            return
        if not param_is_true(self.env, "bf_cx.sign_feedback", default=False):
            return
        template = self.env.ref(
            "bf_cx_sign.mail_template_sign_rating",
            raise_if_not_found=False,
        )
        if not template:
            return
        partner = self._rating_get_partner()
        if not partner or not partner.email:
            return
        allowed, blocked = partner._bf_cx_split_solicitable()
        if blocked:
            self.message_post(
                body=_(
                    "Demande de feedback non envoyée : %s a été sollicité "
                    "récemment (garde-fou anti-sursollicitation)."
                )
                % partner.display_name
            )
            return
        self.rating_send_request(
            template, lang=partner.lang, force_send=False
        )
        partner._bf_cx_mark_solicited()
        self.bf_cx_feedback_sent = True

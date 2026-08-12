"""Post-maintenance CSAT request.

Hooks action_mark_done() on the maintenance schedule. When a scheduled
maintenance touching a client service is marked done, a 3-emoji rating
request goes to the service's client. Opt-in via bf_cx.hosting_feedback
(default off); the central bf_cx solicitation guard also applies inside
rating_send_request() itself. Internal partners (the company's own
partner) are excluded, and everything is wrapped so a feedback hiccup
can never block marking a maintenance done.

Schedules are RECURRING: action_mark_done() reuses the same record and
advances next_due, so the sent flag is per maintenance cycle. It is
reset each time a new occurrence starts; the bf_cx anti-solicitation
cooldown is what protects the client from being asked too often.
"""
import logging

from odoo import _, fields, models

from odoo.addons.bf_cx.models.bf_cx_feedback import param_is_true

_logger = logging.getLogger(__name__)


class HostingMaintenanceSchedule(models.Model):
    _inherit = "hosting.maintenance.schedule"

    bf_cx_feedback_sent = fields.Boolean(
        string="Feedback post-maintenance envoyé",
        copy=False,
        help="Indicateur par cycle de maintenance : réinitialisé à chaque "
        "nouvelle occurrence (la planification est récurrente).",
    )

    def action_mark_done(self):
        res = super().action_mark_done()
        try:
            with self.env.cr.savepoint():
                # The schedule is recurring: marking it done starts a new
                # occurrence (last_performed is set, next_due advances and
                # a fresh activity is created). Reset the per-cycle flag
                # so each maintenance cycle can ask for a CSAT again; the
                # bf_cx anti-solicitation cooldown remains the guard
                # against over-asking the same contact.
                self.write({"bf_cx_feedback_sent": False})
                self._bf_cx_maybe_request_feedback()
        except Exception:  # noqa: BLE001 - never block the maintenance flow
            _logger.exception(
                "bf_cx_hosting: feedback request failed for schedules %s",
                self.ids,
            )
        return res

    def _bf_cx_maybe_request_feedback(self):
        if not param_is_true(self.env, "bf_cx.hosting_feedback", default=False):
            return
        template = self.env.ref(
            "bf_cx_hosting.mail_template_hosting_maintenance_rating",
            raise_if_not_found=False,
        )
        if not template:
            return
        # The companies' own partners: an internal maintenance (a service
        # the company hosts for itself) must never trigger a client survey.
        internal_partners = self.env["res.company"].sudo().search([]).partner_id
        for record in self:
            if record.bf_cx_feedback_sent:
                continue
            partner = record.partner_id
            if not partner or not partner.email:
                continue
            if partner.commercial_partner_id in internal_partners:
                continue
            allowed, blocked = partner._bf_cx_split_solicitable()
            if blocked:
                record.message_post(
                    body=_(
                        "Demande de feedback non envoyée : %s a été sollicité "
                        "récemment (garde-fou anti-sursollicitation)."
                    )
                    % partner.display_name
                )
                continue
            record.rating_send_request(
                template, lang=partner.lang, force_send=False
            )
            partner._bf_cx_mark_solicited()
            record.bf_cx_feedback_sent = True

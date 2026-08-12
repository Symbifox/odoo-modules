"""SMS survey invitations for wave recipients without an email address.

The email wave path (bf_cx_wave.action_send) skips contacts that have no
email. This bridge adds a manual header button that reaches those contacts
through bf_sms_archive: one tokenized answer per contact (tagged with the
wave, so ingestion and dedup work exactly like the email path), the survey
link sent as a short SMS on the configured line. Hard cap of 5 SMS per
click (the SMS provider throttles at roughly 27 messages a day), outbound
guards applied, and no cron: every send is an explicit user action.
"""
import logging

from markupsafe import Markup

from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.bf_cx.models.bf_cx_feedback import param_is_true

_logger = logging.getLogger(__name__)

# VoIP.ms caps outbound at roughly 27 SMS a day: 5 per click leaves room
# for the regular 1:1 conversations the line exists for.
MAX_SMS_PER_CLICK = 5


class BfCxWave(models.Model):
    _inherit = "bf.cx.wave"

    def action_send_sms_invites(self):
        """Send up to MAX_SMS_PER_CLICK survey invitations by SMS.

        Candidates are wave recipients with no email but a mobile or phone
        number, not yet invited in this wave. Each one gets a tokenized
        survey.user_input (the wave-tagged answer doubles as the
        anti-double-send flag: a second click skips them) and the personal
        survey link by SMS. Manual action only, never called by a cron.
        """
        if not param_is_true(self.env, "bf_cx.sms_invite", default=False):
            raise UserError(
                _("Les invitations par SMS sont désactivées. Activez "
                  "« Invitations de sondage par SMS » dans les paramètres "
                  "Expérience client.")
            )
        line = self._bf_cx_sms_get_line()
        base = (
            self.env["ir.config_parameter"].sudo().get_param("web.base.url")
            or ""
        ).rstrip("/")
        for wave in self:
            if wave.state == "closed":
                raise UserError(_("La vague « %s » est fermée.") % wave.name)
            program = wave.program_id
            if not program.survey_id:
                raise UserError(
                    _("Le programme « %s » n'a pas de sondage.") % program.name
                )
            already = wave.user_input_ids.partner_id
            candidates = (wave.partner_ids - already).filtered(
                lambda p: not p.email and (p.mobile or p.phone)
            )
            if program.program_type == "internal":
                # Same rule as the email path: the anti-oversolicitation
                # guard is a CLIENT concept, an internal pulse must neither
                # block itself nor burn the client solicitation budget.
                cooled = self.env["res.partner"]
            else:
                candidates, cooled = candidates._bf_cx_split_solicitable(
                    days=program.cooldown_days or None
                )
            if not candidates:
                if wave.state == "draft":
                    if cooled:
                        raise UserError(
                            _("Aucun destinataire à inviter par SMS : %s "
                              "sollicité(s) récemment (garde-fou "
                              "anti-sursollicitation).")
                            % ", ".join(cooled.mapped("display_name"))
                        )
                    raise UserError(
                        _("Aucun destinataire sans courriel avec un numéro "
                          "de téléphone à inviter par SMS.")
                    )
                wave.message_post(
                    body=_("Aucun nouveau destinataire à inviter par SMS.")
                )
                continue
            overflow = candidates[MAX_SMS_PER_CLICK:]
            batch = candidates[:MAX_SMS_PER_CLICK]
            sent = self.env["res.partner"]
            failed = self.env["res.partner"]
            for partner in batch:
                answer = None
                try:
                    answer = program.survey_id.sudo()._create_answer(
                        partner=partner,
                        check_attempts=False,
                        deadline=wave.deadline,
                        bf_cx_wave_id=wave.id,
                    )
                    body = _(
                        "Bonjour %(name)s, votre avis compte pour nous. "
                        "Répondez à notre court sondage (2 minutes) : "
                        "%(url)s Merci !",
                        name=partner.name,
                        url=base + answer.get_start_url(),
                    )
                    self.env["sms.archive.message"].action_send(
                        line.id, partner.mobile or partner.phone, body
                    )
                    sent |= partner
                except Exception:  # noqa: BLE001 - one bad number must not kill the batch
                    _logger.exception(
                        "bf_cx_sms: SMS invite failed for partner %s "
                        "(wave %s)",
                        partner.id,
                        wave.id,
                    )
                    failed |= partner
                    if answer is not None:
                        # Drop the orphaned answer so a later click can
                        # retry this contact (the dedup above would
                        # otherwise skip them forever).
                        try:
                            answer.sudo().unlink()
                        except Exception:  # noqa: BLE001 - cleanup only
                            _logger.exception(
                                "bf_cx_sms: could not clean up answer of "
                                "partner %s (wave %s)",
                                partner.id,
                                wave.id,
                            )
            if sent and program.program_type != "internal":
                sent._bf_cx_mark_solicited()
            body_lines = [
                _("%(count)d invitation(s) envoyée(s) par SMS "
                  "(ligne « %(line)s »).",
                  count=len(sent),
                  line=line.label)
            ]
            if failed:
                body_lines.append(
                    _("Échec d'envoi SMS (voir le journal du serveur) : %s")
                    % ", ".join(failed.mapped("display_name"))
                )
            if cooled:
                body_lines.append(
                    _("Reportés par les garde-fous (sollicitation récente, "
                      "liste à ne pas contacter, recouvrement) : %s")
                    % ", ".join(cooled.mapped("display_name"))
                )
            if overflow:
                body_lines.append(
                    _("Limite de %(cap)d SMS par clic atteinte : "
                      "%(count)d destinataire(s) en attente. Relancer le "
                      "bouton plus tard (plafond quotidien du fournisseur "
                      "SMS).",
                      cap=MAX_SMS_PER_CLICK,
                      count=len(overflow))
                )
            wave.message_post(body=Markup("<br/>").join(body_lines))
            if sent and wave.state == "draft":
                vals = {"state": "sent"}
                if not wave.sent_date:
                    vals["sent_date"] = fields.Datetime.now()
                wave.write(vals)
        return True

    def _bf_cx_sms_get_line(self):
        """Resolve the sending line: the configured parameter first, then
        the first active SMS-enabled line (default line preferred)."""
        Line = self.env["sms.archive.line"].sudo()
        raw = (
            self.env["ir.config_parameter"].sudo().get_param("bf_cx.sms_line_id")
        )
        try:
            line_id = int(raw or 0)
        except (TypeError, ValueError):
            line_id = 0
        line = Line.browse(line_id).exists() if line_id else Line
        if line and not (line.active and line.sms_enabled):
            line = Line
        if not line:
            line = Line.search(
                [("active", "=", True), ("sms_enabled", "=", True)],
                order="is_default desc, id asc",
                limit=1,
            )
        if not line:
            raise UserError(
                _("Aucune ligne SMS active. Configurez une ligne d'envoi "
                  "dans les paramètres Expérience client ou dans le module "
                  "SMS.")
            )
        return line

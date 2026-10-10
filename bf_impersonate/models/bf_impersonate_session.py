"""Le journal des incarnations.

Aucune ACL n'y donne l'écriture, à personne, administrateurs compris : le
code écrit en ``sudo()`` et lui seul. L'OCA ``impersonate_login`` donnait
l'écriture à tout interne, qui pouvait donc réécrire qui avait incarné qui.
"""
from datetime import timedelta

import pytz
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools import format_date, format_time

NOTIFY_PARAM = "bf_impersonate.notify"
NOTIFY_NEVER = "never"
NOTIFY_START = "start"
NOTIFY_START_END = "start_end"


class BfImpersonateSession(models.Model):
    _name = "bf.impersonate.session"
    _description = "Impersonation session"
    _inherit = ["mail.thread"]
    _order = "date_start desc, id desc"

    user_id = fields.Many2one(
        "res.users", string="Impersonated by", required=True, readonly=True,
        index=True, ondelete="restrict")
    target_user_id = fields.Many2one(
        "res.users", string="Person seen", required=True, readonly=True,
        index=True, ondelete="restrict")
    reason = fields.Text(required=True, readonly=True)
    mode = fields.Selection(
        [("read", "Read only"), ("write", "Read and write")],
        required=True, readonly=True)
    date_start = fields.Datetime(
        string="Started at", required=True, readonly=True,
        default=fields.Datetime.now)
    expires_at = fields.Datetime(string="Planned end", required=True, readonly=True)
    date_end = fields.Datetime(string="Actual end", readonly=True)
    end_reason = fields.Selection(
        [
            ("manual", "Returned to own account"),
            ("expired", "Time limit reached"),
            ("forced", "Ended by an administrator"),
            ("logout", "Logged out"),
        ],
        readonly=True)
    state = fields.Selection(
        [("open", "In progress"), ("closed", "Ended")],
        compute="_compute_state", store=True)
    ip_address = fields.Char(string="IP address", readonly=True, groups="base.group_system")
    line_ids = fields.One2many(
        "bf.impersonate.session.line", "session_id", string="Actions",
        readonly=True)
    line_count = fields.Integer(compute="_compute_line_count", string="Actions taken")
    duration_minutes = fields.Integer(compute="_compute_duration", string="Minutes")

    @api.depends("date_end")
    def _compute_state(self):
        for journal in self:
            journal.state = "closed" if journal.date_end else "open"

    @api.depends("line_ids")
    def _compute_line_count(self):
        for journal in self:
            journal.line_count = len(journal.line_ids)

    @api.depends("date_start", "date_end", "expires_at")
    def _compute_duration(self):
        now = fields.Datetime.now()
        for journal in self:
            end = journal.date_end or min(now, journal.expires_at or now)
            start = journal.date_start or end
            journal.duration_minutes = max(0, int((end - start).total_seconds() // 60))

    @api.depends("user_id", "target_user_id", "date_start")
    def _compute_display_name(self):
        for journal in self:
            journal.display_name = _(
                "%(origin)s as %(target)s",
                origin=journal.user_id.name or "",
                target=journal.target_user_id.name or "",
            )

    # ------------------------------------------------------------------
    # Ouverture et fermeture, appelées par le code seulement

    @api.model
    def _bf_open(self, origin, target, reason, mode, minutes, ip_address=None):
        now = fields.Datetime.now()
        journal = self.sudo().with_context(
            mail_create_nosubscribe=True, mail_create_nolog=True,
            tracking_disable=True,
        ).create({
            "user_id": origin.id,
            "target_user_id": target.id,
            "reason": reason,
            "mode": mode,
            "date_start": now,
            "expires_at": now + timedelta(minutes=minutes),
            "ip_address": ip_address,
        })
        journal._bf_notify("start")
        return journal

    def _bf_close(self, reason, when=None, notify=True):
        """Fermer les entrées encore ouvertes ; rend celles qu'on vient de fermer."""
        closed = self.sudo().filtered(lambda j: not j.date_end)
        for journal in closed:
            journal.write({
                "date_end": when or fields.Datetime.now(),
                "end_reason": reason,
            })
            if notify:
                journal._bf_notify("end")
        return closed

    def action_force_end(self):
        """Mettre fin à une incarnation en cours : la session de l'incarnateur
        le voit à sa requête suivante et revient à son compte."""
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only an administrator can end someone else's impersonation."))
        self.check_access("read")
        self._bf_close("forced")
        return True

    @api.model
    def _cron_close_expired(self):
        """Fermer les entrées dont la durée est passée : l'incarnateur est revenu
        à son compte à sa première requête après l'échéance, sans écrire en base
        (le curseur y est souvent en lecture seule), ou s'est déconnecté."""
        now = fields.Datetime.now()
        for journal in self.sudo().search([
            ("date_end", "=", False), ("expires_at", "<=", now),
        ]):
            journal._bf_close("expired", when=journal.expires_at)

    # ------------------------------------------------------------------
    # Avis à la personne

    def _bf_notify(self, moment):
        self.ensure_one()
        policy = self.env["ir.config_parameter"].sudo().get_param(
            NOTIFY_PARAM, NOTIFY_START)
        if policy == NOTIFY_NEVER:
            return
        if moment == "end" and policy != NOTIFY_START_END:
            return
        target = self.target_user_id
        # Un contexte neuf, pas celui du client : le navigateur pourrait sinon
        # repousser l'avis (mail_defer_seconds, mail_notify_force_send).
        journal = self.sudo().with_context({"lang": target.lang or self.env.lang})
        subject, body = journal._bf_notification(moment)
        # Envoyé sur-le-champ (force_send), jamais mis en file ni différé : la file
        # des courriels peut être inactive, et mail_post_defer repousserait l'avis.
        # L'avis de début part AVANT la validation : après elle, la session est déjà
        # incarnée et les gardes d'envoi (à raison) le refuseraient. Vu au banc le
        # 2026-10-09 sur une base sans mail_post_defer. L'avis de fin, lui, part
        # après la restauration : il garde l'envoi après validation, pour qu'une
        # panne du serveur de courriel ne bloque pas le retour au compte.
        journal.message_notify(
            partner_ids=target.partner_id.ids,
            author_id=self.user_id.partner_id.id,
            subject=subject,
            body=body,
            email_layout_xmlid="bf_onboarding_base.bf_mail_layout",
            force_send=True,
            send_after_commit=moment != "start",
        )

    def _bf_minutes(self, minutes):
        if minutes < 1:
            return self.env._("less than a minute")
        if minutes == 1:
            return self.env._("1 minute")
        return self.env._("%s minutes", minutes)

    def _bf_when(self, value):
        """« 8 octobre 2026 à 14 h 05 », dans le fuseau et la langue de la personne."""
        tz = pytz.timezone(self.target_user_id.tz or "UTC")
        local = pytz.utc.localize(value).astimezone(tz)
        return self.env._(
            "%(date)s at %(time)s",
            date=format_date(self.env, local.date(), date_format="long"),
            time=format_time(self.env, local.time(), time_format="short"),
        )

    def _bf_notification(self, moment):
        self.ensure_one()
        env = self.env
        origin = self.user_id.name
        mode = dict(self._fields["mode"]._description_selection(env)).get(self.mode)
        if moment == "start":
            subject = env._("%s is seeing Symbifox as you", origin)
            body = Markup("<p>%s</p><p>%s</p><p>%s</p>") % (
                env._(
                    "%(origin)s opened a session as you on %(start)s, %(mode)s, "
                    "for at most %(duration)s.",
                    origin=origin,
                    start=self._bf_when(self.date_start),
                    mode=mode.lower() if mode else "",
                    duration=self._bf_minutes(
                        int((self.expires_at - self.date_start).total_seconds() // 60)),
                ),
                env._("Reason: %s", self.reason),
                env._("The journal entry linked to this message lists what was done."),
            )
            return subject, body
        end_label = dict(self._fields["end_reason"]._description_selection(env)).get(
            self.end_reason) or ""
        subject = env._("The session of %s as you has ended", origin)
        items = Markup("").join(
            Markup("<li>%s</li>") % line._bf_label() for line in self.line_ids[:20])
        body = Markup("<p>%s</p>") % env._(
            "Ended on %(end)s (%(reason)s) after %(duration)s. Actions taken: %(count)s.",
            end=self._bf_when(self.date_end),
            reason=end_label.lower(),
            duration=self._bf_minutes(self.duration_minutes),
            count=len(self.line_ids),
        )
        if items:
            body += Markup("<ul>%s</ul>") % items
        return subject, body


class BfImpersonateSessionLine(models.Model):
    _name = "bf.impersonate.session.line"
    _description = "Impersonation journal line"
    _order = "date, id"

    session_id = fields.Many2one(
        "bf.impersonate.session", required=True, readonly=True,
        index=True, ondelete="cascade")
    date = fields.Datetime(readonly=True, default=fields.Datetime.now)
    route = fields.Char(readonly=True)
    model = fields.Char(readonly=True)
    method = fields.Char(readonly=True)
    record_ids = fields.Char(string="Records", readonly=True)
    field_names = fields.Char(string="Fields", readonly=True)
    details = fields.Text(
        readonly=True,
        help="Every write made as the person during this call: model, "
             "operation, records and fields.")

    def _bf_label(self):
        """« Tâche [42] : modifiée (name) », lisible par la personne avisée."""
        self.ensure_one()
        env = self.env
        if not self.model:
            return self.route or ""
        model = env["ir.model"]._get(self.model)
        label = model.name if model else self.model
        if self.record_ids:
            label += f" [{self.record_ids}]"
        if self.method == "message_post" or self.route == "/mail/message/post":
            verb = env._("note posted")
        elif self.method in ("web_save", "write"):
            verb = env._("changed")
        elif self.method == "create":
            verb = env._("created")
        elif self.method == "unlink":
            verb = env._("deleted")
        else:
            verb = self.method or self.route or ""
        label += f" : {verb}"
        if self.field_names:
            label += f" ({self.field_names})"
        return label

"""Préférences de notification de l'agent : événement × canal × mode.

Sans préférence, rien ne change : Odoo notifie comme avant (boîte de réception
ou courriel, selon le réglage de l'usager). Une préférence prend la main sur
un événement : l'agent est retiré des destinataires ordinaires, et la
notification part par le canal choisi (Odoo, courriel, ntfy), tout de suite
ou dans un condensé horaire ou quotidien. Une priorité très élevée part
toujours tout de suite.

Les mentions restent à Odoo : une mention s'adresse à une personne, elle ne
se condense pas.
"""
import json
import logging
import urllib.request
from datetime import timedelta

import pytz
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from .dispatch_mark import DISPATCH_MARK

_logger = logging.getLogger(__name__)

EVENTS = [
    ("new_ticket", "Nouveau billet dans mes équipes"),
    ("assigned", "Billet qui m'est assigné"),
    ("client_reply", "Réponse d'un client"),
    ("sla_risk", "SLA à risque"),
    ("sla_breach", "SLA dépassé"),
]
CHANNELS = [
    ("odoo", "Odoo (boîte de réception)"),
    ("email", "Courriel"),
    ("ntfy", "ntfy (téléphone)"),
    ("none", "Aucune notification"),
]
MODES = [
    ("immediate", "Tout de suite"),
    ("hourly", "Condensé toutes les heures"),
    ("daily", "Condensé quotidien"),
]
DAILY_HOUR = 8


class HelpdeskNotifyPref(models.Model):
    _name = "helpdesk.notify.pref"
    _description = "Préférence de notification d'assistance"
    _order = "user_id, event"

    user_id = fields.Many2one(
        "res.users", string="Agent", required=True, index=True,
        default=lambda self: self.env.user, ondelete="cascade",
        domain="[('share', '=', False)]",
    )
    event = fields.Selection(EVENTS, string="Événement", required=True)
    channel = fields.Selection(CHANNELS, string="Canal", required=True, default="odoo")
    mode = fields.Selection(MODES, string="Quand", required=True, default="immediate")

    _sql_constraints = [
        ("user_event_uniq", "unique(user_id, event)",
         "Une seule préférence par événement et par agent."),
    ]

    def write(self, vals):
        # Les règles d'Odoo 18 se vérifient avant l'écriture : sans ce contrôle,
        # un agent donnait sa préférence « aucun » à un autre et le rendait sourd.
        if "user_id" in vals and not self.env.su and vals["user_id"] != self.env.uid \
                and not self.env.user.has_group("helpdesk_mgmt.group_helpdesk_manager"):
            raise AccessError(_("Une préférence de notification reste à son agent."))
        return super().write(vals)

    @api.model
    def _for(self, users, event):
        """{user_id: pref} des préférences posées pour cet événement."""
        prefs = self.sudo().search([("user_id", "in", users.ids), ("event", "=", event)])
        return {p.user_id.id: p for p in prefs}


class ResUsers(models.Model):
    _inherit = "res.users"

    helpdesk_notify_pref_ids = fields.One2many(
        "helpdesk.notify.pref", "user_id", string="Notifications d'assistance")
    helpdesk_notify_daily_last = fields.Date(readonly=True, copy=False)


class HelpdeskAgentNotifyItem(models.Model):
    _name = "helpdesk.agent.notify.item"
    _description = "Notification d'assistance en attente de condensé"
    _order = "id"

    user_id = fields.Many2one("res.users", required=True, index=True, ondelete="cascade")
    ticket_id = fields.Many2one("helpdesk.ticket", required=True, ondelete="cascade")
    event = fields.Selection(EVENTS, required=True)
    channel = fields.Selection(CHANNELS, required=True)
    mode = fields.Selection(MODES, required=True)
    summary = fields.Char(required=True)
    sent = fields.Boolean(default=False, index=True)
    sent_date = fields.Datetime(readonly=True)

    # ------------------------------------------------------------------
    # Envoi
    # ------------------------------------------------------------------
    @api.model
    def _dispatch(self, ticket, users, event, summary):
        """Notifier `users` de `event` selon leurs préférences.

        Rend les usagers SANS préférence pour cet événement : à l'appelant de
        leur garder le comportement d'origine.
        """
        prefs = self.env["helpdesk.notify.pref"]._for(users, event)
        urgent = (ticket.priority or "0") == "3"
        for user in users.filtered(lambda u: u.id in prefs):
            pref = prefs[user.id]
            if pref.channel == "none":
                continue
            if pref.mode == "immediate" or urgent:
                self._deliver(user, pref.channel, ticket, summary)
            else:
                self.sudo().create({
                    "user_id": user.id, "ticket_id": ticket.id, "event": event,
                    "channel": pref.channel, "mode": pref.mode, "summary": summary,
                })
        return users.filtered(lambda u: u.id not in prefs)

    @api.model
    def _deliver(self, user, channel, ticket, summary):
        if channel == "ntfy" and self._ntfy(user, summary, ticket):
            return
        # ntfy absent ou en échec : la notification ne se perd pas, elle
        # tombe dans la boîte de réception Odoo.
        notif = "email" if channel == "email" else "inbox"
        ticket.sudo().with_context(
            bf_hd_dispatch=DISPATCH_MARK,
            bf_hd_force_notif={user.partner_id.id: notif},
            mail_notify_author=True,
        ).message_notify(
            partner_ids=user.partner_id.ids,
            subject="[%s] %s" % (ticket.number, summary),
            body=Markup("<p>%s</p>") % summary,
            email_layout_xmlid=ticket.MAIL_LAYOUT,
            # Envoyé par le système : sans signature, il signait « -- System ».
            email_add_signature=False,
        )

    @api.model
    def _ntfy(self, user, summary, ticket=None, count=None):
        url = self.env["ir.config_parameter"].sudo().get_param(
            "bf_helpdesk.ntfy_webhook_url")
        if not url:
            return False
        payload = {
            "_model": "helpdesk.ticket",
            "_action": "agent_notification",
            "recipient": user.login,
            "recipient_email": user.email or "",
            "title": summary,
        }
        if ticket:
            payload.update({
                "_id": ticket.id, "number": ticket.number,
                "url": ticket.get_base_url() + "/odoo/helpdesk-tickets/%s" % ticket.id,
            })
        if count is not None:
            payload["count"] = count
        try:
            request = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=5) as resp:
                return resp.status < 400
        except Exception:  # noqa: BLE001
            _logger.warning("bf_helpdesk: ntfy injoignable pour %s", user.login)
            return False

    # ------------------------------------------------------------------
    # Condensés
    # ------------------------------------------------------------------
    @api.model
    def _daily_handled_by_digest(self, user):
        """Vrai si le condensé quotidien par courriel de `user` part dans le digest du matin.

        bf_helpdesk_digest le surcharge ; sans lui, ou pour un agent qui ne
        reçoit pas le digest, bf_helpdesk envoie son propre courriel quotidien.
        """
        return False

    @api.model
    def _user_local_now(self, user):
        try:
            zone = pytz.timezone(user.tz or "America/Toronto")
        except pytz.UnknownTimeZoneError:
            zone = pytz.timezone("America/Toronto")
        return pytz.utc.localize(fields.Datetime.now()).astimezone(zone)

    @api.model
    def _cron_send_agent_digests(self):
        # En sudo, pour la même raison que les résumés clients.
        self = self.sudo()
        pending = self.search([("sent", "=", False)])
        for (user, channel, mode), items in pending.grouped(
                lambda i: (i.user_id, i.channel, i.mode)).items():
            if mode == "daily":
                # Le digest du matin s'en charge. Filet : s'il n'est pas parti
                # (rien d'autre à dire ce jour-là), on envoie nous-mêmes à 26 h.
                oldest = min(items.mapped("create_date"))
                if (channel == "email" and self._daily_handled_by_digest(user)
                        and oldest > fields.Datetime.now() - timedelta(hours=26)):
                    continue
                local = self._user_local_now(user)
                if local.hour < DAILY_HOUR:
                    continue
                # Une date PAR CANAL : un agent quotidien sur deux canaux
                # recevait l'un et l'autre attendait sans fin.
                midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
                midnight_utc = midnight.astimezone(pytz.utc).replace(tzinfo=None)
                if self.search_count([
                    ("user_id", "=", user.id), ("channel", "=", channel),
                    ("mode", "=", "daily"), ("sent", "=", True),
                    ("sent_date", ">=", midnight_utc),
                ]):
                    continue
            items._send_condensed(user, channel)
            items.write({"sent": True, "sent_date": fields.Datetime.now()})
        self.search([
            ("sent", "=", True),
            ("write_date", "<", fields.Datetime.now() - timedelta(days=30)),
        ]).unlink()

    def _send_condensed(self, user, channel):
        count = len(self)
        title = _("%s nouveauté(s) d'assistance", count)
        if channel == "ntfy" and self._ntfy(user, title, count=count):
            return
        rows = Markup("").join(
            Markup('<li><a href="%s">[%s]</a> %s</li>') % (
                item.ticket_id.get_base_url() + "/odoo/helpdesk-tickets/%s" % item.ticket_id.id,
                item.ticket_id.number, item.summary)
            for item in self
        )
        body = Markup("<p>%s</p><ul>%s</ul>") % (title, rows)
        partner = user.partner_id
        partner.sudo().with_context(
            bf_hd_force_notif={partner.id: "email" if channel == "email" else "inbox"},
            mail_notify_author=True,
        ).message_notify(
            partner_ids=partner.ids,
            subject=title,
            body=body,
            # Gabarit maître, sans signature « -- System ».
            email_layout_xmlid="bf_helpdesk.mail_layout_helpdesk",
            email_add_signature=False,
        )

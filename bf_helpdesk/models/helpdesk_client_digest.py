"""Préférences de notification du client : chaque réponse ou un résumé quotidien.

Par défaut rien ne change : chaque réponse de l'équipe part au client. Un
contact peut choisir le résumé quotidien (au portail ou par l'équipe), et
couper les courriels d'un billet (lien signé du courriel, bouton du portail).
Deux envois passent toujours : une demande d'information (le billet attend
le client) et la résolution. Le portail, lui, montre tout, toujours.
"""
from datetime import timedelta

import pytz
from markupsafe import Markup

from odoo import api, fields, models

DIGEST_HOUR = 8  # heure locale du client à partir de laquelle part le résumé


class ResPartner(models.Model):
    _inherit = "res.partner"

    helpdesk_notify_mode = fields.Selection(
        [
            ("each", "À chaque réponse"),
            ("daily", "Résumé quotidien"),
        ],
        string="Mises à jour d'assistance",
        default="each",
        help="Comment ce contact reçoit les réponses de l'équipe d'assistance. "
             "Les demandes d'information et la résolution d'un billet partent "
             "toujours tout de suite.",
    )
    helpdesk_digest_last = fields.Date(
        string="Dernier résumé d'assistance", readonly=True, copy=False,
    )


class HelpdeskClientDigestItem(models.Model):
    _name = "helpdesk.client.digest.item"
    _description = "Réponse d'assistance en attente de résumé"
    _order = "id"

    partner_id = fields.Many2one("res.partner", required=True, index=True,
                                 ondelete="cascade")
    ticket_id = fields.Many2one("helpdesk.ticket", required=True,
                                ondelete="cascade")
    message_id = fields.Many2one("mail.message", required=True,
                                 ondelete="cascade")
    sent = fields.Boolean(default=False, index=True)

    @api.model
    def _partner_local_now(self, partner):
        # Pas le fuseau de la société, qui peut être sur un autre continent.
        tz = partner.tz or "America/Toronto"
        try:
            zone = pytz.timezone(tz)
        except pytz.UnknownTimeZoneError:
            zone = pytz.timezone("America/Toronto")
        return pytz.utc.localize(fields.Datetime.now()).astimezone(zone)

    @api.model
    def _cron_send_client_digests(self):
        """Tâche horaire : un résumé par client, une fois par jour après 8 h (heure locale).

        En sudo : la tâche peut tourner sous un usager qui n'a que la lecture
        de ces éléments (le responsable de l'assistance, pas __system__).
        """
        self = self.sudo()
        pending = self.search([("sent", "=", False)])
        for partner in pending.partner_id:
            local = self._partner_local_now(partner)
            if local.hour < DIGEST_HOUR:
                continue
            if partner.helpdesk_digest_last and partner.helpdesk_digest_last >= local.date():
                continue
            items = pending.filtered(lambda i, p=partner: i.partner_id == p)
            if items._send_digest(partner):
                items.write({"sent": True})
                partner.sudo().helpdesk_digest_last = local.date()
        # Ménage : les éléments envoyés depuis plus de 30 jours.
        self.search([
            ("sent", "=", True),
            ("create_date", "<", fields.Datetime.now() - timedelta(days=30)),
        ]).unlink()

    def _send_digest(self, partner):
        """Un courriel de résumé pour `partner`, avec les réponses de chaque billet."""
        email = partner.email
        if not email:
            return True  # rien à envoyer, mais rien à garder non plus
        en = (partner.lang or "").startswith("en")
        tickets = self.ticket_id
        company = tickets[:1].company_id or self.env.company
        blocks = []
        for ticket in tickets:
            messages = self.filtered(lambda i, t=ticket: i.ticket_id == t).message_id
            rows = []
            for message in messages.sorted("id"):
                rows.append(Markup(
                    '<div style="margin:0 0 10px 0;padding:0 0 0 12px;'
                    'border-left:3px solid #D1D5DB;">'
                    '<p style="margin:0 0 4px 0;font-size:12px;color:#4B5563;">%s</p>%s</div>'
                ) % (message.author_id.name or "", message.body))
            link = ""
            if ticket.partner_has_portal_access:
                link = Markup(' <a href="%s">%s</a>') % (
                    ticket.portal_ticket_url, "View" if en else "Voir la demande")
            blocks.append(Markup(
                '<h2 style="margin:16px 0 8px 0;font-size:16px;">[%s] %s%s</h2>%s'
            ) % (ticket.number, ticket.email_thread_subject or ticket.name, link,
                 Markup("").join(rows)))
        intro = (
            "Here are today's updates on your requests. To reply, simply answer "
            "the email of the request concerned, or open it in the portal."
            if en else
            "Voici les nouvelles de vos demandes. Pour répondre, répondez au "
            "courriel de la demande concernée, ou ouvrez-la au portail."
        )
        inner = Markup('<p style="margin:0 0 12px 0;">%s</p>') % intro + Markup("").join(blocks)
        lang = partner.lang or company.partner_id.lang
        body = self.env["ir.qweb"].with_context(lang=lang)._render(
            "bf_helpdesk.mail_layout_helpdesk", {
                "message": self.env["mail.message"].new({"body": inner}),
                "record": False,
                "company": company,
                "lang": lang,
                "email_add_signature": False,
                "has_button_access": False,
                "is_html_empty": lambda v: not v,
            }, minimal_qcontext=True)
        subject = (
            "Updates on your requests" if en else "Nouvelles de vos demandes"
        ) + " (%s)" % len(tickets)
        self.env["mail.mail"].sudo().create({
            "subject": subject,
            "body_html": body,
            "email_from": company.email_formatted or company.email,
            "recipient_ids": [(4, partner.id)],
            "auto_delete": False,
            "headers": repr({
                "Auto-Submitted": "auto-generated",
                "X-Auto-Response-Suppress": "All",
            }),
        })
        return True

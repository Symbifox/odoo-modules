"""Section « Assistance » du digest du matin.

Deux choses pour le lecteur : ses billets ouverts, les SLA dépassés et à
risque en tête, et les notifications d'assistance qu'il a demandé à recevoir
une fois par jour par courriel (matrice de bf_helpdesk). Elles sont marquées
envoyées seulement quand le digest part vraiment (_send_digest_to), pas au
rendu : un aperçu ne les consomme plus. Un jour où le
digest ne part pas, elles restent en attente, et bf_helpdesk les envoie
lui-même passé 26 h.
"""
from markupsafe import escape as _esc

from odoo import _, api, fields, models

MAX_ROWS = 12
RENDERED_KEY = "bf_helpdesk_digest.rendered_items"
SLA_ORDER = {"breached": 0, "at_risk": 1, "ok": 2, "paused": 3, "none": 4, "met": 5}
SLA_BADGE = {
    "breached": ("#B42318", "SLA dépassé"),
    "at_risk": ("#8A4B00", "SLA à risque"),
    "paused": ("#4B5563", "En pause"),
}


class DailyDigestConfig(models.Model):
    _inherit = "daily.digest.config"

    include_helpdesk = fields.Boolean(
        string="Assistance (billets et notifications)", default=True)

    def _helpdesk_daily_items(self, user):
        return self.env["helpdesk.agent.notify.item"].sudo().search([
            ("user_id", "=", user.id), ("sent", "=", False),
            ("mode", "=", "daily"), ("channel", "=", "email"),
        ])

    def _render_helpdesk(self, user):
        """Section « Assistance » du lecteur, ou ''."""
        self.ensure_one()
        if not self.include_helpdesk:
            return ""
        Ticket = self.env["helpdesk.ticket"].sudo()
        tickets = Ticket.search([
            ("user_id", "=", user.id), ("stage_id.closed", "=", False),
        ])
        items = self._helpdesk_daily_items(user)
        if not tickets and not items:
            return ""
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "").rstrip("/")
        company = user.company_id or self.env.company
        dark = getattr(company, "report_brand_dark", False) or "#2E3132"
        link = company.helpdesk_brand_link_color or "#1F6F95"
        cell = ("padding:8px 10px;border-bottom:1px solid #e5e7eb;"
                "font-family:'Lexend',Arial,sans-serif;font-size:13px;")
        parts = []
        waiting = len(tickets.filtered(lambda t: t.waiting_state == "client"))
        title = _("Assistance : %s billet(s) ouvert(s)", len(tickets))
        if waiting:
            title += " · " + _("%s en attente du client", waiting)
        parts.append(
            f'<h3 style="font-family:\'Lexend\',\'Segoe UI\',Arial,sans-serif;font-size:16px;'
            f'font-weight:600;color:{dark};margin:0 0 8px 0;">🛟 {_esc(title)}</h3>'
        )
        if tickets:
            ordered = tickets.sorted(lambda t: (
                SLA_ORDER.get(t.sla_state, 9), -int(t.priority or 0), t.id))
            rows = ""
            for ticket in ordered[:MAX_ROWS]:
                url = f"{base_url}/odoo/helpdesk-tickets/{ticket.id}"
                badge = ""
                if ticket.sla_state in SLA_BADGE:
                    color, label = SLA_BADGE[ticket.sla_state]
                    badge = (f'<span style="color:{color};font-weight:600;white-space:nowrap;">'
                             f'{_esc(_(label))}</span>')
                client = _esc(ticket.partner_id.name or ticket.partner_name or "")
                rows += (
                    f'<tr><td style="{cell}white-space:nowrap;">'
                    f'<a href="{_esc(url)}" style="color:{link};">{_esc(ticket.number)}</a></td>'
                    f'<td style="{cell}color:#111827;">{_esc(ticket.name)}'
                    f'<br/><span style="color:#4B5563;">{client}</span></td>'
                    f'<td style="{cell}text-align:right;">{badge}</td></tr>'
                )
            parts.append(
                '<table role="presentation" width="100%" style="border:1px solid #e5e7eb;'
                'border-radius:8px;border-collapse:separate;overflow:hidden;margin-bottom:8px;">'
                f'<tbody>{rows}</tbody></table>'
            )
            if len(tickets) > MAX_ROWS:
                parts.append(
                    f'<p style="font-family:Arial,sans-serif;font-size:13px;margin:4px 0 8px 0;">'
                    f'{_esc(_("et %s autre(s)", len(tickets) - MAX_ROWS))}</p>')
        if items:
            lis = "".join(
                f'<li style="margin:0 0 4px 0;"><a href="{_esc(base_url)}/odoo/helpdesk-tickets/'
                f'{item.ticket_id.id}" style="color:{link};">[{_esc(item.ticket_id.number)}]</a> '
                f'{_esc(item.summary)}</li>'
                for item in items
            )
            parts.append(
                f'<p style="font-family:Arial,sans-serif;font-size:14px;font-weight:600;'
                f'color:{dark};margin:12px 0 4px 0;">{_esc(_("Depuis hier"))}</p>'
                f'<ul style="font-family:Arial,sans-serif;font-size:13px;color:#111827;'
                f'margin:0 0 8px 0;padding-left:18px;">{lis}</ul>'
            )
        return "".join(parts)

    def _generate_html(self, data, user):
        html = super()._generate_html(data, user)
        items = self._helpdesk_daily_items(user) if self.include_helpdesk else None
        section = self._render_helpdesk(user)
        if section and "<!-- Divider -->" in html:
            # Même insertion que les autres sections : une ligne à elle, sinon
            # HTML5 la sort de la table de l'enveloppe.
            block = f'<tr><td style="padding:0 24px 24px 24px;">{section}</td></tr>'
            html = html.replace("<!-- Divider -->", block + "<!-- Divider -->", 1)
            if items:
                # Retenus pour ce passage ; marqués par _send_digest_to après l'envoi.
                self.env.cr.precommit.data.setdefault(RENDERED_KEY, set()).update(items.ids)
        return html

    def _send_digest_to(self, user):
        self.env.cr.precommit.data[RENDERED_KEY] = set()
        res = super()._send_digest_to(user)
        rendered = self.env.cr.precommit.data.pop(RENDERED_KEY, set())
        if rendered:
            self.env["helpdesk.agent.notify.item"].sudo().browse(list(rendered)).write({"sent": True})
        return res


class HelpdeskAgentNotifyItem(models.Model):
    _inherit = "helpdesk.agent.notify.item"

    @api.model
    def _daily_handled_by_digest(self, user):
        return bool(self.env["daily.digest.config"].sudo().search_count([
            ("active", "=", True), ("include_helpdesk", "=", True),
            ("user_ids", "in", user.ids),
        ]))

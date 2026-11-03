"""« Factures en retard » section in the daily digest.

Same injection pattern as the other digest bridges: build the section HTML and
splice it in front of the "<!-- Divider -->" marker emitted by the base
template, wrapped in its own ``<tr><td>`` — the marker sits between two rows of
the wrapper table, so bare block content spliced there would be foster parented
out of the table by any HTML5 parser and land above the card.

It renders only for a recipient who opted in on their own user record
(``bf_followup_digest``) AND belongs to the billing group: the opt-in field is
self-writeable, so the group check has to live here too, not only on the view.
"""
from datetime import timedelta

from markupsafe import escape as _esc

from odoo import _, fields, models
from odoo.tools import formatLang

DEFAULT_MIN_DAYS = 0


def _default_min_days(env):
    """Floor defaults to 0 — every overdue invoice, exactly what the standalone
    digest used to send."""
    raw = env["ir.config_parameter"].sudo().get_param(
        "bf_invoice_followup.digest_min_days", DEFAULT_MIN_DAYS)
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return DEFAULT_MIN_DAYS


class DailyDigestConfig(models.Model):
    _inherit = "daily.digest.config"

    include_overdue_invoices = fields.Boolean(
        string="Inclure les factures en retard", default=True,
    )
    overdue_invoice_min_days = fields.Integer(
        string="Retard minimal (jours)",
        default=lambda self: _default_min_days(self.env),
        help="N'afficher que les factures échues depuis au moins ce nombre de "
             "jours. 0 = toutes les factures en retard.",
    )

    # ------------------------------------------------------------------
    # Section
    # ------------------------------------------------------------------
    def _for_recipient(self, user):
        """Rebind on the recipient's own language and timezone, so `_()`, the
        money formatting and the "today" boundary all follow them rather than
        the cron's environment."""
        self.ensure_one()
        return self.with_context(
            lang=user.lang or self.env.lang,
            tz=user.tz or self.env.context.get("tz") or "America/Montreal",
        )

    def _overdue_invoices_for_digest(self, user):
        """Posted, unpaid customer invoices past due for this recipient's company."""
        self.ensure_one()
        company = user.company_id or self.env.company
        today = fields.Date.context_today(self)
        # "Overdue by at least N days" is bf_days_overdue >= N, i.e. due on or
        # before today - N. The floor is clamped to 1 so that N = 0 still means
        # "every overdue invoice" and not "including the ones due today", which
        # are not late at all.
        cutoff = today - timedelta(days=max(self.overdue_invoice_min_days or 0, 1))
        return self.env["account.move"].sudo().search([
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
            ("payment_state", "in", ("not_paid", "partial")),
            ("company_id", "=", company.id),
            ("invoice_date_due", "!=", False),
            ("invoice_date_due", "<=", cutoff),
        ], order="invoice_date_due asc")

    def _render_overdue_invoices(self, user):
        """Build the overdue-receivables HTML, or '' when there is nothing to show."""
        self.ensure_one()
        if not self.include_overdue_invoices:
            return ""
        # Per-user opt-in, set in Preferences. Unchecked = this recipient never
        # received the standalone digest either.
        if not user.bf_followup_digest:
            return ""
        # ⚠️ La case est auto-inscriptible : sans ce groupe, un destinataire du
        # digest quotidien s'ouvrait lui-même le grand livre client.
        if not user.has_group("account.group_account_invoice"):
            return ""
        self = self._for_recipient(user)
        invoices = self._overdue_invoices_for_digest(user)
        if not invoices:
            return ""

        company = user.company_id or self.env.company
        # getattr: the brand fields come from bluefox_branding, which neither
        # this module nor daily_todo_digest declares as a dependency.
        accent = getattr(company, "report_brand_primary", None) or "#29ABE2"
        dark = getattr(company, "report_brand_dark", None) or "#2E3132"
        base_url = (self.env["ir.config_parameter"].sudo()
                    .get_param("web.base.url", "") or "").rstrip("/")
        cell = (
            "padding:8px 10px;border-bottom:1px solid #e5e7eb;"
            "font-family:'Lexend',Arial,sans-serif;font-size:13px;"
        )
        head = (
            "padding:8px 10px;background:%s;color:#fff;"
            "font-family:'Lexend',Arial,sans-serif;font-size:12px;" % accent
        )

        def money(amount, currency):
            return formatLang(self.env, amount, currency_obj=currency)

        # Days overdue are computed here rather than read from
        # account.move.bf_days_overdue: that compute is not stored and does not
        # declare depends_context=('tz',) even though it calls context_today(),
        # so its value is cached in whichever timezone read it first and can
        # contradict the cutoff the search above used. Deriving it from the same
        # `today` keeps the column and the filter telling the same story.
        today = fields.Date.context_today(self)
        rows = ""
        for inv in invoices:
            days = (today - inv.invoice_date_due).days
            if days >= 60:
                days_color = "#dc3545"
            elif days >= 30:
                days_color = "#f59e0b"
            else:
                days_color = "#374151"
            name = _esc(inv.name or "")
            label = _esc(inv._followup_state_label(inv.bf_followup_state))
            rows += (
                f"<tr>"
                f"<td style=\"{cell}color:#111827;\">"
                f"<a href=\"{base_url}/odoo/account.move/{inv.id}\" "
                f"style=\"color:{accent};text-decoration:none;font-weight:600;\">{name}</a></td>"
                f"<td style=\"{cell}color:#111827;\">"
                f"{_esc(inv.partner_id.display_name or '')}</td>"
                f"<td style=\"{cell}color:#6B7280;white-space:nowrap;\">"
                f"{_esc(str(inv.invoice_date_due))}</td>"
                f"<td style=\"{cell}color:{days_color};font-weight:600;"
                f"text-align:right;white-space:nowrap;\">{days} j</td>"
                f"<td style=\"{cell}color:#374151;text-align:right;white-space:nowrap;\">"
                f"{_esc(money(inv.amount_residual or 0.0, inv.currency_id))}</td>"
                f"<td style=\"{cell}color:#6B7280;\">{label}</td>"
                f"</tr>"
            )

        # Total in the company currency: amount_residual_signed is already
        # converted, so a foreign-currency invoice does not get added as if it
        # were CAD.
        total = sum(abs(amount) for amount in invoices.mapped("amount_residual_signed"))
        total_fmt = _esc(money(total, company.currency_id))
        count = len(invoices)
        summary = _(
            "%(count)s facture(s) en retard, solde total %(total)s."
        ) % {"count": count, "total": total_fmt}

        return (
            f"<h3 style=\"font-family:'Lexend','Segoe UI',Arial,sans-serif;"
            f"font-size:16px;font-weight:600;color:{dark};margin:0 0 8px 0;\">"
            f"💸 {_esc(_('Factures en retard'))}"
            f"<span style=\"font-weight:400;font-size:13px;color:#6B7280;\"> — "
            f"{summary}</span></h3>"
            f"<table role=\"presentation\" width=\"100%\" "
            f"style=\"border:1px solid #e5e7eb;border-radius:8px;"
            f"border-collapse:separate;overflow:hidden;\">"
            f"<thead><tr>"
            f"<th style=\"{head}text-align:left;\">{_esc(_('Facture'))}</th>"
            f"<th style=\"{head}text-align:left;\">{_esc(_('Client'))}</th>"
            f"<th style=\"{head}text-align:left;\">{_esc(_('Échéance'))}</th>"
            f"<th style=\"{head}text-align:right;\">{_esc(_('Retard'))}</th>"
            f"<th style=\"{head}text-align:right;\">{_esc(_('Solde dû'))}</th>"
            f"<th style=\"{head}text-align:left;\">{_esc(_('Relance'))}</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>"
            f"<p style=\"font-family:'Lexend',Arial,sans-serif;font-size:12px;"
            f"color:#9CA3AF;margin:8px 0 0 0;\">"
            f"{_esc(_('Récapitulatif interne. Chaque facture se relance depuis le bouton « Envoyer relance » sur sa fiche.'))}"
            f"</p>"
        )

    def _generate_html(self, data, user):
        html = super()._generate_html(data, user)
        section = self._render_overdue_invoices(user)
        if section and "<!-- Divider -->" in html:
            block = (
                f"<tr><td style=\"padding:0 24px 24px 24px;\">{section}</td></tr>"
            )
            html = html.replace("<!-- Divider -->", block + "<!-- Divider -->", 1)
        return html

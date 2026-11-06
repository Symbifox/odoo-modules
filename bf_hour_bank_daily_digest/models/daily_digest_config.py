"""« Banques d'heures » section in the daily digest.

Same injection pattern as bf_subscription_daily_digest and bf_cx_digest:
build the section HTML, splice it in front of the "<!-- Divider -->" marker
emitted by the base wrapper.

Two things are reported, and they answer two different questions.

* **Paliers franchis** — the `hour.bank.threshold.event` rows fired over the
  horizon. An alert email already went to the bank's recipients when it fired;
  this is the internal morning recap of what fired while nobody was looking.
  A balance alert whose threshold has re-armed since (the balance climbed back
  above it) stays listed, marked « résorbé » : the firing happened and the
  email went out, but the row must not read as a problem still open.
* **Banques sans palier** — active banks that no threshold watches, either
  because `threshold_mode` is still `disabled` (its default) or because the
  mode is set but carries no active line, which never fires either. This one
  repeats every morning until it is fixed, on purpose: a bank nobody watches
  will never warn anyone, whatever your own agreements promise.

Quiet days stay quiet: with no event and no gap, the section is not rendered.
"""
import logging

from dateutil.relativedelta import relativedelta
from markupsafe import escape as _esc

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

#: Beyond this many events over the horizon, the table is truncated — and says
#: so. A truncation nobody is told about reads as "that was all of them".
MAX_EVENT_ROWS = 20

#: Same idea for the list of unwatched banks named in the reminder line.
MAX_UNSET_NAMED = 4


def _h(value):
    """Format an hour count the way the rest of the digest reads: 5,2 — not 5.20."""
    return ("%.2f" % (value or 0.0)).rstrip("0").rstrip(".").replace(".", ",")


class DailyDigestConfig(models.Model):
    _inherit = "daily.digest.config"

    include_hour_bank_thresholds = fields.Boolean(
        string="Inclure les paliers de banque d'heures", default=True,
    )
    hour_bank_threshold_days = fields.Integer(
        string="Horizon paliers (jours)", default=7,
        help="Fenêtre de rappel des paliers déjà franchis. Le courriel "
             "d'alerte, lui, part au moment du déclenchement.",
    )
    hour_bank_flag_unset = fields.Boolean(
        string="Signaler les banques sans palier", default=True,
        help="Rappelle chaque matin les banques actives qu'aucun palier actif "
             "ne surveille — le mode est resté « Désactivé », ou aucun palier "
             "n'y est posé. Sans palier, aucun avis ne partira, quoi que vous "
             "ayez promis au client.",
    )

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    @api.model
    def _hour_bank_threshold_label(self, mode, value):
        """Human label for a fired threshold, read from the event's own snapshot.

        Deliberately NOT `event.threshold_line_id.name`: the line is
        `ondelete='set null'` and its value can be edited after the fact, so the
        live label may no longer describe what actually fired. The snapshot is
        the record of the firing. Mirrors `hour.bank.threshold.line._compute_name`.
        """
        if mode == "budget_pct":
            return _("%(v)s %% du budget", v=_h(value))
        if mode == "unbilled":
            return _("%(v)s h non facturées", v=_h(value))
        if mode == "balance_floor":
            if (value or 0.0) < 0:
                # Postpaid account: the balance dives below zero and the
                # threshold reads as an hour debt, not as a remainder.
                return _("Dette de %(v)s h", v=_h(abs(value)))
            return _("Solde sous %(v)s h", v=_h(value))
        return _h(value)

    @api.model
    def _hour_bank_event_resolved(self, event):
        """True when the balance has climbed back since `event` fired.

        Only `balance_floor` can tell: the line and the event are written with
        the same period key when the alert fires, and `bf_hour_bank` clears the
        line's key when it re-arms on the way back up (hysteresis of 0,5 h).
        A line whose key no longer matches the event's has therefore re-armed,
        or fired again later, since this event. Without a line (deleted, the
        field is `ondelete='set null'`) nothing proves the balance came back,
        so the row stays as it was.
        """
        line = event.threshold_line_id
        if event.mode_snapshot != "balance_floor" or not line:
            return False
        return (line.last_fired_period_key or "") != (event.period_key or "")

    def _hour_bank_unset_banks(self, company):
        """Active banks of `company` that no active threshold watches."""
        Bank = self.env["hour.bank.client"].sudo()
        banks = Bank.search([
            ("active", "=", True),
            ("company_id", "=", company.id),
        ])
        # A mode without an active line never fires either — counting it as
        # "watched" would hide exactly the case this reminder exists for.
        return banks.filtered(
            lambda b: b.threshold_mode == "disabled"
            or not b.threshold_line_ids.filtered("active")
        )

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render_hour_bank_thresholds(self, user):
        """Build the hour-bank HTML section, or '' when there is nothing to say."""
        self.ensure_one()
        if not self.include_hour_bank_thresholds:
            return ""

        company = user.company_id or self.env.company
        Event = self.env["hour.bank.threshold.event"].sudo()
        days = self.hour_bank_threshold_days or 7
        since = fields.Datetime.now() - relativedelta(days=days)
        domain = [
            ("bank_id.company_id", "=", company.id),
            ("fired_date", ">=", fields.Datetime.to_string(since)),
        ]
        total = Event.search_count(domain)
        events = Event.search(domain, order="fired_date desc", limit=MAX_EVENT_ROWS)

        unset = self._hour_bank_unset_banks(company) if self.hour_bank_flag_unset \
            else self.env["hour.bank.client"]

        if not events and not unset:
            return ""

        accent = company.report_brand_primary or "#29ABE2"
        dark = company.report_brand_dark or "#2E3132"
        # ⚠️ Le titre est calculé AVANT la f-string, et ce n'est pas du style :
        # une apostrophe échappée dans l'expression d'une f-string est une
        # SyntaxError sur Python 3.10 et 3.11 (PEP 701 ne l'autorise qu'à
        # partir de 3.12). Le module tourne en 3.12 ici, mais il est publié
        # pour des hôtes en 3.10 (Ubuntu 22.04), où il ne s'importait pas.
        titre_banques = _esc(_("Banques d'heures"))
        parts = [
            f'<h3 style="font-family:\'Lexend\',\'Segoe UI\',Arial,sans-serif;font-size:16px;'
            f'font-weight:600;color:{dark};margin:0 0 8px 0;">'
            f'🔔 {titre_banques}</h3>'
        ]

        if events:
            parts.append(self._render_hour_bank_event_table(events, total, accent, days))
        if unset:
            parts.append(self._render_hour_bank_unset_notice(unset))
        return "".join(parts)

    def _render_hour_bank_event_table(self, events, total, accent, days):
        """The table of thresholds crossed over the horizon."""
        today = fields.Date.context_today(self)
        cell = ("padding:8px 10px;border-bottom:1px solid #e5e7eb;"
                "font-family:'Lexend',Arial,sans-serif;")
        head = ("padding:8px 10px;text-align:left;background:%s;color:#fff;"
                "font-family:'Lexend',Arial,sans-serif;font-size:12px;" % accent)

        rows = ""
        for ev in events:
            fired_local = fields.Datetime.context_timestamp(self, ev.fired_date) \
                if ev.fired_date else None
            fired_date = fired_local.date() if fired_local else None
            # Fresh (fired today or yesterday) is what still deserves a look
            # this morning; older rows are context, not a call to action.
            resolved = self._hour_bank_event_resolved(ev)
            fresh = bool(fired_date and (today - fired_date).days <= 1) and not resolved
            date_color = "#dc3545" if fresh else "#6B7280"
            label = self._hour_bank_threshold_label(ev.mode_snapshot, ev.value_snapshot)
            resolved_tag = (
                f' <span style="color:#198754;font-weight:600;">· {_esc(_("résorbé"))}</span>'
                if resolved else ""
            )
            if ev.mode_snapshot == "budget_pct":
                measured = "%s %%" % _h(ev.measured_value)
            else:
                measured = "%s h" % _h(ev.measured_value)
            rows += (
                f'<tr>'
                f'<td style="{cell}font-size:14px;color:#111827;">{_esc(ev.bank_id.display_name or "")}</td>'
                f'<td style="{cell}font-size:13px;color:#6B7280;">{_esc(label)}{resolved_tag}</td>'
                f'<td style="{cell}font-size:13px;color:#111827;font-weight:600;text-align:right;">{_esc(measured)}</td>'
                f'<td style="{cell}font-size:13px;color:{date_color};">{_esc(str(fired_date) if fired_date else "")}</td>'
                f'</tr>'
            )

        title = _("Paliers franchis (%(n)s derniers jours)", n=days)
        table = (
            f'<table role="presentation" width="100%" style="border:1px solid #e5e7eb;'
            f'border-radius:8px;border-collapse:separate;overflow:hidden;margin-bottom:8px;">'
            f'<thead><tr>'
            f'<th style="{head}">{_esc(_("Banque"))}</th>'
            f'<th style="{head}">{_esc(_("Palier"))}</th>'
            f'<th style="{head}text-align:right;">{_esc(_("Mesuré"))}</th>'
            f'<th style="{head}">{_esc(_("Franchi le"))}</th>'
            f'</tr></thead><tbody>{rows}</tbody></table>'
        )
        caption = (
            f'<p style="font-family:\'Lexend\',Arial,sans-serif;font-size:12px;'
            f'color:#6B7280;margin:0 0 8px 0;">{_esc(title)}</p>'
        )
        more = ""
        if total > len(events):
            more = (
                f'<p style="font-family:\'Lexend\',Arial,sans-serif;font-size:12px;'
                f'color:#6B7280;margin:0 0 8px 0;">'
                f'{_esc(_("… et %(n)s autre(s) sur la période.", n=total - len(events)))}</p>'
            )
        return caption + table + more

    def _render_hour_bank_unset_notice(self, unset):
        """The reminder line for banks no active threshold watches."""
        names = ", ".join(unset[:MAX_UNSET_NAMED].mapped("display_name"))
        if len(unset) > MAX_UNSET_NAMED:
            names += _(" (+%(n)s autre(s))", n=len(unset) - MAX_UNSET_NAMED)
        msg = _(
            "%(n)s banque(s) d'heures active(s) qu'aucun palier ne surveille : %(names)s. "
            "Sans palier, aucun avis ne partira tout seul.",
            n=len(unset), names=names,
        )
        return (
            f'<div style="background-color:#fff3cd;border:1px solid #ffe69c;'
            f'border-radius:8px;padding:12px 14px;margin:0 0 8px 0;">'
            f'<p style="font-family:\'Lexend\',\'Segoe UI\',Arial,sans-serif;'
            f'font-size:13px;color:#664d03;margin:0;">⚠️ {_esc(msg)}</p>'
            f'</div>'
        )

    # ------------------------------------------------------------------
    # Injection
    # ------------------------------------------------------------------

    def _generate_html(self, data, user):
        html = super()._generate_html(data, user)
        try:
            section = self._render_hour_bank_thresholds(user)
        except Exception:
            # A broken section must never cost the recipient their digest.
            _logger.exception("Hour bank digest section failed for %s", user.name)
            return html
        if section and "<!-- Divider -->" in html:
            # ⚠️ Le marqueur « <!-- Divider --> » se trouve ENTRE deux <tr>
            # de la table d'enveloppe de _wrap_email. Une section nue insérée
            # là est sortie de la table par tout analyseur HTML5 (foster
            # parenting) et s'affiche au-dessus de la carte. Elle doit donc
            # porter sa propre ligne.
            block = f'<tr><td style="padding:0 24px 24px 24px;">{section}</td></tr>'
            html = html.replace("<!-- Divider -->", block + "<!-- Divider -->", 1)
        return html

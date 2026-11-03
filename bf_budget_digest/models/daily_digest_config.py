"""« Budget » section in the daily digest.

Same injection pattern as bf_cx_digest and bf_employee_experience_digest: build
the section HTML, splice it in front of the "<!-- Divider -->" marker emitted by
the base template.

🔴 **The section follows the responsible, exactly like the alert does.**
bf_budget raises an activity ONCE, when a line crosses the threshold, and tells
the budget's responsible. This digest tells the same person, every morning, as
long as the line stays flagged. That is the division of labour: the activity is
the event, the digest is the standing reminder. Sending it to everyone who can
read budgets would turn a signal into noise, and nobody would own it.

A quiet day stays quiet: no flagged line, no section.
"""

import logging

from markupsafe import escape as _esc

from odoo import fields, models
from odoo.tools.misc import format_amount, formatLang

ACCENT = "#29ABE2"
DARK = "#2E3132"
#: Hors f-string : `\N{...}` y est admis mais se lit mal à côté des accolades
#: de formatage.
MONEY = "\N{MONEY BAG}"

_logger = logging.getLogger(__name__)

#: Beyond this, the mail would be a report. The count below the table says how
#: many were left out, so the figure is never silently truncated.
MAX_ROWS = 10


class DailyDigestConfig(models.Model):
    _inherit = "daily.digest.config"

    include_budget = fields.Boolean(
        string="Include budgets",
        default=True,
        help="Flagged budget lines of the open budgets this recipient is "
             "responsible for.",
    )

    def _budget_flagged_lines(self, user):
        """The flagged lines of the open budgets this person is responsible for.

        ⚠️ `is_alert` is computed and not stored: it cannot be filtered in SQL.
        The socle offers `_search_is_alert`, which walks every line of every
        open budget — too wide here. Reading the lines of THIS person's budgets
        and filtering them in Python stays bounded by what they actually own.
        """
        self.ensure_one()
        # 🔴 The scope is this template's company when it has one, and otherwise
        # every company the person belongs to — NOT `user.company_id` alone,
        # which is the company currently selected on their screen, not the reach
        # of what they answer for. On a multi-company instance, a budget they
        # are responsible for, held by another of their companies, would have
        # gone unsaid.
        companies = self.company_id or user.company_ids
        budgets = self.env["bf.budget"].sudo().search(
            [
                ("state", "=", "open"),
                ("user_id", "=", user.id),
                ("company_id", "in", companies.ids),
            ]
        )
        # 🔴 Être nommé responsable ne donne pas le droit de lire le budget.
        # `user_id` n'a qu'un domaine de champ, qui ne protège ni un import, ni
        # un script, ni un `write` par RPC, et une personne peut perdre le
        # groupe Budgets après coup. Le socle a écrit cette garde exprès
        # (`_alert_recipient` : active, interne, de la société, membre de
        # `group_bf_budget_user`) et refuse alors l'activité. Ce digest part par
        # COURRIEL : tout ce qui entre dans le HTML sort de l'organisation. Sans
        # cette ligne, il envoyait chaque matin les chiffres d'un budget à
        # quelqu'un à qui son ouverture rend une erreur d'accès.
        budgets = budgets.filtered(lambda budget: budget._alert_recipient())
        if not budgets:
            return self.env["bf.budget.line"].sudo()
        return budgets.line_ids.filtered("is_alert")

    def _render_budget_section(self, user):
        """The budget HTML section, or '' when nothing is flagged."""
        self.ensure_one()
        if not self.include_budget:
            return ""
        flagged = self._budget_flagged_lines(user)
        if not flagged:
            return ""

        # 🔴 The socle already renders the digest in the recipient's language
        # (`with_context(lang=…)` before `_generate_html`). We set it again here
        # so that a direct call — a test, another bridge — says the same thing:
        # labels AND amounts come out in the language of whoever reads them. Only
        # the language changes; reading AS the recipient would re-open every
        # access question the search above has already settled.
        env = self.env(context=dict(self.env.context, lang=user.lang or self.env.lang))
        shown = flagged.sorted(lambda line: -abs(line.amount_drift))[:MAX_ROWS]
        rows = "".join(self._render_budget_row(env, line) for line in shown)
        left_out = len(flagged) - len(shown)

        title = env._("Budget")
        subtitle = env._("%(count)s flagged line(s)", count=len(flagged))
        head = "".join(
            f'<th style="padding:8px 10px;text-align:{align};background:{ACCENT};'
            f"color:#fff;font-family:'Lexend',Arial,sans-serif;font-size:12px;\">"
            f"{_esc(label)}</th>"
            for label, align in (
                (env._("Budget item"), "left"),
                (env._("Planned"), "right"),
                (env._("Committed"), "right"),
                (env._("Theoretical"), "right"),
                (env._("Variance from theoretical"), "right"),
            )
        )
        more = ""
        if left_out:
            # Le libellé reste hors de la f-string : des apostrophes imbriquées
            # n'y sont admises qu'à partir de Python 3.12.
            reste = env._("%(count)s more flagged line(s) not shown.", count=left_out)
            more = (
                f'<p style="margin:6px 0 0 0;font-family:Arial,sans-serif;'
                f'font-size:12px;color:#6B7280;">'
                f"{_esc(reste)}"
                f"</p>"
            )
        return (
            f"<h3 style=\"font-family:'Lexend','Segoe UI',Arial,sans-serif;"
            f'font-size:16px;font-weight:600;color:{DARK};margin:0 0 8px 0;">'
            f"{MONEY} {_esc(title)}"
            f'<span style="font-weight:400;font-size:13px;color:#6B7280;"> - '
            f"{_esc(subtitle)}</span></h3>"
            f'<table role="presentation" width="100%" '
            f'style="border:1px solid #e5e7eb;border-radius:8px;'
            f'border-collapse:separate;overflow:hidden;margin-bottom:8px;">'
            f"<thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"
            f"{more}"
        )

    def _render_budget_row(self, env, line):
        """One flagged line: its budget, its figures, and how far off it is."""
        cell = (
            "padding:8px 10px;border-bottom:1px solid #e5e7eb;"
            "font-family:'Lexend',Arial,sans-serif;font-size:13px;"
        )
        currency = line.currency_id
        # Positive drift on an expense means spending faster than planned; on a
        # revenue budget it is the other way round, and the socle's `is_alert`
        # already carries that direction. Here we only have to show the sign the
        # way the record reads it.
        over = line.amount_drift > 0 if line.budget_type == "expense" else line.amount_drift < 0
        colour = "#dc3545" if over else "#16a34a"
        drift = format_amount(env, line.amount_drift, currency)
        pct = formatLang(env, line.drift_pct, digits=1)
        return (
            f"<tr>"
            f'<td style="{cell}color:#111827;">{_esc(line.name)}'
            f'<br/><span style="color:#6B7280;font-size:12px;">'
            f"{_esc(line.budget_id.display_name)}</span></td>"
            f'<td style="{cell}color:#374151;text-align:right;">'
            f"{_esc(format_amount(env, line.amount_planned, currency))}</td>"
            f'<td style="{cell}color:#374151;text-align:right;">'
            f"{_esc(format_amount(env, line.amount_committed, currency))}</td>"
            f'<td style="{cell}color:#374151;text-align:right;">'
            f"{_esc(format_amount(env, line.amount_theoretical, currency))}</td>"
            f'<td style="{cell}color:{colour};text-align:right;font-weight:600;">'
            f"{_esc(drift)}<br/>"
            f'<span style="font-weight:400;font-size:12px;">{_esc(pct)} %</span></td>'
            f"</tr>"
        )

    @staticmethod
    def _splice_budget_section(html, section):
        """Splice the section in front of the marker, inside its own row.

        ⚠️ The "<!-- Divider -->" marker sits BETWEEN two <tr> of the wrapper
        table. A bare section inserted there is pushed out of the table by any
        HTML5 parser (foster parenting) and renders above the card. It must
        carry its own <tr>.

        Kept as a pure helper so it can be tested without rendering the host
        digest, which needs branding fields from another module.
        """
        if not section or "<!-- Divider -->" not in html:
            return html
        block = f'<tr><td style="padding:0 24px 24px 24px;">{section}</td></tr>'
        return html.replace("<!-- Divider -->", block + "<!-- Divider -->", 1)

    def _generate_html(self, data, user):
        html = super()._generate_html(data, user)
        try:
            # 🔴 Le point de reprise est l'essentiel, pas le `try`. Une panne
            # vient presque toujours d'une requête, et elle laisse la
            # transaction Postgres avortée : sans savepoint, le `except` rend la
            # main mais le `mail.mail.create()` qui suit lève à son tour, et le
            # destinataire perd TOUT son briefing du matin, pas seulement cette
            # section. Le point de reprise du cron hôte, lui, ne sauve que les
            # destinataires suivants.
            with self.env.cr.savepoint():
                section = self._render_budget_section(user)
        except Exception:  # noqa: BLE001 - une section cassée n'emporte pas le digest
            _logger.exception(
                "Section Budget du digest : rendu abandonné pour %s", user.login
            )
            return html
        return self._splice_budget_section(html, section)

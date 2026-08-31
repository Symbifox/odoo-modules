"""Section « Avantages » du digest quotidien.

Même patron d'injection que bf_cx_digest : construire le HTML de la section, et
le glisser devant le marqueur « <!-- Divider --> » émis par le gabarit de base.
"""

from markupsafe import escape as _esc

from odoo import _, fields, models

ACCENT = "#29ABE1"
MAX_ROWS = 10


class DailyDigestConfig(models.Model):
    _inherit = "daily.digest.config"

    include_employee_experience = fields.Boolean(
        string="Inclure les avantages", default=True,
    )

    def _render_ex_section(self, user):
        """Le HTML de la section, ou '' quand il n'y a rien à faire."""
        self.ensure_one()
        if not self.include_employee_experience:
            return ""

        company = user.company_id or self.env.company
        Claim = self.env["bf.ex.claim"].sudo()
        Usage = self.env["bf.ex.usage"].sudo()
        Benefit = self.env["bf.ex.benefit"].sudo()

        pending = Claim.search(
            [("state", "=", "submitted"), ("company_id", "=", company.id)],
            order="date_request asc", limit=MAX_ROWS,
        )
        # Un usage sans droit ouvert est soit une erreur de saisie, soit une
        # règle d'admissibilité à revoir. Dans les deux cas quelqu'un doit voir.
        orphans = Usage.search(
            [("entitled", "=", False), ("state", "=", "confirmed"),
             ("company_id", "=", company.id)],
            order="date desc", limit=MAX_ROWS,
        )
        unused = Benefit.search([("company_id", "=", company.id)]).filtered("unused")

        if not (pending or orphans or unused):
            return ""

        blocks = [
            f'<h3 style="margin:0 0 12px 0;font-family:\'Lexend\',Arial,sans-serif;'
            f'font-size:16px;color:{ACCENT};">{_esc(_("Avantages"))}</h3>'
        ]
        if pending:
            blocks.append(self._render_ex_table(
                _("Demandes en attente d'une décision"),
                [(c.date_request, c.employee_id.display_name, c.benefit_id.display_name)
                 for c in pending],
            ))
        if orphans:
            blocks.append(self._render_ex_table(
                _("Usages sans droit ouvert"),
                [(u.date, u.employee_id.display_name, u.benefit_id.display_name)
                 for u in orphans],
            ))
        if unused:
            names = ", ".join(unused[:MAX_ROWS].mapped("display_name"))
            blocks.append(
                f'<p style="margin:6px 0 12px 0;font-family:Arial,sans-serif;'
                f'font-size:13px;color:#92400e;">'
                f'{_esc(_("Personne n\'a pris ces avantages depuis un an :"))} '
                f'{_esc(names)}</p>'
            )
        return "".join(blocks)

    def _render_ex_table(self, title, rows):
        cell = "padding:6px 10px;border-bottom:1px solid #e5e7eb;"
        body = "".join(
            f"<tr>"
            f'<td style="{cell}font-family:Arial,sans-serif;font-size:13px;color:#6b7280;">{_esc(str(a or ""))}</td>'
            f'<td style="{cell}font-family:Arial,sans-serif;font-size:13px;color:#111827;">{_esc(b or "")}</td>'
            f'<td style="{cell}font-family:Arial,sans-serif;font-size:13px;color:#111827;">{_esc(c or "")}</td>'
            f"</tr>"
            for a, b, c in rows
        )
        head = (
            f'<th style="padding:8px 10px;text-align:left;background:{ACCENT};color:#fff;'
            f'font-family:\'Lexend\',Arial,sans-serif;font-size:12px;">{_esc(_("Date"))}</th>'
            f'<th style="padding:8px 10px;text-align:left;background:{ACCENT};color:#fff;'
            f'font-family:\'Lexend\',Arial,sans-serif;font-size:12px;">{_esc(_("Personne"))}</th>'
            f'<th style="padding:8px 10px;text-align:left;background:{ACCENT};color:#fff;'
            f'font-family:\'Lexend\',Arial,sans-serif;font-size:12px;">{_esc(_("Avantage"))}</th>'
        )
        return (
            f'<p style="margin:10px 0 4px 0;font-family:Arial,sans-serif;font-size:13px;'
            f'font-weight:bold;color:#374151;">{_esc(title)}</p>'
            f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">'
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
        )

    @staticmethod
    def _splice_ex_section(html, section):
        """Glisser la section devant le marqueur, dans sa propre ligne.

        ⚠️ Le marqueur « <!-- Divider --> » se trouve ENTRE deux <tr> de la table
        d'enveloppe. Une section nue insérée là est sortie de la table par tout
        analyseur HTML5 (foster parenting) et s'affiche au-dessus de la carte.
        Elle doit donc porter son propre <tr>.

        Isolé en aide pure pour être testable sans dépendre du rendu de l'hôte,
        qui exige des champs de marque venus d'un autre module.
        """
        if not section or "<!-- Divider -->" not in html:
            return html
        block = f'<tr><td style="padding:0 24px 24px 24px;">{section}</td></tr>'
        return html.replace("<!-- Divider -->", block + "<!-- Divider -->", 1)

    def _generate_html(self, data, user):
        html = super()._generate_html(data, user)
        return self._splice_ex_section(html, self._render_ex_section(user))

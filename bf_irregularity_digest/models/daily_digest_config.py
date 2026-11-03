from markupsafe import escape as _esc

from odoo import _, fields, models

SEVERITY_COLORS = {"critical": "#dc3545", "warning": "#d97706", "info": "#6B7280"}
MAX_ROWS = 15


class DailyDigestConfig(models.Model):
    _inherit = "daily.digest.config"

    include_irregularities = fields.Boolean(string="Include irregularities", default=True)

    def _render_irregularities(self, user):
        """Irrégularités ouvertes dont le lecteur est responsable, ou ''."""
        self.ensure_one()
        if not self.include_irregularities:
            return ""
        Irregularity = self.env["bf.irregularity"].sudo()
        domain = [("owner_id", "=", user.id), ("state", "=", "open")]
        total = Irregularity.search_count(domain)
        if not total:
            return ""
        irregularities = Irregularity.search(domain, limit=MAX_ROWS)
        company = user.company_id or self.env.company
        # Couleur de marque portée par bf_onboarding_base, absent de nos dépendances.
        dark = getattr(company, "report_brand_dark", False) or "#2E3132"
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "").rstrip("/")
        cell = "padding:8px 10px;border-bottom:1px solid #e5e7eb;font-family:'Lexend',Arial,sans-serif;font-size:13px;"
        rows = ""
        for irr in irregularities:
            color = SEVERITY_COLORS.get(irr.severity, "#6B7280")
            link = f"{base_url}/odoo/{irr.res_model}/{irr.res_id}" if irr.res_model else ""
            record = _esc(irr.res_name or "")
            if link:
                record = f'<a href="{_esc(link)}" style="color:#111827;">{record}</a>'
            rows += (
                f'<tr>'
                f'<td style="{cell}color:{color};font-weight:600;white-space:nowrap;">{_esc(irr.detector_id.name)}</td>'
                f'<td style="{cell}color:#111827;">{record}<br/><span style="color:#6B7280;">{_esc(irr.name)}</span></td>'
                f'</tr>'
            )
        title = _("Irregularities to look at (%s)", total)
        more = ""
        if total > MAX_ROWS:
            label = _("See all %s", total)
            action = self.env.ref("bf_irregularity.action_bf_irregularity")
            more = (
                f'<p style="font-family:\'Lexend\',Arial,sans-serif;font-size:13px;margin:4px 0 0 0;">'
                f'<a href="{_esc(base_url)}/odoo/action-{action.id}">{_esc(label)}</a></p>'
            )
        return (
            f'<h3 style="font-family:\'Lexend\',\'Segoe UI\',Arial,sans-serif;font-size:16px;'
            f'font-weight:600;color:{dark};margin:0 0 8px 0;">⚠️ {_esc(title)}</h3>'
            f'<table role="presentation" width="100%" style="border:1px solid #e5e7eb;border-radius:8px;'
            f'border-collapse:separate;overflow:hidden;margin-bottom:8px;"><tbody>{rows}</tbody></table>'
            f'{more}'
        )

    def _generate_html(self, data, user):
        html = super()._generate_html(data, user)
        section = self._render_irregularities(user)
        if section and "<!-- Divider -->" in html:
            # Le marqueur est entre deux <tr> de l'enveloppe : la section doit
            # porter sa propre ligne, sinon HTML5 la sort de la table.
            block = f'<tr><td style="padding:0 24px 24px 24px;">{section}</td></tr>'
            html = html.replace("<!-- Divider -->", block + "<!-- Divider -->", 1)
        return html

"""Corps du récapitulatif des abonnements, habillé par la mise en page commune.

18.0.1.8.0 : le module ne bâtit plus sa propre coquille (bandeau, logo de
la société, pied). Le courriel part sous `bf_onboarding_base.bf_mail_layout`, comme
les autres courriels maison : `bluefox_branding` la remplace par la sienne quand il est
installé, sinon la copie de secours de `bf_onboarding_base` s'applique. Ce fichier ne
produit plus que le contenu de la carte.

La mise en page n'a pas de DOCTYPE : en mode quirks, une table n'hérite ni de la taille,
ni de l'interligne, ni de la couleur. Chaque cellule les porte donc elle-même, avec les
valeurs du corps de la mise en page (15 px, 24 px, #374151).
"""
from markupsafe import escape as _esc

from odoo.tools import is_html_empty

MAIL_LAYOUT = "bf_onboarding_base.bf_mail_layout"

TEXT = "#374151"
MUTED = "#6B7280"
FAINT = "#9CA3AF"
RULE = "#E5E7EB"


def _brand_dark(company=None):
    return (company and company.report_brand_dark) or "#212529"


def _font(company=None):
    """La pile de polices de la mise en page (même lecture, même repli)."""
    police = ((company and 'font' in company and company.font) or "Lexend").replace("_", " ")
    return f"'{police}','Segoe UI',Arial,sans-serif"


def _cell(company, size=15, color=TEXT, weight=300, extra=""):
    return (f"font-family:{_font(company)};font-size:{size}px;line-height:{size + 9}px;"
            f"color:{color};font-weight:{weight};{extra}")


def heading(title, company=None):
    return (f'<h1 style="{_cell(company, 20, _brand_dark(company), 600, "margin:0 0 4px 0;")}">'
            f'{_esc(title)}</h1>')


def lead(text, company=None):
    return f'<p style="{_cell(company, 13, MUTED, 300, "margin:0 0 8px 0;")}">{_esc(text)}</p>'


def section_title(title, company=None):
    return (f'<h2 style="{_cell(company, 17, _brand_dark(company), 600, "margin:24px 0 12px 0;")}">'
            f'{_esc(title)}</h2>')


def info_card(rows, company=None):
    # La valeur est en anthracite, pas dans l'accent : le bleu de marque sur blanc
    # ne donne que 2,6:1 (il faut 4,5:1 pour du texte).
    row_html = "".join(
        f'<tr><td style="{_cell(company, 14, MUTED, 300, "padding:4px 0;width:60%;")}">{_esc(label)}</td>'
        f'<td style="{_cell(company, 16, _brand_dark(company), 600, "padding:4px 0;text-align:right;")}">'
        f'{value}</td></tr>'
        for label, value in rows)
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border:1px solid {RULE};border-radius:10px;margin-bottom:8px;"><tbody><tr>'
            f'<td style="padding:14px 16px;"><table role="presentation" width="100%" cellpadding="0" '
            f'cellspacing="0" border="0"><tbody>{row_html}</tbody></table></td></tr></tbody></table>')


def data_table(headers, rows, company=None):
    dark = _brand_dark(company)
    head = "".join(
        f'<th style="{_cell(company, 13, "#FFFFFF", 600, f"padding:10px;text-align:left;background-color:{dark};")}">'
        f'{_esc(h)}</th>'
        for h in headers)
    body = "".join(
        "<tr>" + "".join(
            f'<td style="{_cell(company, 14, TEXT, 300, f"padding:10px;border-bottom:1px solid {RULE};")}">'
            f'{cell}</td>'
            for cell in row) + "</tr>"
        for row in rows)
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border:1px solid {RULE};border-collapse:separate;border-radius:8px;'
            f'margin-bottom:20px;overflow:hidden;"><thead><tr>{head}</tr></thead>'
            f'<tbody>{body}</tbody></table>')


def empty_note(text, company=None):
    return f'<p style="{_cell(company, 14, FAINT, 300, "margin:0 0 16px 0;")}">{_esc(text)}</p>'


def dress(env, body, company, record=None, lang=None):
    """Habille ``body`` (déjà sûr) de la mise en page commune, comme ``send_mail`` le ferait.

    Le récapitulatif part en ``mail.mail`` sans gabarit : on donne ici le contexte que
    ``send_mail`` donne à une mise en page, dans la langue du destinataire. Sans la mise
    en page (socle trop ancien), le contenu part sans habillage plutôt que de ne pas partir.
    """
    qweb = env["ir.qweb"].with_context(lang=lang) if lang else env["ir.qweb"]
    values = {
        "message": env["mail.message"].sudo().new({
            "body": body, "record_name": record.display_name if record else False}),
        "subtype": env["mail.message.subtype"].sudo(),
        "model_description": env["ir.model"]._get(record._name).display_name if record else False,
        "record": record,
        "record_name": False,
        "subtitles": False,
        "company": company,
        "email_add_signature": False,
        "signature": "",
        "website_url": "",
        "is_html_empty": is_html_empty,
    }
    html = qweb._render(MAIL_LAYOUT, values, minimal_qcontext=True, raise_if_not_found=False)
    if not html:
        return body
    return env["mail.render.mixin"]._replace_local_links(html)

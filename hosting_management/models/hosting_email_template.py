# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""
Utilitaires de gabarit courriel de marque Company pour le module hosting_management.
"""

from markupsafe import escape as _esc

from odoo.tools import is_html_empty

# Couleurs d'ÉTAT (panne, lenteur, rétablissement). Elles ne sont pas la marque et
# ne suivent donc pas `report_brand_*`. Chaque état a trois usages, et ils ne
# supportent pas la même couleur :
#   accent  : barre et filet décoratifs, jamais du texte ;
#   texte   : du texte sur blanc ou sur la teinte, ≥ 4,5:1 sur les deux ;
#   entete  : fond d'une ligne d'en-tête, avec la couleur de texte qui s'y lit.
# 🔴 Avant : le jaune d'accent servait aussi au texte. « 5130 ms » en #ffc107 sur
# blanc rendait 1,63:1, et la pastille « LENT » 1,47:1 sur sa teinte : illisibles.
ETATS = {
    "danger": {"accent": "#dc3545", "texte": "#B02A37", "teinte": "#f8d7da",
               "entete": "#dc3545", "entete_texte": "#FFFFFF"},
    "avertissement": {"accent": "#ffc107", "texte": "#8A5A00", "teinte": "#fff3cd",
                      "entete": "#ffc107", "entete_texte": "#212529"},
    "succes": {"accent": "#198754", "texte": "#146C43", "teinte": "#d1e7dd",
               "entete": "#198754", "entete_texte": "#FFFFFF"},
}
NEUTRE = {"texte": "#4B5563", "teinte": "#f3f4f6"}


def _brand_primary(company=None):
    return (company and company.report_brand_primary) or "#714B67"


def _brand_dark(company=None):
    return (company and company.report_brand_dark) or "#212529"


def _brand_font(company=None):
    """La pile de polices de la mise en page de marque (`bf_mail_layout`)."""
    police = ((company and company.font) or "Lexend").replace("_", " ")
    return f"'{police}','Segoe UI',Arial,sans-serif"


def _pied_de_marque(company, brand_primary, brand_dark, police):
    """Le pied de `bluefox_branding.bf_mail_layout`, tel quel.

    Nom de la société, slogan, coordonnées (ou le pied HTML de la société), puis les
    liens de confidentialité et de conditions quand la société les a remplis.
    🔴 Avant : « Gestion d'hébergement » écrit en dur, et deux liens vers « # ».
    """
    if not company:
        return ""
    slogan = company.brand_email_tagline or company.report_header or ""
    lignes = [f'<strong style="color:{brand_dark}; font-size:14px;">{_esc(company.name or "")}</strong>']
    if slogan:
        lignes.append(f"<br/><span>{_esc(slogan)}</span>")
    if not is_html_empty(company.brand_email_footer_html):
        contact = str(company.brand_email_footer_html)
    else:
        morceaux = []
        if company.email:
            morceaux.append(f'<a href="mailto:{_esc(company.email)}" style="color:{brand_primary}; text-decoration:none;">{_esc(company.email)}</a>')
        if company.phone:
            morceaux.append(f'<a href="tel:{_esc(company.phone)}" style="color:{brand_primary}; text-decoration:none;">{_esc(company.phone)}</a>')
        if company.website:
            morceaux.append(f'<a href="{_esc(company.website)}" style="color:{brand_primary}; text-decoration:none;">{_esc(company.website)}</a>')
        contact = '<span style="color:#D1D5DB;"> &#183; </span>'.join(morceaux)
    liens = []
    if company.brand_privacy_url:
        liens.append(f'<a href="{_esc(company.brand_privacy_url)}" style="color:#9CA3AF; text-decoration:underline;">Confidentialité</a>')
    if company.brand_terms_url:
        liens.append(f'<a href="{_esc(company.brand_terms_url)}" style="color:#9CA3AF; text-decoration:underline;">Conditions</a>')
    bloc_liens = (
        f'<tr><td style="padding-top:12px; font-family:{police}; font-size:11px;">'
        + '<span style="color:#D1D5DB;"> | </span>'.join(liens) + "</td></tr>") if liens else ""
    bloc_contact = (
        f'<tr><td style="padding-top:12px; font-family:{police}; font-size:12px; color:#9CA3AF; line-height:18px;">{contact}</td></tr>'
        if contact else "")
    return f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
    <tbody>
        <tr><td style="font-family:{police}; font-size:13px; color:#6B7280; line-height:20px;">{"".join(lignes)}</td></tr>
        {bloc_contact}
        {bloc_liens}
    </tbody>
</table>'''


def get_email_wrapper(title, content, alert_type=None, company=None):
    """
    Générer un gabarit courriel branded.

    Args:
        title: Le titre de l'en-tête du courriel
        content: Le contenu HTML principal
        alert_type: Type d'alerte optionnel ("down", "recovered", "slow", "info")
        company: res.company optionnel — sa couleur de marque sert d'accent
                 par défaut quand alert_type est None ou "info".

    Returns:
        Chaîne HTML complète du courriel
    """
    brand_primary = _brand_primary(company)
    brand_dark = _brand_dark(company)
    police = _brand_font(company)
    # La barre sous le bandeau dit l'état ; sans état, c'est l'accent de marque.
    etat = {"down": "danger", "recovered": "succes", "slow": "avertissement"}.get(alert_type)
    accent_color = ETATS[etat]["accent"] if etat else brand_primary
    # 🔴 Avant : `/web/image/res.company/1/logo`, la société 1 quelle que soit celle
    # qui écrit, et un lien vers « # ». La route de marque suit la société.
    if company:
        logo = (f'<img src="/brand/logo/{company.id}" alt="{_esc(company.name or "")}" '
                'style="height:44px; width:auto; display:block; border:0;" height="44"/>')
        if company.website:
            logo = f'<a href="{_esc(company.website)}" style="text-decoration:none;">{logo}</a>'
    else:
        logo = ""
    pied = _pied_de_marque(company, brand_primary, brand_dark, police)

    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0; padding:0; background-color:#F8FAFC; font-family:{police};">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color:#F8FAFC;">
    <tbody>
        <tr>
            <td align="center" style="padding:24px;">
                <table cellspacing="0" cellpadding="0" border="0" width="100%" align="center" role="presentation">
                    <tbody>
                        <tr>
                            <td>
                                <br/>
                                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="width:600px; max-width:600px; margin:0 auto; background-color:#ffffff; border-radius:12px; border:1px solid #e5e7eb; border-collapse:collapse;">
                                    <tbody>
                                        <!-- En-tête -->
                                        <tr>
                                            <td style="background-color:{brand_dark}; padding:16px 24px; border-radius:12px 12px 0 0;">
                                                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                                                    <tbody>
                                                        <tr>
                                                            <td align="left" style="color:#FFFFFF; font-family:{police}; font-size:16px; font-weight:600;">
                                                                {logo}
                                                            </td>
                                                            <td align="right" style="color:#E6EDF3; font-family:{police}; font-size:22px; font-weight:800; letter-spacing:0.2px;">
                                                                {_esc(title)}
                                                            </td>
                                                        </tr>
                                                    </tbody>
                                                </table>
                                            </td>
                                        </tr>

                                        <!-- Barre d'accentuation -->
                                        <tr>
                                            <td style="height:4px; line-height:4px; background-color:{accent_color};">&nbsp;</td>
                                        </tr>

                                        <!-- Contenu -->
                                        <tr>
                                            <td style="padding:24px; font-family:{police};">
                                                {content}
                                            </td>
                                        </tr>

                                        <!-- Séparateur -->
                                        <tr>
                                            <td style="height:1px; line-height:1px; background-color:#E5E7EB;">&nbsp;</td>
                                        </tr>

                                        <!-- Pied de page -->
                                        <tr>
                                            <td style="padding:16px 24px 24px 24px; background-color:#FFFFFF; border-radius:0 0 12px 12px;">
                                                {pied}
                                            </td>
                                        </tr>
                                    </tbody>
                                </table>

                                <!-- Barre d'accentuation inférieure -->
                                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="width:600px; max-width:600px; margin:12px auto 0;">
                                    <tbody>
                                        <tr>
                                            <td style="height:3px; line-height:3px; background-color:{brand_primary}; width:50%;">&nbsp;</td>
                                            <td style="height:3px; line-height:3px; background-color:{brand_dark}; width:50%;">&nbsp;</td>
                                        </tr>
                                    </tbody>
                                </table>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </td>
        </tr>
    </tbody>
</table>
</body>
</html>'''


def get_info_card(rows, badge_text=None, badge_color=None, company=None):
    """
    Générer une carte d'information stylisée pour les données clé-valeur.

    Args:
        rows: Liste de tuples (étiquette, valeur) pour le contenu de la carte
        badge_text: Texte de badge optionnel à afficher
        badge_color: Couleur du badge (défaut: brand_primary de la company)
        company: res.company optionnel pour les couleurs de marque

    Returns:
        Chaîne HTML de la carte d'information
    """
    if badge_color is None:
        badge_color = _brand_primary(company)
    brand_dark = _brand_dark(company)
    police = _brand_font(company)
    row_html = ""
    for i, (label, value) in enumerate(rows):
        padding_top = "8px" if i > 0 else "0"
        # label/value may contain pre-built HTML from callers — escape at call site
        row_html += f'''
        <tr>
            <td style="font-family:{police}; font-size:14px; color:#6B7280; width:140px; padding-top:{padding_top};">
                {label}
            </td>
            <td style="font-family:{police}; font-size:14px; color:#111827; padding-top:{padding_top};">
                {value}
            </td>
        </tr>'''

    badge_html = ""
    if badge_text:
        badge_html = f'''
        <tr>
            <td style="padding:0 16px 16px 16px;" colspan="2">
                <span style="display:inline-block; font-family:{police}; font-size:12px; text-transform:uppercase; letter-spacing:0.6px; padding:6px 10px; background-color:#F3F4F6; color:{brand_dark}; border:1px solid #E5E7EB; border-radius:999px;">
                    {_esc(badge_text)}
                </span>
            </td>
        </tr>'''

    return f'''
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border:1px solid #e5e7eb; border-radius:10px; margin-bottom:16px;">
        <tbody>
            <tr>
                <td style="padding:16px 16px 8px 16px;">
                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                        <tbody>
                            {row_html}
                        </tbody>
                    </table>
                </td>
            </tr>
            {badge_html}
        </tbody>
    </table>'''


def get_data_table(headers, rows, header_bg=None, header_color="#FFFFFF", company=None):
    """
    Générer un tableau de données stylisé.

    Args:
        headers: Liste des noms de colonnes d'en-tête
        rows: Liste de listes pour les lignes du tableau
        header_bg: Couleur de fond de la ligne d'en-tête (défaut: brand_dark de la company)
        header_color: Couleur du texte de la ligne d'en-tête
        company: res.company optionnel pour les couleurs de marque

    Returns:
        Chaîne HTML du tableau
    """
    if header_bg is None:
        header_bg = _brand_dark(company)
    police = _brand_font(company)
    header_html = "".join(
        f'<th style="padding:12px; text-align:left; border-bottom:2px solid #e5e7eb; font-family:{police}; font-size:13px; font-weight:600; color:{header_color}; background-color:{header_bg};">{_esc(h)}</th>'
        for h in headers
    )

    rows_html = ""
    for row in rows:
        # cells may contain pre-built HTML (links, badges) — escape at call site
        cells = "".join(
            f'<td style="padding:12px; border-bottom:1px solid #e5e7eb; font-family:{police}; font-size:14px; color:#374151;">{cell}</td>'
            for cell in row
        )
        rows_html += f"<tr>{cells}</tr>"

    return f'''
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border:1px solid #e5e7eb; border-radius:8px; border-collapse:separate; margin-bottom:20px; overflow:hidden;">
        <thead>
            <tr>{header_html}</tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>'''


def get_section_title(title, color=None, company=None):
    """Générer un titre de section."""
    if color is None:
        color = _brand_dark(company)
    police = _brand_font(company)
    return f'''
    <h2 style="font-family:{police}; font-size:18px; font-weight:600; color:{color}; margin:24px 0 12px 0; padding:0;">
        {_esc(title)}
    </h2>'''


def get_status_badge(status, size="normal", company=None):
    """
    Générer un badge de statut.

    Args:
        status: Texte du statut ("up", "down", "degraded", "timeout", etc.)
        size: "small" ou "normal"

    Returns:
        Chaîne HTML du badge
    """
    status_lower = status.lower() if status else "unknown"
    police = _brand_font(company)

    etat = {"up": "succes", "down": "danger", "timeout": "danger",
            "degraded": "avertissement", "slow": "avertissement"}.get(status_lower)
    couleurs = ETATS[etat] if etat else NEUTRE
    # La couleur de TEXTE de l'état, pas son accent : le jaune d'accent rendait
    # 1,47:1 sur sa teinte.
    text_color, bg_color = couleurs["texte"], couleurs["teinte"]
    font_size = "11px" if size == "small" else "12px"
    padding = "4px 8px" if size == "small" else "6px 10px"

    return f'''<span style="display:inline-block; font-family:{police}; font-size:{font_size}; text-transform:uppercase; letter-spacing:0.5px; padding:{padding}; background-color:{bg_color}; color:{text_color}; border-radius:999px; font-weight:600;">{_esc(status.upper())}</span>'''


def get_button(text, url, primary=True, company=None):
    """
    Générer un bouton stylisé.

    Args:
        text: Texte du bouton
        url: URL du bouton
        primary: True pour bouton principal (accent de marque), False pour secondaire (foncé)
        company: res.company dont la marque colore le bouton

    Returns:
        Chaîne HTML du bouton
    """
    # L'accent BRUT sous du texte blanc, comme le bouton de `bf_mail_layout` : c'est
    # l'arbitrage du propriétaire de la marque (voir `branding.scss`), pas un oubli.
    bg_color = _brand_primary(company) if primary else _brand_dark(company)
    police = _brand_font(company)

    return f'''<a href="{_esc(url)}" style="display:inline-block; font-family:{police}; font-size:14px; font-weight:600; padding:12px 18px; background-color:{bg_color}; color:#ffffff; text-decoration:none; border-radius:8px; text-align:center;">{_esc(text)}</a>'''


def get_contact_footer(company=None):
    """Générer la section de pied de page de contact."""
    police = _brand_font(company)
    return f'''
    <p style="font-family:{police}; font-size:13px; line-height:20px; color:#6B7280; margin:16px 0 0 0;">
        Pour toute assistance, contactez votre fournisseur d'hébergement.
    </p>'''

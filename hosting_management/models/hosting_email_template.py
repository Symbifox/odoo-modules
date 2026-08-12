# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""
Utilitaires de gabarit courriel de marque Company pour le module hosting_management.
"""

from markupsafe import escape as _esc


def _brand_primary(company=None):
    return (company and company.report_brand_primary) or "#714B67"


def _brand_dark(company=None):
    return (company and company.report_brand_dark) or "#212529"


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
    # Schéma de couleurs basé sur le type d'alerte
    if alert_type == "down":
        accent_color = "#dc3545"  # Rouge
        badge_bg = "#f8d7da"
        badge_text = "Alerte"
    elif alert_type == "recovered":
        accent_color = "#198754"  # Vert
        badge_bg = "#d1e7dd"
        badge_text = "Rétabli"
    elif alert_type == "slow":
        accent_color = "#ffc107"  # Orange/Jaune
        badge_bg = "#fff3cd"
        badge_text = "Avertissement"
    else:
        accent_color = brand_primary
        badge_bg = "#E8F6FD"
        badge_text = "Info"

    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0; padding:0; background-color:#F8FAFC;">
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
                                                            <td align="left" style="color:#FFFFFF; font-family:'Lexend','Segoe UI',Arial,sans-serif; font-size:16px; font-weight:600;">
                                                                <a href="#" style="text-decoration:none;">
                                                                    <img src="/web/image/res.company/1/logo" alt="Company" style="height:48px; width:auto; display:block; border:0;"/>
                                                                </a>
                                                            </td>
                                                            <td align="right" style="color:#E6EDF3; font-family:'Lexend','Segoe UI',Arial,sans-serif; font-size:22px; font-weight:800; letter-spacing:0.2px;">
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
                                            <td style="padding:24px; font-family:'Lexend','Segoe UI',Arial,sans-serif;">
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
                                                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                                                    <tbody>
                                                        <tr>
                                                            <td style="font-family:'Lexend','Segoe UI',Arial,sans-serif; font-size:12px; color:#6B7280;">
                                                                <strong style="color:{brand_dark};">Gestion d'hébergement</strong><br/>
                                                                Solutions éthiques et souveraines pour vos données.
                                                            </td>
                                                            <td align="right" style="font-family:'Lexend','Segoe UI',Arial,sans-serif; font-size:12px; color:#9CA3AF;">
                                                                <a href="#" style="color:#9CA3AF; text-decoration:underline;">Politique de confidentialité</a>
                                                                <span style="color:#9CA3AF;"> | </span>
                                                                <a href="#" style="color:#9CA3AF; text-decoration:underline;">Conditions</a>
                                                            </td>
                                                        </tr>
                                                    </tbody>
                                                </table>
                                            </td>
                                        </tr>
                                    </tbody>
                                </table>

                                <!-- Barre d'accentuation inférieure -->
                                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="width:600px; max-width:600px; margin:12px auto 0;">
                                    <tbody>
                                        <tr>
                                            <td style="height:3px; line-height:3px; background-color:#29abe2; width:50%;">&nbsp;</td>
                                            <td style="height:3px; line-height:3px; background-color:#22303b; width:50%;">&nbsp;</td>
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
    row_html = ""
    for i, (label, value) in enumerate(rows):
        padding_top = "8px" if i > 0 else "0"
        # label/value may contain pre-built HTML from callers — escape at call site
        row_html += f'''
        <tr>
            <td style="font-family:'Lexend','Segoe UI',Arial,sans-serif; font-size:14px; color:#6B7280; width:140px; padding-top:{padding_top};">
                {label}
            </td>
            <td style="font-family:'Lexend','Segoe UI',Arial,sans-serif; font-size:14px; color:#111827; padding-top:{padding_top};">
                {value}
            </td>
        </tr>'''

    badge_html = ""
    if badge_text:
        badge_html = f'''
        <tr>
            <td style="padding:0 16px 16px 16px;" colspan="2">
                <span style="display:inline-block; font-family:'Lexend','Segoe UI',Arial,sans-serif; font-size:12px; text-transform:uppercase; letter-spacing:0.6px; padding:6px 10px; background-color:#E8F6FD; color:{brand_dark}; border:1px solid #BEE3F8; border-radius:999px;">
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
    header_html = "".join(
        f'<th style="padding:12px; text-align:left; border-bottom:2px solid #e5e7eb; font-family:\'Lexend\',\'Segoe UI\',Arial,sans-serif; font-size:13px; font-weight:600; color:{header_color}; background-color:{header_bg};">{_esc(h)}</th>'
        for h in headers
    )

    rows_html = ""
    for row in rows:
        # cells may contain pre-built HTML (links, badges) — escape at call site
        cells = "".join(
            f'<td style="padding:12px; border-bottom:1px solid #e5e7eb; font-family:\'Lexend\',\'Segoe UI\',Arial,sans-serif; font-size:14px; color:#374151;">{cell}</td>'
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
    return f'''
    <h2 style="font-family:'Lexend','Segoe UI',Arial,sans-serif; font-size:18px; font-weight:600; color:{color}; margin:24px 0 12px 0; padding:0;">
        {_esc(title)}
    </h2>'''


def get_status_badge(status, size="normal"):
    """
    Générer un badge de statut.

    Args:
        status: Texte du statut ("up", "down", "degraded", "timeout", etc.)
        size: "small" ou "normal"

    Returns:
        Chaîne HTML du badge
    """
    status_lower = status.lower() if status else "unknown"

    colors = {
        "up": ("#198754", "#d1e7dd"),
        "down": ("#dc3545", "#f8d7da"),
        "degraded": ("#ffc107", "#fff3cd"),
        "timeout": ("#dc3545", "#f8d7da"),
        "slow": ("#ffc107", "#fff3cd"),
    }

    text_color, bg_color = colors.get(status_lower, ("#6B7280", "#f3f4f6"))
    font_size = "11px" if size == "small" else "12px"
    padding = "4px 8px" if size == "small" else "6px 10px"

    return f'''<span style="display:inline-block; font-family:'Lexend','Segoe UI',Arial,sans-serif; font-size:{font_size}; text-transform:uppercase; letter-spacing:0.5px; padding:{padding}; background-color:{bg_color}; color:{text_color}; border-radius:999px; font-weight:600;">{_esc(status.upper())}</span>'''


def get_button(text, url, primary=True):
    """
    Générer un bouton stylisé.

    Args:
        text: Texte du bouton
        url: URL du bouton
        primary: True pour bouton principal (cyan), False pour secondaire (foncé)

    Returns:
        Chaîne HTML du bouton
    """
    if primary:
        bg_color = "#29abe2"
    else:
        bg_color = "#22303b"

    return f'''<a href="{_esc(url)}" style="display:inline-block; font-family:'Lexend','Segoe UI',Arial,sans-serif; font-size:14px; font-weight:600; padding:12px 18px; background-color:{bg_color}; color:#ffffff; text-decoration:none; border-radius:8px; text-align:center;">{_esc(text)}</a>'''


def get_contact_footer():
    """Générer la section de pied de page de contact."""
    return '''
    <p style="font-family:'Lexend','Segoe UI',Arial,sans-serif; font-size:13px; line-height:20px; color:#6B7280; margin:16px 0 0 0;">
        Pour toute assistance, contactez votre fournisseur d'hébergement.
    </p>'''

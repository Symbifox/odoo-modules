"""Self-contained branded email template helpers for subscription digests.

Kept independent of hosting_management so the module has no cross-dependency.
"""
from markupsafe import escape as _esc

ACCENT = "#29ABE1"   # Blue Fox cyan
DARK = "#2D3031"     # anthracite


def _brand_accent(company=None):
    return (company and getattr(company, 'report_brand_primary', None)) or ACCENT


def _brand_dark(company=None):
    return (company and getattr(company, 'report_brand_dark', None)) or DARK


def wrapper(title, content, company=None):
    dark = _brand_dark(company)
    logo = '<img src="/web/image/res.company/%d/logo" alt="" style="height:42px;width:auto;display:block;border:0;"/>' % company.id if company else ''
    return f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#F8FAFC;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color:#F8FAFC;"><tbody><tr>
<td align="center" style="padding:24px;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="width:600px;max-width:600px;margin:0 auto;background:#fff;border-radius:12px;border:1px solid #e5e7eb;border-collapse:collapse;"><tbody>
    <tr><td style="background-color:{dark};padding:16px 24px;border-radius:12px 12px 0 0;">
      <table role="presentation" width="100%"><tbody><tr>
        <td align="left">{logo}</td>
        <td align="right" style="color:#fff;font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:16px;font-weight:600;">{_esc(title)}</td>
      </tr></tbody></table>
    </td></tr>
    <tr><td style="padding:24px;">{content}</td></tr>
    <tr><td style="padding:16px 24px;border-top:1px solid #e5e7eb;color:#9CA3AF;font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;">
      Récapitulatif automatique des abonnements.
    </td></tr>
  </tbody></table>
</td></tr></tbody></table>
</body></html>'''


def section_title(title, company=None):
    return (f'<h2 style="font-family:\'Lexend\',\'Segoe UI\',Arial,sans-serif;font-size:18px;'
            f'font-weight:600;color:{_brand_dark(company)};margin:24px 0 12px 0;">{_esc(title)}</h2>')


def info_card(rows, company=None):
    accent = _brand_accent(company)
    row_html = ""
    for label, value in rows:
        row_html += (f'<tr><td style="font-family:\'Lexend\',Arial,sans-serif;font-size:14px;'
                     f'color:#6B7280;padding:4px 0;width:60%;">{_esc(label)}</td>'
                     f'<td style="font-family:\'Lexend\',Arial,sans-serif;font-size:16px;'
                     f'font-weight:600;color:{accent};padding:4px 0;text-align:right;">{value}</td></tr>')
    return (f'<table role="presentation" width="100%" style="border:1px solid #e5e7eb;'
            f'border-radius:10px;margin-bottom:8px;"><tbody><tr><td style="padding:14px 16px;">'
            f'<table width="100%"><tbody>{row_html}</tbody></table></td></tr></tbody></table>')


def data_table(headers, rows, company=None):
    dark = _brand_dark(company)
    head = "".join(
        f'<th style="padding:10px;text-align:left;font-family:\'Lexend\',Arial,sans-serif;'
        f'font-size:13px;font-weight:600;color:#fff;background:{dark};">{_esc(h)}</th>'
        for h in headers)
    body = ""
    for row in rows:
        cells = "".join(
            f'<td style="padding:10px;border-bottom:1px solid #e5e7eb;'
            f'font-family:\'Lexend\',Arial,sans-serif;font-size:14px;color:#374151;">{cell}</td>'
            for cell in row)
        body += f"<tr>{cells}</tr>"
    return (f'<table role="presentation" width="100%" style="border:1px solid #e5e7eb;'
            f'border-radius:8px;border-collapse:separate;margin-bottom:20px;overflow:hidden;">'
            f'<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>')


def empty_note(text):
    return (f'<p style="font-family:\'Lexend\',Arial,sans-serif;font-size:14px;'
            f'color:#9CA3AF;margin:0 0 16px 0;">{_esc(text)}</p>')

"""La mise en page commune des courriels, pour ceux qui ne passent pas par ``send_mail``."""
from odoo.tools import is_html_empty

# Mise en page commune des courriels maison : bf_onboarding_base la livre,
# bluefox_branding la remplace par la sienne quand il est installé.
MAIL_LAYOUT = "bf_onboarding_base.bf_mail_layout"


def dress_mail_body(env, body, company, record=None, lang=None, layout=MAIL_LAYOUT):
    """Habille ``body`` (déjà rendu et sûr) de la mise en page commune des courriels.

    L'invitation, la relance et le code de vérification partent en ``mail.mail``
    nu, pour que le jeton de signature ne reste jamais dans un message : ils ne
    passent donc pas par ``send_mail``, qui habille d'ordinaire. On donne ici le
    contexte que ``send_mail`` donne à une mise en page, dans la langue où le
    gabarit a été rendu. ``body`` doit être déjà sûr : ``mail.message.new`` ne le
    nettoie pas. Sans la mise en page (socle trop ancien), le contenu part sans
    habillage plutôt que de ne pas partir.
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
    html = qweb._render(layout, values, minimal_qcontext=True, raise_if_not_found=False)
    if not html:
        return body
    return env["mail.render.mixin"]._replace_local_links(html)

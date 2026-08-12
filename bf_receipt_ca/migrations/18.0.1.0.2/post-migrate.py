# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import SUPERUSER_ID, api

FR_SUBJECT = "{{ object.company_id.name }} — Reçu officiel {{ object.number or '' }}"

FR_BODY = """
<div style="margin:0; padding:0; font-size:13px;">
    <p>
        Bonjour <t t-out="object.partner_id.name or ''">Donateur</t>,
        <br/><br/>
        Merci sincèrement de votre don.
        <br/><br/>
        Vous trouverez ci-joint votre <strong>reçu officiel aux fins de l'impôt
        sur le revenu</strong> n<sup>o</sup>
        <strong><t t-out="object.number or ''">REÇU-AAAA-NNNNN</t></strong>,
        d'un montant admissible de
        <strong><t t-out="format_amount(object.eligible_amount, object.currency_id) or ''">0,00 $</t></strong>,
        émis par <t t-out="object.company_id.name or ''">l'organisme</t>.
        <t t-if="not is_html_empty(user.signature)">
            <br/><br/>
            <t t-out="user.signature or ''">--</t>
        </t>
    </p>
</div>
"""


def migrate(cr, version):
    """Localize the receipt email to French on existing installs. The template
    is owned by ``donation_base`` with ``noupdate="1"``, so ``-u`` cannot update
    it via data; fresh installs get it from data/mail_template.xml."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    tmpl = env.ref(
        "donation_base.tax_receipt_email_template", raise_if_not_found=False
    )
    if tmpl:
        tmpl.write({"subject": FR_SUBJECT, "body_html": FR_BODY})

"""Les courriels de visite passent par la mise en page commune.

Les six gabarits appellent `visit_email_layout`, qui portait la coquille : fond,
carte de 600 px, en-tête foncé au logo Rendez-vous et au titre, filet, pied au nom
de marque et au courriel de soutien. La vue ne garde que le surtitre, le contenu et
le bouton ; les gabarits déclarent `bf_onboarding_base.bf_mail_layout`, que
bluefox_branding remplace par la sienne.

Tout est `noupdate` : la mise à jour ne réécrit ni la vue ni les gabarits. On réécrit
la vue dans chaque langue stockée, avec son titre (tout ou rien : une langue sans
l'ancienne coquille, refaite à la main, laisse la vue et les gabarits tels quels).
Dans les corps, les raccourcis de style que le corps d'un message n'admet pas
(`background:`, `border-left:`) prennent leur forme longue.
"""
import json
import logging
import re

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

MODULE = "bf_appointment_visit"
VERSION = "18.0.1.2.0"
GABARITS = (
    "mail_visit_pending",
    "mail_visit_approved",
    "mail_visit_declined",
    "mail_visit_seller_request",
    "mail_visit_tenant_notice",
    "mail_visit_feedback_request",
)
MISE_EN_PAGE = "bf_onboarding_base.bf_mail_layout"
TITRE = re.compile(r'<td align="right"[^>]*font-size:20px[^>]*>\s*(.*?)\s*</td>', re.S)
TRACES = ('width="600"', "border-radius:12px 12px 0 0")
VUE = """<t t-name="bf_appointment_visit.visit_email_layout">
            <t t-set="company" t-value="(object.listing_id.company_id) or user.company_id"/>
            <t t-set="brand_primary" t-value="(company and company.report_brand_primary) or '#714B67'"/>
            <p style="margin:0 0 4px 0; font-size:12px; font-weight:600; letter-spacing:0.6px; text-transform:uppercase; color:#6B7280;">{titre}</p>
            <t t-out="contenu"/>
            <table t-if="bouton_url" role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:22px 0 6px 0;">
                <tbody>
                    <tr>
                        <td t-attf-style="background-color:{{{{ brand_primary }}}}; border-radius:8px;">
                            <a t-attf-href="{{{{ bouton_url }}}}" style="display:inline-block; padding:12px 22px; color:#ffffff; font-weight:600; font-size:15px; text-decoration:none;">
                                <t t-out="bouton_texte"/>
                            </a>
                        </td>
                    </tr>
                </tbody>
            </table>
        </t>"""
COULEUR = r"(#[0-9A-Fa-f]{3,8}|\{\{[^}]*\}\}|#\{[^}]*\})"
FOND = re.compile(r"(?<![\w-])background:\s*" + COULEUR + r"\s*(?=;|\"|')")
BORDURE = re.compile(
    r"(?<![\w-])border-(left|right):\s*(\d+px)\s+solid\s+" + COULEUR + r"\s*(?=;|\"|')")


def styles_admis(corps):
    corps = FOND.sub(r"background-color:\1", corps or "")
    return BORDURE.sub(
        r"border-\1-width:\2; border-\1-style:solid; border-\1-color:\3", corps)


def vue_sans_coquille(arch):
    """(nouvelle vue, True), ou (None, False) sans l'ancienne coquille."""
    if not all(trace in (arch or "") for trace in TRACES):
        return None, False
    titre = TITRE.search(arch)
    if not titre or "<" in titre.group(1):
        return None, False
    return VUE.format(titre=titre.group(1).strip()), True


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.flush_all()
    vue = env.ref(f"{MODULE}.visit_email_layout", raise_if_not_found=False)
    if not vue:
        return
    cr.execute("SELECT arch_db FROM ir_ui_view WHERE id = %s", [vue.id])
    valeurs = cr.fetchone()[0] or {}
    nouvelles = {}
    for lang, arch in valeurs.items():
        if "text-transform:uppercase; color:#6B7280;" in (arch or "") and "width=\"600\"" not in arch:
            continue  # déjà réécrite
        nouvelle, ok = vue_sans_coquille(arch)
        if not ok:
            _logger.warning("%s %s : visit_email_layout (%s) sans l'ancienne coquille, "
                            "vue et gabarits laissés tels quels", MODULE, VERSION, lang)
            return
        nouvelles[lang] = nouvelle
    if nouvelles:
        cr.execute("UPDATE ir_ui_view SET arch_db = %s::jsonb WHERE id = %s",
                   [json.dumps(dict(valeurs, **nouvelles)), vue.id])
        vue.invalidate_recordset(["arch_db"])
    for xmlid in GABARITS:
        template = env.ref(f"{MODULE}.{xmlid}", raise_if_not_found=False)
        if not template:
            continue
        cr.execute("SELECT body_html FROM mail_template WHERE id = %s", [template.id])
        corps = cr.fetchone()[0] or {}
        cr.execute(
            "UPDATE mail_template SET body_html = %s::jsonb, email_layout_xmlid = %s WHERE id = %s",
            [json.dumps({lang: styles_admis(c) for lang, c in corps.items()}), MISE_EN_PAGE, template.id])
        template.invalidate_recordset(["body_html", "email_layout_xmlid"])
    _logger.info("%s %s : habillage des visites sur la mise en page commune", MODULE, VERSION)

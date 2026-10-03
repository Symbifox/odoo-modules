"""Retirer la coquille des courriels de signature.

Les quatre gabarits (invitation, relance, document signé, refus) portaient leur
propre coquille : enveloppe, en-tête foncé au logo et au titre, filet, carte,
pied au nom de la société. Ils ne gardent que le contenu ; la mise en page
commune (`bf_onboarding_base.bf_mail_layout`, que bluefox_branding remplace par
la sienne) les habille à l'envoi. Le titre de l'en-tête devient un surtitre.

Les gabarits sont `noupdate` : la mise à jour ne réécrit ni la source ni les
autres langues. On découpe chaque langue stockée qui porte encore une coquille,
avec le même outil que la source. Tout ou rien par gabarit : si une langue garde
une coquille introuvable, ou porte un corps refait à la main, le gabarit reste
tel quel et on le journalise. Une valeur déjà découpée ne porte plus de trace :
la migration rejouée ne la retouche pas.
"""
import json
import logging
import re

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

GABARITS = (
    "mail_template_sign_request",
    "mail_template_sign_reminder",
    "mail_template_sign_completed",
    "mail_template_sign_refused",
)
MISE_EN_PAGE = "bf_onboarding_base.bf_mail_layout"

DIV = re.compile(r"<div\b[^>]*>|</div>")
ENVELOPPE = re.compile(r'<div style="font-family:Lexend,system-ui,Arial,sans-serif;[^"]*max-width:600px[^"]*">')
TITRE = re.compile(r'<td align="right"[^>]*font-size:20px[^>]*>\s*(.*?)\s*</td>', re.S)
CARTE = re.compile(r'<div style="background:#ffffff; border:1px solid #e3e7eb; border-top:0; padding:24px;[^"]*">')
SURTITRE = ('<p style="margin:0 0 4px 0; font-size:12px; font-weight:600; '
            'letter-spacing:0.6px; text-transform:uppercase; color:#6B7280;">{}</p>')
COULEUR = r"(#[0-9A-Fa-f]{3,8}|\{\{[^}]*\}\}|#\{[^}]*\})"
FOND = re.compile(r"(?<![\w-])background:\s*" + COULEUR + r"\s*(?=;|\"|')")
BORDURE = re.compile(
    r"(?<![\w-])border-(left|right|top|bottom):\s*(\d+px)\s+solid\s+" + COULEUR + r"\s*(?=;|\"|')")
# Ce que seule l'ancienne coquille portait.
TRACES = ("max-width:600px", "border-radius:10px 10px 0 0", "/brand/logo/")


def styles_admis(corps):
    corps = FOND.sub(r"background-color:\1", corps)
    return BORDURE.sub(
        r"border-\1-width:\2; border-\1-style:solid; border-\1-color:\3", corps)


def _fin_div(corps, debut):
    """Position juste après le `</div>` qui ferme la `div` ouverte à `debut`."""
    profondeur = 0
    for m in DIV.finditer(corps, debut):
        balise = m.group(0)
        if balise.startswith("</"):
            profondeur -= 1
        elif not balise.endswith("/>"):
            profondeur += 1
        if profondeur == 0:
            return m.end()
    return None


def retirer_coquille(corps):
    """(nouveau corps, True), ou (None, False) si les repères manquent."""
    corps = corps or ""
    enveloppe = ENVELOPPE.search(corps)
    if not enveloppe:
        return None, False
    fin = _fin_div(corps, enveloppe.start())
    titre = TITRE.search(corps, enveloppe.end())
    carte = CARTE.search(corps, enveloppe.end())
    if fin is None or not titre or not carte or not titre.start() < carte.start() < fin:
        return None, False
    fin_carte = _fin_div(corps, carte.start())
    if fin_carte is None or fin_carte > fin:
        return None, False
    interieur = styles_admis(corps[carte.end():fin_carte - len("</div>")].strip())
    nouveau = "%s%s\n%s\n%s" % (
        corps[:enveloppe.start()], SURTITRE.format(titre.group(1).strip()), interieur,
        corps[fin:].lstrip())
    return nouveau, True


def a_une_coquille(corps):
    return any(trace in (corps or "") for trace in TRACES)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.flush_all()
    for xmlid in GABARITS:
        template = env.ref(f"bf_sign.{xmlid}", raise_if_not_found=False)
        if not template:
            continue
        cr.execute("SELECT body_html FROM mail_template WHERE id = %s", [template.id])
        valeurs = cr.fetchone()[0] or {}
        nouvelles, refusees = {}, []
        for lang, corps in valeurs.items():
            if not a_une_coquille(corps):
                continue
            nouveau, ok = retirer_coquille(corps)
            if ok and not a_une_coquille(nouveau):
                nouvelles[lang] = nouveau
            else:
                refusees.append(lang)
        # Chaque langue doit finir découpée par cet outil : un corps refait à la main,
        # sans nos repères, porte son propre habillage et la mise en page commune
        # l'habillerait deux fois.
        refusees += [lang for lang, corps in valeurs.items()
                     if lang not in nouvelles and lang not in refusees
                     and "text-transform:uppercase; color:#6B7280;" not in (corps or "")]
        if refusees:
            _logger.warning(
                "bf_sign 18.0.3.27.0 : %s (%s) sans les repères de la coquille ni le surtitre, "
                "gabarit laissé tel quel", xmlid, ", ".join(sorted(refusees)))
            continue
        cr.execute(
            "UPDATE mail_template SET body_html = %s::jsonb, email_layout_xmlid = %s WHERE id = %s",
            [json.dumps(dict(valeurs, **nouvelles)), MISE_EN_PAGE, template.id])
        template.invalidate_recordset(["body_html", "email_layout_xmlid"])
        _logger.info("bf_sign 18.0.3.27.0 : %s sur la mise en page commune%s",
                     xmlid, " (coquille retirée : %s)" % ", ".join(sorted(nouvelles)) if nouvelles else "")

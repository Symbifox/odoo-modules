"""Retirer la coquille des courriels d'hébergement.

Les sept gabarits du module (rapport de sauvegarde, digest, cinq gabarits client)
portaient leur propre coquille : fond, carte de 600 ou 700 px, en-tête foncé au logo et
au titre, filet, pied au nom de la société avec slogan, liens de politique, double
filet. Ils ne gardent que le contenu de la carte ; la mise en page commune
(`bf_onboarding_base.bf_mail_layout`, que bluefox_branding remplace par la sienne) les
habille à l'envoi. Le titre de l'en-tête devient un surtitre ; le modèle polyvalent,
dont l'en-tête ne portait qu'un slogan, n'en a pas. La mention du pied qui n'est pas de
la marque (« Rapport généré automatiquement… ») reste, en petits caractères.

Tout ou rien par gabarit : chaque langue stockée doit être découpée par cet outil ou
déjà sans coquille. Un texte inconnu hors du contenu fait refuser le gabarit plutôt que
de le perdre. Les gabarits client et le digest ne sont pas `noupdate` : la mise à jour
réécrit la source avant cette migration, qui tolère donc une langue déjà découpée.
"""
import html
import json
import logging
import re

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

MODULE = "hosting_management"
VERSION = "18.0.2.62.0"
GABARITS = (
    "email_template_backup_report",
    "mail_template_hosting_digest",
    "mail_template_client_generic",
    "mail_template_client_monthly_report",
    "mail_template_client_maintenance_notice",
    "mail_template_client_intervention_report",
    "mail_template_client_welcome",
)
MISE_EN_PAGE = "bf_onboarding_base.bf_mail_layout"

BALISE_TABLE = re.compile(r"<table\b[^>]*?/?>|</table>")
BALISE_TD = re.compile(r"<td\b[^>]*?/?>|</td>")
CARTE = re.compile(r'<table\b[^>]*\bwidth="(?:600|700)"[^>]*border-radius:(?:12|16)px')
ENTETE = re.compile(r"<td\b[^>]*border-radius:(?:12|16)px (?:12|16)px 0 0;[^>]*>")
TITRE = re.compile(r'<td\b[^>]*font-size:(?:19|2[0-9])px[^>]*>\s*([^<]*?)\s*</td>', re.S)
CONTENU = re.compile(r'<td style="padding:(?:24px|32px|36px 32px 32px 32px);[^"]*">')
SURTITRE = ('<p style="margin:0 0 4px 0; font-size:12px; font-weight:600; '
            'letter-spacing:0.6px; text-transform:uppercase; color:#6B7280;">{}</p>')
PETITS_CARACTERES = '<p style="font-size:12px; line-height:18px; color:#6B7280; margin:24px 0 0 0;">{}</p>'
# Ce que le pied et l'en-tête disaient de la marque : jamais repris.
MARQUE = (
    "Solutions éthiques et souveraines pour vos données.", "Solutions TI éthiques",
    "Politique de confidentialité", "Termes et conditions", "Confidentialité", "Conditions",
    "Gestion d'hébergement par", "contiGNU par", "Blue Fox", "|", "·",
    # Rédactions anciennes : un bloc de contact aux valeurs factices des réglages.
    "Votre hébergeur", "service@example.com", "555-555-5555", "bluefoxconsultant.com",
)
# Mentions du pied qui ne sont pas de la marque : gardées.
MENTIONS = ("Rapport généré automatiquement par votre module d'hébergement.",)
COULEUR = r"(#[0-9A-Fa-f]{3,8}|\{\{[^}]*\}\}|#\{[^}]*\})"
BORDURE = re.compile(
    r"(?<![\w-])border-(left|right):\s*(\d+px)\s+solid\s+" + COULEUR + r"\s*(?=;|\"|')")
# Ce que seule l'ancienne coquille portait.
TRACES = ('width="600"', 'width="700"', "border-radius:16px 16px 0 0",
          "border-radius:12px 12px 0 0", "/brand/logo/")


def styles_admis(corps):
    """Le corps d'un message envoyé par le compositeur ne garde pas les raccourcis."""
    return BORDURE.sub(r"border-\1-width:\2; border-\1-style:solid; border-\1-color:\3", corps)


def _fin(motif, corps, debut):
    """Position juste après la balise fermante qui équilibre celle ouverte à `debut`."""
    profondeur = 0
    for m in motif.finditer(corps, debut):
        balise = m.group(0)
        if balise.startswith("</"):
            profondeur -= 1
        elif not balise.endswith("/>"):
            profondeur += 1
        if profondeur == 0:
            return m.end()
    return None


def _texte(fragment):
    """Le texte lisible d'un fragment, expressions comprises, entités décodées."""
    fragment = re.sub(r'<t t-out="[^"]*"\s*/>', " ", fragment)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(fragment).replace("\xa0", " ")).strip()


def _reste(texte):
    """Ce qui reste d'un texte une fois la marque et les mentions connues retirées."""
    for morceau in MARQUE + MENTIONS:
        texte = texte.replace(morceau, " ")
    return re.sub(r"\s+", "", texte.replace("&nbsp;", ""))


def a_une_coquille(corps):
    return any(trace in (corps or "") for trace in TRACES)


def retirer_coquille(corps):
    """(nouveau corps, True) si la coquille a été retirée sans perte ; sinon (None, False)."""
    debut = corps.find("<table")
    carte = CARTE.search(corps)
    entete = carte and ENTETE.search(corps, carte.end())
    if debut < 0 or not entete:
        return None, False
    fin_carte = _fin(BALISE_TABLE, corps, carte.start())
    fin_entete = _fin(BALISE_TD, corps, entete.start())
    if fin_carte is None or fin_entete is None:
        return None, False
    titre = TITRE.search(corps[entete.end():fin_entete])
    titre = _texte(titre.group(1)) if titre else ""
    if titre in MARQUE:
        titre = ""
    contenu = CONTENU.search(corps, fin_entete)
    if not contenu or contenu.start() > fin_carte:
        return None, False
    fin_contenu = _fin(BALISE_TD, corps, contenu.start())
    if fin_contenu is None or fin_contenu > fin_carte:
        return None, False
    # Rien d'autre que la marque, avant comme après : sinon refus.
    if _reste(_texte(corps[debut:contenu.start()])) not in ("", _reste(titre)):
        return None, False
    pied = _texte(corps[fin_contenu:])
    if _reste(pied):
        return None, False
    mention = next((m for m in MENTIONS if m in pied), "")
    interieur = styles_admis(corps[contenu.end():fin_contenu - len("</td>")].strip())
    morceaux = [corps[:debut].rstrip()]
    if titre:
        morceaux.append(SURTITRE.format(html.escape(titre, quote=False)))
    morceaux.append(interieur)
    if mention:
        morceaux.append(PETITS_CARACTERES.format(html.escape(mention, quote=False)))
    return "\n".join(m for m in morceaux if m) + "\n", True


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.flush_all()
    for xmlid in GABARITS:
        template = env.ref(f"{MODULE}.{xmlid}", raise_if_not_found=False)
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
        if refusees:
            _logger.warning("%s %s : %s (%s) non découpé, gabarit laissé tel quel",
                            MODULE, VERSION, xmlid, ", ".join(sorted(refusees)))
            continue
        cr.execute(
            "UPDATE mail_template SET body_html = %s::jsonb, email_layout_xmlid = %s WHERE id = %s",
            [json.dumps(dict(valeurs, **nouvelles)), MISE_EN_PAGE, template.id])
        template.invalidate_recordset(["body_html", "email_layout_xmlid"])
        _logger.info("%s %s : %s sur la mise en page commune%s", MODULE, VERSION, xmlid,
                     " (coquille retirée : %s)" % ", ".join(sorted(nouvelles)) if nouvelles else "")

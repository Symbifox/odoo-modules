"""Retirer la coquille des courriels de la matrice de connaissances.

Les quatre gabarits (envoi d'un document, rappel, mise à jour disponible,
rapport documentaire) portaient une coquille complète : fond, carte, en-tête
au logo de l'éditeur codé en dur, slogan et politique de confidentialité de
l'éditeur au pied, quel que soit le locataire. Ils ne gardent que le contenu ;
la mise en page commune (`bf_onboarding_base.bf_mail_layout`, que
bluefox_branding remplace par la sienne) les habille à l'envoi, aux couleurs,
au logo et au pied de la société. Le titre de l'en-tête devient un surtitre,
et l'adresse de contact factice du contenu devient celle de la société.

Les gabarits sont `noupdate` chez certains locataires et pas chez d'autres :
là où ils ne le sont pas, la mise à jour a déjà réécrit la source (en_US) et
le champ de mise en page, pas les autres langues. On découpe donc chaque langue
stockée qui porte encore une coquille, avec le même outil que la source. Tout
ou rien par gabarit : si une langue garde une coquille introuvable, ou porte un
corps refait à la main, le gabarit reste tel quel et on le journalise. Là où
le gabarit n'est pas `noupdate`, « tel quel » s'entend après la mise à jour :
la source anglaise et le champ de mise en page y sont déjà neufs, et une
langue refusée partirait habillée deux fois ; le journal le signale. Aucun
corps relevé ne déclenche ce cas. Une valeur déjà découpée ne porte plus de
trace : la migration rejouée ne la retouche pas.
"""
import json
import logging
import re

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

GABARITS = (
    "mail_template_document_distribution",
    "mail_template_document_reminder",
    "mail_template_document_update_available",
    "mail_template_document_dashboard_report",
)
MISE_EN_PAGE = "bf_onboarding_base.bf_mail_layout"

BALISE_TABLE = re.compile(r"<table\b|</table>")
BALISE_TD = re.compile(r"<td\b|</td>")
TITRE = re.compile(r'<td align="right"[^>]*font-size:2[02]px[^>]*>\s*(.*?)\s*</td>', re.S)
ACCENT = re.compile(r"<td\b[^>]*height:4px;\s*line-height:4px[^>]*>")
CONTENU = re.compile(r'<td style="padding:24px;?">')
CONTACT = re.compile(
    r'<p ((?:(?!</p>)[^>])*)>((?:(?!</p>).)*?)<a href="mailto:service@example\.com"([^>]*)>'
    r"service@example\.com</a>((?:(?!</p>).)*?)</p>", re.S)
SURTITRE = ('<p style="margin:0 0 4px 0; font-size:12px; font-weight:600; '
            'letter-spacing:0.6px; text-transform:uppercase; color:#6B7280;">{}</p>')
COULEUR = r"(#[0-9A-Fa-f]{3,8}|\{\{[^}]*\}\})"
FOND = re.compile(r"(?<![\w-])background:\s*" + COULEUR + r"\s*(?=;|\"|')")
BORDURE = re.compile(
    r"(?<![\w-])border-(left|right|top|bottom):\s*(\d+px)\s+solid\s+" + COULEUR + r"\s*(?=;|\"|')")
# Ce que seule l'ancienne coquille portait.
TRACES = ("width=\"600\"", "website/1/logo/", "border-radius:12px 12px 0 0",
          "Solutions éthiques et souveraines")


def styles_admis(corps):
    corps = FOND.sub(r"background-color:\1", corps)
    return BORDURE.sub(
        r"border-\1-width:\2; border-\1-style:solid; border-\1-color:\3", corps)


def contact_de_la_societe(corps):
    """La phrase de contact cite l'adresse de la société, ou disparaît sans elle."""
    return CONTACT.sub(
        r'<p t-if="company and company.email" \1>\2'
        r'<a t-attf-href="mailto:{{ company.email }}"\3><t t-out="company.email"/></a>\4</p>',
        corps)


def _fin(motif, corps, debut):
    """Position juste après la balise fermante qui équilibre celle ouverte à `debut`."""
    profondeur = 0
    for m in motif.finditer(corps, debut):
        profondeur += -1 if m.group(0).startswith("</") else 1
        if profondeur == 0:
            return m.end()
    return None


def retirer_coquille(corps):
    """(nouveau corps, True), ou (None, False) si les repères manquent.

    Le corps garde ce qui précède la coquille (les `t-set` de marque) et ce qui la
    suit ; seul le contenu de la carte en sort.
    """
    corps = corps or ""
    debut = corps.find("<table")
    if debut < 0:
        return None, False
    fin = _fin(BALISE_TABLE, corps, debut)
    accent = ACCENT.search(corps, debut)
    titre = TITRE.search(corps, debut)
    if fin is None or not accent or accent.start() > fin or not titre or titre.start() > accent.start():
        return None, False
    cellule = CONTENU.search(corps, accent.end())
    if not cellule or cellule.start() > fin:
        return None, False
    fin_cellule = _fin(BALISE_TD, corps, cellule.start())
    if fin_cellule is None or fin_cellule > fin:
        return None, False
    interieur = corps[cellule.end():fin_cellule - len("</td>")].strip()
    interieur = contact_de_la_societe(styles_admis(interieur))
    nouveau = "%s%s\n%s\n%s" % (
        corps[:debut], SURTITRE.format(titre.group(1).strip()), interieur, corps[fin:].lstrip())
    return nouveau, True


def a_une_coquille(corps):
    return any(trace in (corps or "") for trace in TRACES)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.flush_all()
    for xmlid in GABARITS:
        template = env.ref(f"project_knowledge_matrix.{xmlid}", raise_if_not_found=False)
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
        # Chaque langue doit finir découpée par cet outil (ou déjà écrite ainsi par la
        # mise à jour) : un corps refait à la main, sans nos repères, porte son propre
        # habillage et la mise en page commune l'habillerait deux fois.
        refusees += [lang for lang, corps in valeurs.items()
                     if lang not in nouvelles and lang not in refusees
                     and "text-transform:uppercase; color:#6B7280;" not in (corps or "")]
        if refusees:
            _logger.warning(
                "project_knowledge_matrix 18.0.13.3.3 : %s (%s) sans les repères de la coquille "
                "ni le surtitre, gabarit laissé tel quel", xmlid, ", ".join(sorted(refusees)))
            continue
        cr.execute(
            "UPDATE mail_template SET body_html = %s::jsonb, email_layout_xmlid = %s WHERE id = %s",
            [json.dumps(dict(valeurs, **nouvelles)), MISE_EN_PAGE, template.id])
        template.invalidate_recordset(["body_html", "email_layout_xmlid"])
        _logger.info("project_knowledge_matrix 18.0.13.3.3 : %s sur la mise en page commune%s",
                     xmlid, " (coquille retirée : %s)" % ", ".join(sorted(nouvelles)) if nouvelles else "")

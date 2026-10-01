"""Retirer la coquille des courriels de consentement.

Les six gabarits de consentement (demande, rappels, avis d'expiration,
confirmations) portaient chacun DEUX coquilles complètes, une par langue
(`<t t-if="is_en">` … `<t t-else="">`) : fond, carte, en-tête au logo et au
titre, filets, pied au nom de la société et bandeau du bas. Ils ne gardent
désormais que le contenu, et la mise en page commune
(`bf_onboarding_base.bf_mail_layout`, que bluefox_branding remplace par la
sienne) les habille à l'envoi, par le champ `email_layout_xmlid` du gabarit.
Le titre de l'en-tête devient un surtitre ; le lien « Preferences » du pied,
propre au consentement, passe sous le contenu quand celui-ci ne l'offre pas.

Les gabarits sont en `noupdate` : la mise à jour ne réécrit ni la source ni
les traductions, ni le champ de mise en page. On découpe donc chaque langue
stockée avec le même outil que celui qui a découpé la source, ce qui garde
une retouche locale qui aurait conservé la coquille. Tout ou rien par
gabarit : si une langue n'a pas ses repères (un corps refait à la main, comme
une demande de consentement refaite par un locataire), le gabarit reste tel quel, sans
mise en page commune, et on le journalise. Une valeur déjà découpée n'a plus
de repères : la migration rejouée ne la retouche pas.
"""
import json
import logging
import re

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

GABARITS = (
    "mail_template_consent_request",
    "mail_template_consent_expiring",
    "mail_template_consent_reminder_1",
    "mail_template_consent_reminder_2",
    "mail_template_consent_renewal_confirmation",
    "mail_template_consent_granted_confirmation",
)
MISE_EN_PAGE = "bf_onboarding_base.bf_mail_layout"

DEBUT = re.compile(r'<table role="presentation"[^>]*background-color:#F8FAFC;[^>]*>')
BALISE_TABLE = re.compile(r'<table\b|</table>')
TITRE = re.compile(r'<td align="right"[^>]*font-size:\d+px[^>]*>\s*(.*?)\s*</td>', re.S)
CONTENU = re.compile(
    r'<!-- Main Content -->\s*<tr>\s*<td[^>]*>(.*?)</td>\s*</tr>\s*<!-- Separator -->', re.S)
PREFERENCES = re.compile(r'<a [^>]*/my/privacy/preferences[^>]*>.*?</a>', re.S)
SURTITRE = ('<p style="margin:0 0 4px 0; font-size:12px; font-weight:600; '
            'letter-spacing:0.6px; text-transform:uppercase; color:#6B7280;">{}</p>')
PIED = '<p style="margin:24px 0 0 0; font-size:12px; color:#6B7280;">{}</p>'
COULEUR = r'(#[0-9A-Fa-f]{3,8}|\{\{[^}]*\}\})'
FOND = re.compile(r'(?<![\w-])background:\s*' + COULEUR + r'\s*(?=;|"|\')')
BORDURE = re.compile(
    r'(?<![\w-])border-(left|right|top|bottom):\s*(\d+px)\s+solid\s+' + COULEUR + r'\s*(?=;|"|\')')
TRACES = ('#F8FAFC', 'width="600"', '/brand/logo/', '<!-- Footer -->')


def styles_admis(corps):
    corps = FOND.sub(r'background-color:\1', corps)
    return BORDURE.sub(
        r'border-\1-width:\2; border-\1-style:solid; border-\1-color:\3', corps)


def _fin_de_table(corps, debut):
    """Position juste après le `</table>` qui ferme la table ouverte à `debut`."""
    profondeur = 0
    for m in BALISE_TABLE.finditer(corps, debut):
        profondeur += 1 if m.group(0) == "<table" else -1
        if profondeur == 0:
            return m.end()
    return None


def _une_coquille(segment):
    titre = TITRE.search(segment)
    contenu = CONTENU.search(segment)
    if not (titre and contenu) or titre.start() > contenu.start():
        return None
    morceaux = [SURTITRE.format(titre.group(1).strip()), styles_admis(contenu.group(1).strip())]
    # Le pied portait le lien vers les préférences de la personne ; la mise en page
    # commune ne l'a pas. On le garde sous le contenu, sauf si le contenu l'offre déjà.
    pref = PREFERENCES.search(segment, contenu.end())
    if pref and not PREFERENCES.search(contenu.group(1)):
        morceaux.append(PIED.format(pref.group(0)))
    return "\n".join(morceaux)


def retirer_coquilles(corps):
    """(nouveau corps, nombre de coquilles retirées), ou (None, 0) si l'une échoue.

    Tout ou rien : une coquille dont les repères manquent laisse le corps intact,
    pour ne jamais livrer une langue habillée deux fois et l'autre une seule.
    """
    corps = corps or ""
    sortie, curseur, n = [], 0, 0
    for debut in DEBUT.finditer(corps):
        if debut.start() < curseur:
            continue
        fin = _fin_de_table(corps, debut.start())
        if fin is None:
            return None, 0
        nouveau = _une_coquille(corps[debut.start():fin])
        if nouveau is None:
            return None, 0
        sortie += [corps[curseur:debut.start()], nouveau]
        curseur, n = fin, n + 1
    if not n:
        return None, 0
    sortie.append(corps[curseur:])
    return "".join(sortie), n


def _poser(cr, template_id, valeurs):
    cr.execute("UPDATE mail_template SET body_html = %s::jsonb, email_layout_xmlid = %s"
               " WHERE id = %s", [json.dumps(valeurs), MISE_EN_PAGE, template_id])


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid in GABARITS:
        template = env.ref(f"privacy_consent.{xmlid}", raise_if_not_found=False)
        if not template:
            continue
        cr.execute("SELECT body_html FROM mail_template WHERE id = %s", [template.id])
        valeurs = cr.fetchone()[0] or {}
        nouvelles, refusees = {}, []
        for lang, corps in valeurs.items():
            nouveau, n = retirer_coquilles(corps)
            if nouveau is None:
                refusees.append(lang)
            else:
                nouvelles[lang] = nouveau
        if refusees:
            if any(any(trace in (valeurs.get(lang) or "") for trace in TRACES) for lang in refusees):
                _logger.warning(
                    "privacy_consent 18.0.5.3.0 : repères introuvables dans %s (%s), "
                    "gabarit laissé tel quel, sans mise en page commune", xmlid, ", ".join(refusees))
            else:
                _logger.info(
                    "privacy_consent 18.0.5.3.0 : %s sans coquille connue (%s), laissé tel quel",
                    xmlid, ", ".join(refusees))
            continue
        if not nouvelles:
            continue
        _poser(cr, template.id, dict(valeurs, **nouvelles))
        template.invalidate_recordset(["body_html", "email_layout_xmlid"])
        _logger.info("privacy_consent 18.0.5.3.0 : coquilles retirées de %s (%s)",
                     xmlid, ", ".join(sorted(nouvelles)))

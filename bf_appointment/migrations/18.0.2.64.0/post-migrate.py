"""Retirer la coquille des courriels de rendez-vous.

Les gabarits portaient leur propre coquille : fond, carte de 600 px, en-tête foncé
au logo et au titre, filet, pied au nom de marque, slogan, liens de politique,
double filet. Ils ne gardent que le contenu de la carte ; la mise en page commune
(`bf_onboarding_base.bf_mail_layout`, que bluefox_branding remplace par la sienne)
les habille à l'envoi. Le titre de l'en-tête devient un surtitre.

Le pied ne part pas toujours : une mention qui n'est pas de la marque (« Notification
interne », « Invitation transmise via… ») reste, en petits caractères à la fin du
contenu, au nom de la société. Le pied de marque (nom, slogan, liens de politique)
cède la place à celui de la mise en page commune.

Dans le contenu : les accents prennent les couleurs de marque de la société
(`report_brand_*`, celles de la mise en page) au lieu de `appointment_brand_*`, et
la phrase de contact lit `res.company.bf_appointment_contact()`, qui écarte les
valeurs factices (service@example.com, 555-555-5555) au profit de l'adresse et du
téléphone de la société, et se tait quand il n'y a rien à citer.

Les gabarits sont `noupdate` : la mise à jour ne réécrit ni la source ni les
autres langues (une base peut en porter d'autres, écrites à la main). On découpe chaque
langue stockée qui porte encore une coquille, avec le même outil que la source.
Tout ou rien par gabarit : si une langue garde une coquille introuvable, ou porte
un corps refait à la main, le gabarit reste tel quel et on le journalise. Une
valeur déjà découpée ne porte plus de trace : la migration rejouée ne la retouche pas.
"""
import json
import logging
import re

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

MODULE = "bf_appointment"
VERSION = "18.0.2.64.0"
# « Rappel (ancien) » n'a pas de coquille et rien ne l'envoie : il reste tel quel.
GABARITS = (
    "mail_template_intake_acknowledgement",
    "mail_template_appointment_confirmation",
    "mail_template_reminder_2d",
    "mail_template_reminder_1d",
    "mail_template_reminder_2h",
    "mail_template_reminder_1h",
    "mail_template_followup_immediate",
    "mail_template_followup_1h",
    "mail_template_followup_2h",
    "mail_template_organizer_new_booking",
    "mail_template_appointment_cancellation",
    "mail_template_organizer_reschedule",
    "mail_template_organizer_cancellation",
    "mail_template_guest_invitation",
    "mail_template_guest_confirmation_request",
)
MISE_EN_PAGE = "bf_onboarding_base.bf_mail_layout"
# La société du gabarit, quand une rédaction ancienne n'a pas de préambule.
SOCIETE_PAR_DEFAUT = "(object.type_id.company_id) or user.company_id"
SOCIETE_PAR_GABARIT = {
    "mail_template_guest_invitation": "(object.booking_id.type_id.company_id) or user.company_id",
}

BALISE_TABLE = re.compile(r"<table\b|</table>")
BALISE_TD = re.compile(r"<td\b|</td>")
CARTE = re.compile(r'<table\b[^>]*\bwidth="600"[^>]*border-radius:12px;')
ENTETE = re.compile(r"<td\b[^>]*border-radius:12px 12px 0 0;[^>]*>")
TITRE = re.compile(r'<td align="right"[^>]*font-size:(?:19|2[0-4])px[^>]*>\s*(.*?)\s*</td>', re.S)
ACCENT = re.compile(r"<td\b[^>]*height:4px;\s*line-height:4px[^>]*>")
CONTENU = re.compile(r'<td style="padding:24px;[^"]*">')
# Rédaction plus ancienne de l'avis d'annulation à l'organisateur : une enveloppe
# `div` de 600 px qui s'ouvre sur un titre `h2`.
ENVELOPPE_DIV = re.compile(
    r'<div style="font-family:[^"]*max-width:600px; margin:0 auto; padding:24px;">')
TITRE_H2 = re.compile(r"\s*<h2\b[^>]*>\s*(.*?)\s*</h2>", re.S)
BALISE_DIV = re.compile(r"<div\b[^>]*>|</div>")
PIED = re.compile(r"<td\b[^>]*border-radius:0 0 12px 12px;[^>]*>")
PIED_DE_MARQUE = ("appointment_brand_tagline", "appointment_brand_privacy_url",
                  "appointment_brand_terms_url")
NOM_DE_MARQUE = re.compile(
    r"\((?:[\w.]+ and )?\(?[\w.]+\.appointment_brand_name(?: or [\w.]+\.name)?\)?\) or '[^']*'")
ENROBAGE = re.compile(r"</?(?:table|tbody|tr|td)\b[^>]*>")
PETITS_CARACTERES = '<p style="font-size:12px; line-height:18px; color:#6B7280; margin:24px 0 0 0;">{}</p>'
SOCIETE = re.compile(r'<t t-set="company" t-value="[^"]*"/>')
SURTITRE = ('<p style="margin:0 0 4px 0; font-size:12px; font-weight:600; '
            'letter-spacing:0.6px; text-transform:uppercase; color:#6B7280;">{}</p>')
# Les rédactions plus anciennes, encore en base chez certains locataires, replient
# sur une couleur en dur, ou sur « '{{ brand_dark }}' » imbriqué dans l'expression.
MARQUE = re.compile(r"\((?:[\w.]+ and )?[\w.]+\.appointment_brand_(primary|dark)\) or "
                    r"(?:brand_\1|'#[0-9A-Fa-f]{3,8}'|'\{\{ brand_\1 \}\}')")
CONTACT = (
    (re.compile(r"\((?:[\w.]+ and )?[\w.]+\.appointment_brand_support_email\) or '[^']*'"), "contact['email']"),
    (re.compile(r"\((?:[\w.]+ and )?[\w.]+\.appointment_brand_support_phone\) or '[^']*'"), "contact['phone']"),
    (re.compile(r"\((?:[\w.]+ and )?[\w.]+\.appointment_brand_support_phone_display\) or '[^']*'"),
     "contact['phone_display']"),
)
PARAGRAPHE_CONTACT = re.compile(r"<p\b((?:(?!t-if)[^>])*)>((?:(?!</p>).)*?)</p>", re.S)
PREAMBULE = {
    "company": '<t t-set="company" t-value="{}"/>',
    "brand_primary": '<t t-set="brand_primary" t-value="(company and company.report_brand_primary) or \'#714B67\'"/>',
    "brand_dark": '<t t-set="brand_dark" t-value="(company and company.report_brand_dark) or \'#212529\'"/>',
}
APPEL_CONTACT = '<t t-set="contact" t-value="company.bf_appointment_contact()"/>'
COULEUR = r"(#[0-9A-Fa-f]{3,8}|\{\{[^}]*\}\}|#\{[^}]*\})"
FOND = re.compile(r"(?<![\w-])background:\s*" + COULEUR + r"\s*(?=;|\"|')")
BORDURE = re.compile(
    r"(?<![\w-])border-(left|right):\s*(\d+px)\s+solid\s+" + COULEUR + r"\s*(?=;|\"|')")
# Ce que seule l'ancienne coquille portait.
TRACES = ('width="600"', "max-width:600px; margin:0 auto; padding:24px;", "border-radius:12px 12px 0 0", "appointment_brand_logo_url",
          "Solutions éthiques et souveraines")
DECOUPE = "text-transform:uppercase; color:#6B7280;"


def styles_admis(corps):
    """Le corps d'un message ne garde que les styles admis : pas de raccourcis."""
    corps = FOND.sub(r"background-color:\1", corps)
    return BORDURE.sub(
        r"border-\1-width:\2; border-\1-style:solid; border-\1-color:\3", corps)


def _contact(corps):
    for motif, nom in CONTACT:
        corps = motif.sub(nom, corps)

    def garde(m):
        texte = m.group(2)
        conditions = [c for c, cle in (("contact['email']", "contact['email']"),
                                       ("contact['phone_display']", "contact['phone"))
                      if cle in texte]
        if not conditions:
            return m.group(0)
        return '<p t-if="%s"%s>%s</p>' % (" and ".join(conditions), m.group(1), texte)

    return PARAGRAPHE_CONTACT.sub(garde, corps)


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


def _preambule(avant, interieur, societe):
    """Les `t-set` dont le contenu a besoin. Des rédactions anciennes n'en ont aucun."""
    manquants = [nom for nom in ("brand_primary", "brand_dark")
                 if nom in interieur and 't-set="%s"' % nom not in avant]
    lignes = [PREAMBULE[nom] for nom in manquants]
    if not SOCIETE.search(avant):
        lignes.insert(0, PREAMBULE["company"].format(societe))
    if "contact[" in interieur:
        lignes.append(APPEL_CONTACT)
    if not lignes:
        return avant
    societe_posee = SOCIETE.search(avant)
    if societe_posee:
        return "%s\n%s%s" % (avant[:societe_posee.end()], "\n".join(lignes), avant[societe_posee.end():])
    return "%s\n%s" % ("\n".join(lignes), avant.lstrip())


def _mot_de_pied(pied):
    """La mention du pied qui n'est pas de la marque, en petits caractères ; sinon rien."""
    if not pied or any(trace in pied for trace in PIED_DE_MARQUE):
        return ""
    dedans = NOM_DE_MARQUE.sub("company.name", ENROBAGE.sub("", pied)).strip()
    if not re.sub(r"<[^>]+>|\s|&#160;", "", dedans):
        return ""
    if dedans.startswith("<p"):
        return re.sub(r"margin:0;", "margin:24px 0 0 0;", dedans, count=1)
    return PETITS_CARACTERES.format(dedans)


def _assembler(avant, titre, interieur, apres, societe=SOCIETE_PAR_DEFAUT):
    """Surtitre, contenu aux styles admis et aux couleurs de la société, contact gardé."""
    if not titre or "<" in titre:
        return None, False
    interieur = _contact(MARQUE.sub(r"brand_\1", styles_admis(interieur.strip())))
    avant = _preambule(avant, interieur, societe)
    if "appointment_brand_" in interieur:
        return None, False
    return "%s%s\n%s\n%s" % (
        avant.rstrip() + "\n", SURTITRE.format(titre), interieur, apres.lstrip()), True


def _retirer_enveloppe_div(corps, societe):
    enveloppe = ENVELOPPE_DIV.search(corps)
    if not enveloppe:
        return None, False
    fin = _fin(BALISE_DIV, corps, enveloppe.start())
    titre = TITRE_H2.match(corps, enveloppe.end())
    if fin is None or not titre:
        return None, False
    return _assembler(corps[:enveloppe.start()], titre.group(1).strip(),
                      corps[titre.end():fin - len("</div>")], corps[fin:], societe)


def retirer_coquille(corps, societe=SOCIETE_PAR_DEFAUT):
    """(nouveau corps, True), ou (None, False) si les repères manquent.

    Le corps garde ce qui précède la coquille (les `t-set` de marque) et ce qui la
    suit ; seul le contenu de la carte en sort. Deux formes : la carte en tableaux,
    et l'ancienne enveloppe `div` de l'avis d'annulation à l'organisateur.
    """
    corps = corps or ""
    if ENVELOPPE_DIV.search(corps):
        return _retirer_enveloppe_div(corps, societe)
    debut = corps.find("<table")
    if debut < 0:
        return None, False
    fin = _fin(BALISE_TABLE, corps, debut)
    carte = CARTE.search(corps, debut)
    if fin is None or not carte or carte.start() > fin:
        return None, False
    entete = ENTETE.search(corps, carte.end())
    if not entete or entete.start() > fin:
        return None, False
    fin_entete = _fin(BALISE_TD, corps, entete.start())
    if fin_entete is None:
        return None, False
    dedans = corps[entete.end():fin_entete - len("</td>")]
    titre = TITRE.search(dedans)
    titre = titre.group(1).strip() if titre else dedans.strip()
    accent = ACCENT.search(corps, fin_entete)
    cellule = accent and CONTENU.search(corps, accent.end())
    if not cellule or cellule.start() > fin:
        return None, False
    fin_cellule = _fin(BALISE_TD, corps, cellule.start())
    if fin_cellule is None or fin_cellule > fin:
        return None, False
    mot = ""
    pied = PIED.search(corps, fin_cellule)
    if pied and pied.start() < fin:
        fin_pied = _fin(BALISE_TD, corps, pied.start())
        if fin_pied is None or fin_pied > fin:
            return None, False
        mot = _mot_de_pied(corps[pied.end():fin_pied - len("</td>")])
    interieur = corps[cellule.end():fin_cellule - len("</td>")].strip()
    return _assembler(corps[:debut], titre, interieur + ("\n" + mot if mot else ""),
                      corps[fin:], societe)


def a_une_coquille(corps):
    return any(trace in (corps or "") for trace in TRACES)


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
            nouveau, ok = retirer_coquille(
                corps, SOCIETE_PAR_GABARIT.get(xmlid, SOCIETE_PAR_DEFAUT))
            if ok and not a_une_coquille(nouveau):
                nouvelles[lang] = nouveau
            else:
                refusees.append(lang)
        # Chaque langue doit finir découpée par cet outil (ou déjà écrite ainsi) : un
        # corps refait à la main, sans nos repères, porte son propre habillage et la
        # mise en page commune l'habillerait deux fois.
        refusees += [lang for lang, corps in valeurs.items()
                     if lang not in nouvelles and lang not in refusees
                     and DECOUPE not in (corps or "")]
        if refusees:
            _logger.warning(
                "%s %s : %s (%s) sans les repères de la coquille ni le surtitre, "
                "gabarit laissé tel quel", MODULE, VERSION, xmlid, ", ".join(sorted(refusees)))
            continue
        cr.execute(
            "UPDATE mail_template SET body_html = %s::jsonb, email_layout_xmlid = %s WHERE id = %s",
            [json.dumps(dict(valeurs, **nouvelles)), MISE_EN_PAGE, template.id])
        template.invalidate_recordset(["body_html", "email_layout_xmlid"])
        _logger.info("%s %s : %s sur la mise en page commune%s", MODULE, VERSION, xmlid,
                     " (coquille retirée : %s)" % ", ".join(sorted(nouvelles)) if nouvelles else "")

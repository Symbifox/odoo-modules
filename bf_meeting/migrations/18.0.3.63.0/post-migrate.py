"""Retirer la coquille des courriels d'ordre du jour et de compte rendu.

Les deux gabarits portaient leur propre mise en page : fond, carte, en-tête au
logo `/brand/logo/<id>/meeting` avec le titre du document, bandes et pied
« envoyé depuis le module Rencontres ». Le composeur les enveloppait en plus
dans `mail.mail_notification_light`. Ils ne gardent désormais que le contenu,
et la mise en page commune (`bf_onboarding_base.bf_mail_layout`, que
bluefox_branding remplace par la sienne quand il est installé) les habille à
l'envoi, par le champ `email_layout_xmlid` du gabarit.

Le titre de l'en-tête (« Ordre du jour », « Compte rendu ») devient un surtitre
au-dessus du nom de la rencontre : l'en-tête commun porte le nom de la société.
Les styles abrégés que le composeur jetait (`background`, `border-left`) passent
en formes longues, qu'il garde.

Le courriel se traduit EN BLOC (voir 18.0.3.59.0) : la mise à jour réécrit
`en_US` et ne touche pas aux autres clés. Donc :

* une clé relevée comme COPIE de l'ancienne source par pre-migrate redevient
  la nouvelle source, au caractère près ;
* une vraie traduction (un compte rendu traduit en anglais, par exemple) passe par le
  même découpage que la source : on garde ce qui est entre les repères
  `<!-- Content -->` et `<!-- Separator -->`, le titre de l'en-tête devient le
  surtitre, le reste part.

Si le découpage ne trouve pas ses repères, la traduction reste telle quelle et
on le journalise : elle partira sous deux habillages, mais elle partira. Une
valeur déjà découpée n'a plus de repères, donc la migration rejouée ne la
retouche pas.
"""

import logging
import re
import textwrap

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

GABARITS = ("meeting_agenda_mail_template", "meeting_report_mail_template")

DEBUT_COQUILLE = re.compile(r'<table role="presentation"[^>]*background-color:#F8FAFC;[^>]*>')
TITRE = re.compile(r'<td align="right"[^>]*font-size:22px[^>]*>\s*(.*?)\s*</td>', re.S)
# Non gourmand : les tableaux du contenu ferment aussi des `</td></tr>`, mais
# jamais suivis du repère du séparateur.
CONTENU = re.compile(
    r'<!-- Content -->\s*<tr>\s*<td[^>]*>(.*?)</td>\s*</tr>\s*<!-- Separator -->', re.S)
SURTITRE = ('<p style="margin:0 0 4px 0; font-size:12px; font-weight:600; '
            'letter-spacing:0.6px; text-transform:uppercase; color:#6B7280;">{}</p>')

# Le composeur passe le corps par le nettoyeur de `mail.message`, qui ne garde que
# les propriétés CSS de sa liste (odoo/tools/mail.py, `_style_whitelist`) : ni
# `background` ni `border-left` en abrégé. Par ce chemin, l'en-tête du tableau des
# sujets et le bouton de contribution sortaient en blanc sur blanc. Les formes
# longues passent, et rendent pareil à l'envoi direct.
# Seules les valeurs qui sont une couleur seule (#hex, `{{ … }}`) changent de forme :
# une image, un dégradé ou une bordure d'un autre type restent tels quels, et le
# composeur les jettera comme avant.
COULEUR = r'(#[0-9A-Fa-f]{3,8}|\{\{[^}]*\}\})'
FOND = re.compile(r'(?<![\w-])background:\s*' + COULEUR + r'\s*(?=;|"|\')')
BORDURE_GAUCHE = re.compile(
    r'(?<![\w-])border-left:\s*(\d+px)\s+solid\s+' + COULEUR + r'\s*(?=;|"|\')')
# Ce qui ne peut venir que de l'ancienne coquille : s'il en reste après un
# découpage refusé, la traduction partira sous deux habillages.
TRACES = ('width="600"', '/brand/logo/', '#F8FAFC')


def styles_admis(corps):
    corps = FOND.sub(r'background-color:\1', corps)
    return BORDURE_GAUCHE.sub(
        r'border-left-width:\1; border-left-style:solid; border-left-color:\2', corps)


def retirer_coquille(corps):
    """Le contenu du gabarit sans sa coquille, ou None si les repères manquent."""
    debut = DEBUT_COQUILLE.search(corps or "")
    titre = TITRE.search(corps or "")
    contenu = CONTENU.search(corps or "")
    if not (debut and titre and contenu):
        return None
    if not debut.start() < titre.start() < contenu.start():
        return None
    entete = corps[:debut.start()].rstrip()
    interieur = styles_admis(textwrap.dedent(contenu.group(1).strip("\n")).strip())
    return f"{entete}\n{SURTITRE.format(titre.group(1).strip())}\n{interieur}\n"


def _copies(cr):
    cr.execute("SELECT to_regclass('pg_temp.bf_meeting_t26238_copies')")
    if not cr.fetchone()[0]:
        _logger.warning(
            "bf_meeting 18.0.3.63.0 : relevé des copies introuvable (pre-migrate pas "
            "passé dans cette connexion) ; les copies seront découpées, pas réalignées")
        return set()
    cr.execute("SELECT template_id, lang FROM bf_meeting_t26238_copies")
    return set(cr.fetchall())


def _poser(cr, template_id, lang, valeur):
    cr.execute(
        "UPDATE mail_template SET body_html = jsonb_set(body_html, %s, to_jsonb(%s::text))"
        " WHERE id = %s", [[lang], valeur, template_id])


def convertir_traductions(env):
    cr = env.cr
    copies = _copies(cr)
    for xmlid in GABARITS:
        template = env.ref(f"bf_meeting.{xmlid}", raise_if_not_found=False)
        if not template:
            continue
        cr.execute("SELECT body_html FROM mail_template WHERE id = %s", [template.id])
        valeurs = cr.fetchone()[0] or {}
        source = valeurs.get("en_US") or ""
        for lang, corps in valeurs.items():
            if lang == "en_US":
                continue
            if (template.id, lang) in copies:
                if corps != source:
                    _poser(cr, template.id, lang, source)
                    _logger.info("bf_meeting 18.0.3.63.0 : %s %s réaligné sur la source",
                                 xmlid, lang)
                continue
            nouveau = retirer_coquille(corps)
            if nouveau is None:
                if any(trace in (corps or "") for trace in TRACES):
                    _logger.warning(
                        "bf_meeting 18.0.3.63.0 : repères introuvables dans %s %s, "
                        "traduction laissée telle quelle (double habillage à l'envoi)",
                        xmlid, lang)
                continue
            _poser(cr, template.id, lang, nouveau)
            _logger.info("bf_meeting 18.0.3.63.0 : coquille retirée de %s %s", xmlid, lang)
        template.invalidate_recordset(["body_html"])
    cr.execute("DROP TABLE IF EXISTS bf_meeting_t26238_copies")


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    # La nouvelle source a été écrite par l'ORM : sans ce flush, la lecture SQL
    # verrait l'ancienne (constaté en 18.0.3.59.0).
    env.flush_all()
    convertir_traductions(env)
    env.invalidate_all()

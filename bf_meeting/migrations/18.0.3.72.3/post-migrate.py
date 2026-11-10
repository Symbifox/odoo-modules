"""Retirer les sujets du corps du courriel dans CHAQUE traduction.

Le corps du courriel recopiait les sujets et leurs points. Mesuré le 2026-11-04 sur dix
comptes rendus réels : médiane de 1206 mots et 60 puces par courriel, jusqu'à 1726 mots
et 97 puces. C'est le format qu'un lecteur insatisfait a qualifié de « fait par l'IA ».
Le détail part dans le PDF joint, qui
porte les sujets (`_bf_sujets_du_rapport`, avec repli sur les sujets de la fiche).

⚠️ Pourquoi une migration. `mail.template.body_html` se traduit EN BLOC : chaque clé
autre que `en_US` est un gabarit complet que la mise à jour du module NE TOUCHE PAS.
Mesuré sur une base d'essai le 2026-11-04 : après le `-u`, les deux comptes rendus anglais
avaient fondu (386 → 242 mots, 13 → 3 puces) et les huit en français n'avaient pas bougé
d'un mot. Sans cette migration, le changement ne sert personne, puisque presque tous les
comptes rendus partent en français.

Une traduction qui ne porte pas exactement un bloc de sujets est laissée telle quelle,
en le journalisant : mieux vaut un courriel trop long qu'un gabarit cassé qui ne part pas.

⛔ « Tâches existantes discutées » reste au courriel : le client doit lire la même liste
partout (PDF, courriel, copie d'échange), et `test_discussed_tasks` l'épingle.
"""

import logging
import re

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

DEBUT = '<t t-if="object.topic_ids">'
OUVRE = re.compile(r"<t(?=[\s>])(?![^>]*/>)")
FERME = re.compile(r"</t>")

# La phrase d'introduction, dans les deux langues où le gabarit l'a portée.
INTROS = (
    ("Veuillez trouver ci-dessous le compte rendu de la rencontre. Le PDF complet est "
     "joint à ce courriel.",
     "Le compte rendu complet est joint à ce courriel, en PDF."),
    ("Please find the meeting minutes below. The full PDF is attached to this email.",
     "The full minutes are attached to this email as a PDF."),
)


def retirer_sujets(corps):
    """Le corps sans son bloc de sujets, ou None s'il faut le laisser tel quel."""
    if DEBUT not in corps:
        return None
    if corps.count(DEBUT) != 1:
        return None
    debut = corps.index(DEBUT)
    i, profondeur = debut, 0
    while i < len(corps):
        o = OUVRE.search(corps, i)
        f = FERME.search(corps, i)
        if not o and not f:
            return None
        if o and (not f or o.start() < f.start()):
            profondeur += 1
            i = o.end()
            continue
        profondeur -= 1
        i = f.end()
        if profondeur == 0:
            break
    else:
        return None
    if profondeur != 0:
        return None
    reste = corps[:debut] + corps[i:]
    # la ligne vide laissée derrière
    return re.sub(r"\n{3,}", "\n\n", reste)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    # Le chargement des données a écrit la nouvelle source par l'ORM, pas encore en base :
    # sans ce flush, les lectures SQL voient l'ancienne.
    env.flush_all()
    template = env.ref("bf_meeting.meeting_report_mail_template", raise_if_not_found=False)
    if not template:
        return
    cr.execute("SELECT jsonb_object_keys(body_html) FROM mail_template WHERE id = %s",
               [template.id])
    langues = [lang for (lang,) in cr.fetchall() if lang != "en_US"]
    for lang in langues:
        cr.execute("SELECT body_html ->> %s FROM mail_template WHERE id = %s",
                   [lang, template.id])
        corps = cr.fetchone()[0] or ""
        if DEBUT not in corps:
            continue
        neuf = retirer_sujets(corps)
        if neuf is None:
            _logger.warning(
                "bf_meeting : traduction %s du courriel laissée telle quelle "
                "(le bloc des sujets n'y est pas exactement une fois, gabarit retouché ?)",
                lang)
            continue
        for avant, apres in INTROS:
            neuf = neuf.replace(avant, apres)
        cr.execute(
            "UPDATE mail_template SET body_html = jsonb_set(body_html, %s, to_jsonb(%s::text)) "
            "WHERE id = %s", [[lang], neuf, template.id])
        _logger.info("bf_meeting : sujets retirés du corps du courriel en %s "
                     "(%d -> %d caractères)", lang, len(corps), len(neuf))
    template.invalidate_recordset(["body_html"])

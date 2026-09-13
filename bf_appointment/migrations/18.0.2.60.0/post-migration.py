# -*- coding: utf-8 -*-
"""Poser l'appel au bloc de liens dans les gabarits de rendez-vous déjà en base.

🔴 Les gabarits de ce module sont chargés sous `noupdate="1"`. C'est
volontaire — ils sont retouchés à la main sur certains locataires — mais ça
veut dire qu'un `-u bf_appointment` ne les rafraîchit JAMAIS : le XML de
18.0.2.60.0 ne servira qu'aux installations neuves, et les locataires
existants garderaient un courriel de confirmation sans le bloc de liens.
C'est cette migration qui le pose chez eux.

⚠️ `mail.template.body_html` est traduit, donc stocké en `jsonb` : une
écriture par l'ORM ne touche que la langue courante et laisserait les autres
sans le bloc. On réécrit donc CHAQUE clé de langue, en SQL.

⚠️ Aucune insertion à l'aveugle. Quand aucune ancre ne se trouve exactement
une fois dans une valeur — parce que le gabarit a été réécrit sur ce
locataire — on journalise et on passe : un bloc posé au mauvais endroit dans
un courriel qui part à des clients coûte plus cher qu'un bloc absent.
"""

import json
import logging

_logger = logging.getLogger(__name__)

APPEL = '<t t-out="object.bf_extra_cta_html()"/>'

# Appels d'une rédaction antérieure, à normaliser vers APPEL plutôt qu'à
# doubler. 🔴 Le nom a d'abord été écrit avec un underscore, et le QWeb d'un
# `mail.template` refuse d'appeler une méthode privée : il rend une erreur au
# lieu du courriel. Aucun locataire n'a reçu cette rédaction-là, mais les
# gabarits sont `noupdate="1"` — ce que la base porte, seule une migration
# peut le corriger, et celle-ci est la seule qui passera jamais par ici.
ANCIENS = ['<t t-out="object._bf_extra_cta_html()"/>']

# Par gabarit, les ancres candidates dans l'ordre de préférence. La première
# qui apparaît exactement une fois gagne ; le bloc se pose juste avant elle.
GABARITS = {
    "mail_template_appointment_confirmation": ['<t t-if="object.access_token">'],
    "mail_template_reminder_2d": ['<t t-if="object.access_token">'],
    "mail_template_reminder_1d": ['<t t-if="object.access_token">'],
    "mail_template_reminder_2h": ['<t t-if="object.access_token">'],
    # Le rappel d'une heure ne porte aucun bouton : on s'ancre sur la
    # fermeture de son tableau d'informations.
    "mail_template_reminder_1h": ["</tbody></table>\n                    </td></tr>",
                                  "</tbody></table>"],
}


def migrate(cr, version):
    if not version:
        return
    for xmlid, ancres in GABARITS.items():
        cr.execute(
            "SELECT res_id FROM ir_model_data "
            "WHERE module = 'bf_appointment' AND name = %s AND model = 'mail.template'",
            (xmlid,),
        )
        ligne = cr.fetchone()
        if not ligne:
            continue
        tid = ligne[0]
        cr.execute("SELECT body_html FROM mail_template WHERE id = %s", (tid,))
        brut = cr.fetchone()
        if not brut or not brut[0]:
            continue
        valeur = brut[0]
        # jsonb traduit : {'en_US': '<...>', 'fr_CA': '<...>'} ; une colonne
        # non traduite rendrait une simple chaîne.
        if isinstance(valeur, str):
            try:
                valeur = json.loads(valeur)
            except ValueError:
                valeur = {"en_US": valeur}
        if not isinstance(valeur, dict):
            continue
        neuf = {}
        touche = 0
        for lang, corps in valeur.items():
            if not corps:
                neuf[lang] = corps
                continue
            # 1. Une rédaction antérieure se CORRIGE, elle ne se double pas.
            corrige = corps
            for ancien in ANCIENS:
                corrige = corrige.replace(ancien, APPEL)
            if corrige != corps:
                neuf[lang] = corrige
                touche += 1
                continue
            # 2. Déjà à jour : rien à faire.
            if APPEL in corps:
                neuf[lang] = corps
                continue
            # 3. Absent : le poser à l'ancre.
            pose = None
            for ancre in ancres:
                if corps.count(ancre) == 1:
                    pose = corps.replace(ancre, APPEL + "\n" + ancre, 1)
                    break
            if pose is None:
                _logger.warning(
                    "bf_appointment 2.60.0 : aucune ancre unique dans %s (%s), "
                    "bloc de liens NON posé — à poser à la main si ce locataire "
                    "installe un satellite.", xmlid, lang)
                neuf[lang] = corps
                continue
            neuf[lang] = pose
            touche += 1
        if touche:
            cr.execute(
                "UPDATE mail_template SET body_html = %s WHERE id = %s",
                (json.dumps(neuf), tid),
            )
            _logger.info("bf_appointment 2.60.0 : bloc de liens posé dans %s "
                         "(%d langue(s))", xmlid, touche)

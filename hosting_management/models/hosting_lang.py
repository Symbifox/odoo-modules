# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Langue du lecteur pour les textes écrits sans personne derrière.

Un travail planifié, un webhook, une route publique : `_()` y prendrait la
langue d'OdooBot ou de la requête, et un champ traduit se lirait dans la
source (anglaise). Les alertes ntfy, les notes au fil des
opérations et le journal d'audit sont lus par l'équipe d'hébergement : la
langue de la société, sinon l'anglais. Un courriel à un contact suit d'abord
la langue du contact.
"""


def reader_lang(env, partner=None, company=None):
    """Langue installée du contact, sinon de la société, sinon en_US.

    `company` : la société de l'enregistrement quand il en porte une (sinon
    celle de l'environnement).
    """
    installed = {code for code, _name in env["res.lang"].get_installed()}
    # sudo : une route publique (usager public) lit aussi la langue de la société.
    candidates = []
    if partner:
        partner = partner.sudo()
        candidates.append(partner.lang)
        if partner.commercial_partner_id:
            candidates.append(partner.commercial_partner_id.lang)
    candidates.append((company or env.company).sudo().partner_id.lang)
    for lang in candidates:
        if lang in installed:
            return lang
    return "en_US"


def reader_env(env, partner=None, company=None):
    """`env` épinglé à la langue du lecteur (voir `reader_lang`)."""
    return env(context=dict(env.context, lang=reader_lang(env, partner, company)))


def user_env(env, user):
    """`env` épinglé à la langue d'un usager (activité, note qui lui est adressée)."""
    installed = {code for code, _name in env["res.lang"].get_installed()}
    lang = user.lang if user and user.lang in installed else reader_lang(env)
    return env(context=dict(env.context, lang=lang))

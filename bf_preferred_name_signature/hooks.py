"""Reprise, à l'installation, des pronoms saisis pour la signature, et
retour, à la désinstallation, des pronoms de la fiche vers la signature.

La colonne `res_users.bf_signature_pronouns` est lue en SQL : son champ est
désormais lié à la fiche, l'ORM lirait la fiche. Les pronoms passent sur la
fiche quand elle n'en a pas ; si elle en a d'autres, la fiche l'emporte (c'est
la saisie de la personne) et l'usager est signalé au journal par son numéro,
jamais par la valeur. Une valeur que la fiche refuse (`bf_preferred_name`
n'admet que des lettres et quelques signes) n'est pas reprise : signalée de
même, sans faire échouer l'installation.

Tant que le pont est là, plus rien n'écrit la colonne. Désinstallé, le champ de `bf_multi_company_email`
redevient stocké et relit la colonne, figée à l'installation, et la signature
reprendrait des pronoms que la personne a changés depuis. D'où le crochet de
désinstallation, qui y recopie ceux de la fiche, sans effacer une valeur
que la reprise avait laissée.
"""

import logging

from odoo.addons.bf_preferred_name.models.res_partner import pronouns_are_valid

_logger = logging.getLogger(__name__)


def _signature_column_exists(env):
    env.cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'res_users' AND column_name = 'bf_signature_pronouns'
    """)
    return bool(env.cr.fetchone())


def post_init_hook(env):
    if not _signature_column_exists(env):
        return
    env.cr.execute("""
        SELECT id, partner_id, bf_signature_pronouns FROM res_users
        WHERE coalesce(trim(bf_signature_pronouns), '') <> ''
    """)
    rows = env.cr.fetchall()
    Partner = env["res.partner"].sudo()
    users = env["res.users"]
    for user_id, partner_id, pronouns in rows:
        partner = Partner.browse(partner_id)
        pronouns = pronouns.strip()
        if not pronouns_are_valid(pronouns):
            _logger.warning(
                "bf_preferred_name_signature : usager %s, pronoms de signature "
                "refusés par la fiche (caractères) ; non repris", user_id)
        elif not partner.bf_pronouns:
            partner.bf_pronouns = pronouns
            users |= env["res.users"].browse(user_id)
        elif partner.bf_pronouns.strip() != pronouns:
            _logger.warning(
                "bf_preferred_name_signature : usager %s, pronoms de signature "
                "différents de ceux de la fiche ; la fiche est gardée", user_id)
    _logger.info("bf_preferred_name_signature : %s pronoms repris sur la fiche", len(users))


def uninstall_hook(env):
    """Recopie les pronoms de chaque fiche dans la colonne de son usager.

    En SQL, comme la reprise : l'ORM écrirait la fiche par le champ lié. Une
    fiche sans pronoms vide la colonne (la personne les a retirés). La
    signature stockée, elle, suit déjà les pronoms de la fiche (crochet de
    `res.partner.write`) : rien à refaire.

    Sauf une colonne que la reprise a laissée : une fiche vide à côté d'une valeur que la fiche refuse ne dit
    pas que la personne a retiré ses pronoms, seulement qu'ils n'ont jamais pu
    y entrer. Ces lignes ne sont pas touchées.
    """
    if not _signature_column_exists(env):
        return
    env.flush_all()
    env.cr.execute("""
        SELECT u.id, u.bf_signature_pronouns FROM res_users u
        JOIN res_partner p ON p.id = u.partner_id
        WHERE coalesce(p.bf_pronouns, '') = ''
          AND coalesce(trim(u.bf_signature_pronouns), '') <> ''
    """)
    left_alone = [user_id for user_id, pronouns in env.cr.fetchall()
                  if not pronouns_are_valid(pronouns.strip())]
    env.cr.execute("""
        UPDATE res_users u SET bf_signature_pronouns = p.bf_pronouns
        FROM res_partner p
        WHERE p.id = u.partner_id
          AND u.bf_signature_pronouns IS DISTINCT FROM p.bf_pronouns
          AND NOT u.id = ANY(%s::int[])
    """, (left_alone,))
    _logger.info("bf_preferred_name_signature : %s pronoms rendus à la signature", env.cr.rowcount)

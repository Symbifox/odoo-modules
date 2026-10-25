"""Fox Quest 18.0.3.0.0 : comptes de service hors jeu, constance refaite en jours ouvrables.

1. Les comptes de service sortent du jeu : un robot d'intégration pouvait
   finir en tête du classement. ``__system__`` et les comptes de service connus
   sont cochés « Hors Fox Quest » ; leur historique reste. Les autres comptes de
   service se cochent à la main sur la fiche de l'usager.

2. L'historique est réputé né d'un geste (`from_request`) : la constance ne
   compte désormais que les jours portant une transaction née dans une requête,
   pas d'un cron, et on ne sait pas trier l'historique après coup.

3. La constance se recalcule depuis l'historique des transactions, en jours
   ouvrables de chaque personne. L'ancien compteur comptait des jours de
   calendrier et cassait sur toute fin de semaine prise. Les badges déjà gagnés restent.

4. Les journées « à jour » ne se lisent plus que par la personne : la règle qui
   les ouvrait aux gestionnaires (premier jet, `noupdate`) est retirée, et celle
   du journal d'XP des gestionnaires écarte la source « À jour » (ils les
   lisaient encore par leurs transactions).

Pas de rattrapage du bonus « tâche avant échéance » jamais versé : choix
délibéré, l'historique n'est pas réécrit.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

SERVICE_LOGIN_PREFIXES = ('meeting-api@', 'gen@')


def migrate(cr, version):
    if not version:
        return
    # En SQL : une écriture ORM sur res.users réveille les synchronisations des
    # autres modules (annuaires, SSO) pour un simple drapeau de jeu.
    cr.execute(
        "UPDATE res_users SET bf_gamification_excluded = TRUE"
        " WHERE id = %s OR login LIKE ANY(%s) RETURNING login",
        (SUPERUSER_ID, [prefix + '%' for prefix in SERVICE_LOGIN_PREFIXES]))
    logins = [row[0] for row in cr.fetchall()]
    _logger.info("Fox Quest 3.0 : %d compte(s) de service hors jeu : %s",
                 len(logins), ', '.join(logins))
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['res.users'].invalidate_model(['bf_gamification_excluded'])

    cr.execute("UPDATE bf_gamification_xp_transaction SET from_request = TRUE"
               " WHERE from_request IS NOT TRUE")
    _logger.info("Fox Quest 3.0 : %d transaction(s) d'historique réputées nées d'un geste",
                 cr.rowcount)
    env['bf.gamification.xp.transaction'].invalidate_model(['from_request'])

    rule = env.ref('bf_gamification.rule_caught_up_manager', raise_if_not_found=False)
    if rule:
        rule.unlink()
    # Règle `noupdate` : la donnée du module ne la change pas sur une base existante.
    rule = env.ref('bf_gamification.rule_xp_transaction_manager', raise_if_not_found=False)
    if rule:
        rule.write({'name': "XP Transaction: gestionnaire voit tout (sauf « à jour »)",
                    'domain_force': "[('source', '!=', 'caught_up')]"})

    Profile = env['bf.gamification.profile']
    profiles = Profile.search(Profile._bf_player_domain())
    before = {p.id: (p.current_streak, p.longest_streak) for p in profiles}
    profiles._bf_recompute_constance()
    for profile in profiles:
        _logger.info("Fox Quest 3.0 : constance de %s, %s -> %s (courante, meilleure)",
                     profile.user_id.login, before[profile.id],
                     (profile.current_streak, profile.longest_streak))

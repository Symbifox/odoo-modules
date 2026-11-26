"""18.0.11.68.3 : vider les quatorze tables d'assistants.

🔴 Les règles de propriétaire ferment la LECTURE des formulaires d'autrui ;
elles n'effacent pas ce qui dort déjà dans ces tables. Là où ce cron est
désactivé, des lignes vieilles de plusieurs semaines restent en table, parce
que le seul appelant de `_transient_vacuum` dans Odoo 18 est le cron
`base.autovacuum_job`, qui peut être désactivé sur une instance. Sans ce passage-ci,
elles survivraient à la pose, et à toutes les sauvegardes qui suivent.

Ce qu'on jette : des brouillons, par définition — une ligne d'assistant ne
vaut que le temps d'un formulaire ouvert. Et une montée redémarre Odoo, donc
elle tue déjà tout formulaire en cours : le coût réel est nul. Décision
assumée.

⚠️ Les QUATORZE, pas seulement celle des comptes : c'est
`bf.email.account.setup` qui porte un mot de passe en clair, mais
`bf.email.browser` porte le corps complet d'un courriel, et les douze autres
disent quels courriels partent où et lesquels sont signalés à qui.

⚠️ En `pre` et non en `post` : rien de ce que fait ce script n'a besoin du code
neuf, et vider avant la mise à jour du schéma évite qu'une colonne ajoutée plus
tard, obligatoire, bute sur de vieilles lignes.

⚠️ `DELETE` et non `TRUNCATE` : les seules tables qui pointent vers les
quatorze sont leurs lignes filles et leurs tables de liaison, toutes en
`ON DELETE CASCADE` (mesuré sur une base d'essai). Un `TRUNCATE ... CASCADE`
ferait la même chose en emportant au passage tout ce qu'un module futur
viendrait y accrocher, sans le dire.
"""
import logging

_logger = logging.getLogger(__name__)

# ⚠️ `warning` et non `info`, exprès : une instance peut tourner avec
# `log_level = warn` dans son `odoo.conf`, donc un `info` ne laisse AUCUNE
# trace de la pose. Pour un correctif dont tout l'argument est « des mots de
# passe dormaient depuis des semaines », le compte de ce qui a été effacé, et où,
# doit être au journal de la montée. Constaté à l'essai : les
# lignes n'étaient lisibles qu'en forçant `--log-level=info` à la main.

# Les quatorze, les lignes filles AVANT leurs parents. L'ordre du fichier de
# sécurité, pour qu'un quinzième assistant se voie des deux côtés.
TABLES = (
    "bf_email_account_setup",
    "bf_email_browser_line",
    "bf_email_browser",
    "bf_email_reroute",
    "bf_email_guess_route_line",
    "bf_email_guess_route",
    "bf_email_report_wizard",
    "bf_email_snooze",
    "bf_email_absence_preview",
    "bf_email_rule_quick_line",
    "bf_email_rule_quick",
    "bf_email_cleanup",
    "bf_email_initial_sync",
    "bf_email_imap_backfill",
)


def migrate(cr, version):
    if not version:
        return
    total = 0
    for table in TABLES:
        cr.execute("SELECT to_regclass(%s)", (table,))
        if not cr.fetchone()[0]:
            # Un locataire monté depuis une version qui n'avait pas encore cet
            # assistant : rien à vider.
            continue
        cr.execute('DELETE FROM "%s"' % table)
        if cr.rowcount:
            _logger.warning(
                "bf_email_management 18.0.11.68.3 pre-migrate : %s ligne(s) "
                "d'assistant effacée(s) dans %s", cr.rowcount, table)
            total += cr.rowcount
    _logger.warning(
        "bf_email_management 18.0.11.68.3 pre-migrate : %s ligne(s) "
        "d'assistant effacée(s) sur %s tables", total, len(TABLES))

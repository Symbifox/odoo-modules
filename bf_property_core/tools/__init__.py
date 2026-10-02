"""Nombres cités dans une phrase lue.

Une voix pondérée se tient à quatre décimales (une quote-part de déclaration
en porte souvent trois), et le champ garde cette précision à l'écran. Dans une
PHRASE, les zéros de remplissage ne disent rien et nuisent à la lecture :
« 585.0000 voix pour sur 502.5000 requises » se lit « 585 voix pour sur
502,5 requises ». La valeur n'est jamais arrondie au-delà de ce que le champ
porte : seuls les zéros de fin s'en vont.

⚠️ Les séparateurs viennent de la langue du LECTEUR (`formatLang` lit
`env.lang`), jamais d'un `%.4f` qui imprime le point anglais en français.
"""
import logging
from datetime import datetime, time, timedelta

from odoo import fields
from odoo.tools.misc import formatLang, get_lang

_logger = logging.getLogger(__name__)


def format_decimal(env, value, digits=4):
    """Rend `value` à `digits` décimales au plus, sans zéros de fin."""
    text = formatLang(env, value or 0.0, digits=digits)
    # La même langue que `formatLang`, sinon on retire un séparateur qu'il n'a
    # pas imprimé.
    point = get_lang(env).decimal_point
    if point and point in text:
        text = text.rstrip("0").rstrip(point)
    return text


# ── L'heure des crons de la suite ────────────────────────────────────────
# Un cron n'a pas de fuseau : sa « date du jour » est celle de l'UTC, et il
# tourne chaque jour à l'heure de sa création. Posé un soir au Québec, il voyait
# déjà le lendemain : un loyer dû le jour même passait en retard avant minuit.
# À 05 h 30 UTC, la date est la même en UTC et au Québec, été (01 h 30) comme
# hiver (00 h 30). Les XML des crons portent cette heure pour une installation
# neuve ; ils sont en `noupdate`, d'où cette aide pour les bases existantes.
CRON_TIME_UTC = time(5, 30)


def anchor_crons_at_dawn(env, xmlids):
    """Recale des crons déjà en base sur le prochain 05 h 30 UTC.

    Pour les migrations : un `-u` ne relit pas le `nextcall` d'un fichier en
    `noupdate`. Un xmlid absent (module d'une autre version) est sauté.
    """
    now = fields.Datetime.now()
    nextcall = datetime.combine(now.date(), CRON_TIME_UTC)
    if nextcall <= now:
        nextcall += timedelta(days=1)
    crons = env["ir.cron"]
    for xmlid in xmlids:
        cron = env.ref(xmlid, raise_if_not_found=False)
        if cron and cron._name == "ir.cron":
            crons |= cron
        else:
            _logger.warning("Cron %s introuvable : son heure n'est pas recalée.", xmlid)
    if crons:
        # En SQL : `write` sur ir.cron prend un verrou NOWAIT, et un cron qui
        # tourne pendant la montée la ferait avorter pour une simple heure.
        # ⚠️ Vider d'abord ce que l'ORM tient en attente : sinon le vidage que
        # fait l'oubli, après, réécrirait l'ancienne heure par-dessus.
        crons.flush_recordset(["nextcall"])
        env.cr.execute(
            "UPDATE ir_cron SET nextcall = %s WHERE id IN %s",
            (nextcall, tuple(crons.ids)),
        )
        crons.invalidate_recordset(["nextcall"])
    return crons

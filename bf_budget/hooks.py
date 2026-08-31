"""Neutralisation réversible des menus budgétaires livrés par des modules tiers.

Odoo 18 Communauté ne livre aucun module de budget. Les greffons comptables tiers
en embarquent chacun le leur, et plusieurs peuvent cohabiter sur la même base :
la Comptabilité finit alors avec plusieurs entrées « Budgets » qui pointent vers
des modèles sans rapport, généralement vides.

Ce module ne peut pas déclarer une dépendance vers ces greffons — ils sont absents
de la plupart des bases. Il cherche donc leurs menus par module d'origine, et
désactive ceux qu'il trouve. Rien n'est supprimé : la désinstallation les rallume,
et un réglage permet de les rallumer à tout moment.
"""

import logging

_logger = logging.getLogger(__name__)

# Modules tiers connus pour livrer un menu budgétaire concurrent. La recherche se
# fait par module d'origine, jamais par une liste figée d'identifiants XML : un
# greffon qui ajoute un menu à sa prochaine version resterait couvert.
THIRD_PARTY_BUDGET_MODULES = [
    "base_account_budget",
    "om_account_budget",
    "account_budget_oca",
]

PARAM_HIDDEN_MENUS = "bf_budget.hidden_menu_ids"
PARAM_HIDE_ENABLED = "bf_budget.hide_thirdparty_menus"


def _third_party_menus(env):
    """Les menus actifs livrés par un greffon budgétaire tiers."""
    data = env["ir.model.data"].sudo().search(
        [
            ("module", "in", THIRD_PARTY_BUDGET_MODULES),
            ("model", "=", "ir.ui.menu"),
        ]
    )
    if not data:
        return env["ir.ui.menu"].sudo()
    return env["ir.ui.menu"].sudo().browse(data.mapped("res_id")).exists()


def _stored_hidden_ids(env):
    param = env["ir.config_parameter"].sudo().get_param(PARAM_HIDDEN_MENUS) or ""
    return [int(i) for i in param.split(",") if i.strip().isdigit()]


def hide_third_party_menus(env):
    """Désactive les menus tiers et retient lesquels, pour pouvoir les rallumer.

    ⚠️ La mémoire s'ACCUMULE. Un second appel ne trouve plus rien d'actif : s'il
    écrasait le paramètre, la désinstallation ne rallumerait plus rien, et les
    menus du greffon tiers resteraient éteints pour toujours.
    """
    menus = _third_party_menus(env).filtered("active")
    if not menus:
        return menus
    menus.write({"active": False})
    known = set(_stored_hidden_ids(env)) | set(menus.ids)
    env["ir.config_parameter"].sudo().set_param(
        PARAM_HIDDEN_MENUS, ",".join(str(i) for i in sorted(known))
    )
    _logger.info(
        "bf_budget : %s menu(s) budgétaire(s) tiers désactivé(s) : %s",
        len(menus),
        ", ".join(menus.mapped("complete_name")),
    )
    return menus


def restore_third_party_menus(env):
    """Rallume uniquement les menus que ce module avait éteints."""
    ids = _stored_hidden_ids(env)
    if not ids:
        return env["ir.ui.menu"].sudo()
    menus = env["ir.ui.menu"].sudo().browse(ids).exists()
    if menus:
        menus.write({"active": True})
        _logger.info("bf_budget : %s menu(s) tiers rallumé(s)", len(menus))
    env["ir.config_parameter"].sudo().set_param(PARAM_HIDDEN_MENUS, "")
    return menus


def post_init_hook(env):
    env["ir.config_parameter"].sudo().set_param(PARAM_HIDE_ENABLED, "True")
    hide_third_party_menus(env)


def uninstall_hook(env):
    restore_third_party_menus(env)

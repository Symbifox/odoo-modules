# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Tuiles optionnelles du bureau par défaut.

⚠️ Pourquoi la tuile « Courriels » n'est pas dans le XML de données.

Ce module est publié sous LGPL-3, `bf_email_management` est sous BUSL-1.1. Une
tuile de bureau qui pointe vers l'action de ce dernier par ``ref=`` imposait une
dépendance de manifeste, donc la licence permissive promettait quelque chose
qu'elle ne pouvait pas tenir : impossible d'installer le bureau sans accepter
des conditions restrictives sur un autre module.

Le bureau se monte donc sans elle, et la tuile s'ajoute à l'installation quand
l'action existe. Un bureau se réagence de toute façon depuis l'interface : ce
n'est qu'une valeur de départ.

⚠️ Créée SANS ``ir.model.data``, volontairement : un xmlid de ce module en
ferait un orphelin au prochain ``-u``, supprimé en silence par le nettoyage de
fin de mise à jour. L'idempotence passe donc par une recherche.
"""

import logging

_logger = logging.getLogger(__name__)

ACTION_OPTIONNELLE = "bf_email_management.bf_email_action"
EMPLACEMENT = "bottom_full"
BUREAU = "bf_bureau.default_desk_admin"


def _ensure_optional_panes(env):
    """Ajoute la tuile courriel au bureau par défaut si l'action est là."""
    action = env.ref(ACTION_OPTIONNELLE, raise_if_not_found=False)
    if not action:
        return False
    desk = env.ref(BUREAU, raise_if_not_found=False)
    if not desk:
        return False
    Pane = env["bf.bureau.pane"].sudo()
    if Pane.search_count([("desk_id", "=", desk.id), ("slot", "=", EMPLACEMENT)]):
        return False
    Pane.create({
        "desk_id": desk.id,
        "slot": EMPLACEMENT,
        "action_id": action.id,
        "view_type": "kanban",
    })
    _logger.info("bf_bureau: tuile courriel ajoutée au bureau par défaut")
    return True


def post_init_hook(env):
    _ensure_optional_panes(env)

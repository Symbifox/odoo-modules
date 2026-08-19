# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Liaisons optionnelles vers les modèles apportés par d'autres modules.

⚠️ Pourquoi ces deux actions ne sont pas dans le XML de données.

Ce module est publié sous LGPL-3 : « utilisez-le, modifiez-le, redistribuez-le,
bâtissez un produit dessus ». Or ses deux actions « Réordonner ce chatter par
date » pour les rencontres visaient ``bf_meeting.model_meeting_record`` et
``…_agenda``, ce qui forçait une dépendance de manifeste vers ``bf_meeting``,
lui-même sous BUSL-1.1. La licence permissive promettait donc quelque chose
qu'elle ne pouvait pas tenir : on ne pouvait pas installer ce module sans
accepter des conditions restrictives sur un autre.

Les deux actions sont donc créées ici, seulement si le modèle existe. Le module
s'installe seul, et rend exactement le même service dès que ``bf_meeting`` est
présent.

⚠️ Les enregistrements sont créés SANS ``ir.model.data``, volontairement. Un
xmlid préfixé par ce module les ferait passer pour des orphelins au prochain
``-u`` — le nettoyage de fin de mise à jour supprime tout xmlid du module qui
n'est plus produit par ses fichiers de données — et ils disparaîtraient
silencieusement. L'idempotence repose donc sur une recherche, pas sur un xmlid.
"""

import logging

_logger = logging.getLogger(__name__)

# Modèles servis quand leur module est là. Le libellé doit rester identique à
# celui des actions déclarées en XML : c'est le même geste pour l'usager.
LIAISONS_OPTIONNELLES = ("meeting.record", "meeting.agenda")
LIBELLE = "Réordonner ce chatter par date"
CODE = "action = env['mail.message'].action_backfill_chatter_dates()"


def _ensure_optional_bindings(env):
    """Crée les actions contextuelles pour les modèles optionnels présents."""
    Action = env["ir.actions.server"].sudo()
    modele_message = env["ir.model"]._get_id("mail.message")
    poses = []
    for nom_modele in LIAISONS_OPTIONNELLES:
        if nom_modele not in env:
            continue
        cible = env["ir.model"]._get_id(nom_modele)
        if not cible:
            continue
        deja = Action.search([
            ("binding_model_id", "=", cible),
            ("model_id", "=", modele_message),
            ("state", "=", "code"),
        ], limit=1)
        if deja:
            continue
        Action.create({
            "name": LIBELLE,
            "model_id": modele_message,
            "binding_model_id": cible,
            "binding_view_types": "form",
            "state": "code",
            "code": CODE,
        })
        poses.append(nom_modele)
    if poses:
        _logger.info(
            "bf_chatter_chronological: liaison chatter posée sur %s",
            ", ".join(poses),
        )
    return poses


def post_init_hook(env):
    _ensure_optional_bindings(env)

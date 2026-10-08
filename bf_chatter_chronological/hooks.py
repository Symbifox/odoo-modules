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

from odoo.tools.translate import LazyTranslate

_logger = logging.getLogger(__name__)
_lt = LazyTranslate(__name__)

# Modèles servis quand leur module est là. Le libellé doit rester identique à
# celui des actions déclarées en XML : c'est le même geste pour l'usager.
LIAISONS_OPTIONNELLES = ("meeting.record", "meeting.agenda")
LIBELLE = _lt("Reorder this chatter by date")
# Le libellé livré avant la source anglaise (18.0.4.2.0) : seul un nom encore
# identique à celui-ci est basculé par la migration, jamais un nom retouché.
LIBELLE_FR_LIVRE = "Réordonner ce chatter par date"
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
        action = Action.create({
            "name": LIBELLE._translate("en_US"),
            "model_id": modele_message,
            "binding_model_id": cible,
            "binding_view_types": "form",
            "state": "code",
            "code": CODE,
        })
        ecrire_libelles(env, action)
        poses.append(nom_modele)
    if poses:
        _logger.info(
            "bf_chatter_chronological: liaison chatter posée sur %s",
            ", ".join(poses),
        )
    return poses


def ecrire_libelles(env, actions):
    """Le nom dans chaque langue installée.

    Un crochet passe APRÈS le chargement des catalogues : l'action qu'il crée ne
    reçoit jamais sa traduction du ``.po``. Et ``update_field_translations``, pas
    ``write`` dans la langue : sur une base où en_US est inactif, Odoo recopie
    une écriture dans n'importe quelle langue en en_US, et la source se perdrait.
    """
    traductions = {
        lang: LIBELLE._translate(lang)
        for lang, _nom in env["res.lang"].get_installed() if lang != "en_US"
    }
    for action in actions:  # update_field_translations veut un seul enregistrement
        action.update_field_translations("name", traductions)


def liaisons_posees(env):
    """Les actions créées par ce crochet (sans xmlid, voir plus haut)."""
    return env["ir.actions.server"].sudo().search([
        ("binding_model_id.model", "in", LIAISONS_OPTIONNELLES),
        ("model_id.model", "=", "mail.message"),
        ("state", "=", "code"),
        ("code", "=", CODE),
    ])


def post_init_hook(env):
    _ensure_optional_bindings(env)

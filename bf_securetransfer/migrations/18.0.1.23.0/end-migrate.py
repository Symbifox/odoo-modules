"""18.0.1.23.0, étape `end` : rafraîchir le français des libellés RENOMMÉS.

🔴 **Une mise à jour n'écrase jamais une traduction existante.** Quatre textes
changent de source anglaise dans cette version et gardaient donc leur ancien
français. Mesuré en environnement d'essai APRÈS le `-u` (c'est le seul moment où l'on voit ce
que le chargement du catalogue a laissé) :

| | français resté en base |
|---|---|
| `secure.transfer.burn_after_download` | « Destruction après lecture (Phase 2) » |
| `secure.transfer.brand.notify_on_download` | « Notifier au téléchargement (Phase 2) » |
| `secure.transfer.brand.allow_burn` | « Destruction après lecture (Phase 2) » |
| action « Visiteurs » (`name`) | « Visiteurs (audiences ouvertes) » |

Les trois premiers sont le défaut que cette version corrige : les deux chemins
sont implantés et couverts par des essais, et l'étiquette « (Phase 2) » a fait
croire à un client que la fonction n'existait pas. La laisser en français
aurait annulé la correction pour tout usager de `fr_CA` — c'est-à-dire pour
presque tout le monde.

⚠️ Les VUES, elles, se rafraîchissent d'elles-mêmes : mesuré, le bouton
« Renvoyer le lien à tout le monde » et l'aide de l'action sont bien passés en
français au `-u`. Seul ce qui est ici en avait besoin — ne pas élargir.

Chaque valeur n'est remplacée QUE si elle porte encore le texte périmé : une
retouche faite par un opérateur est laissée intacte.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

# (modèle, champ) -> [(périmé, neuf)]
LIBELLES = {
    ("secure.transfer", "burn_after_download"):
        ("Destruction après lecture (Phase 2)", "Détruire après lecture"),
    ("secure.transfer.brand", "notify_on_download"):
        ("Notifier au téléchargement (Phase 2)", "Notifier au téléchargement"),
    ("secure.transfer.brand", "allow_burn"):
        ("Destruction après lecture (Phase 2)",
         "Offrir la destruction après lecture"),
}
ACTION_XMLID = "bf_securetransfer.action_secure_transfer_audience"
ACTION_PERIME = "Visiteurs (audiences ouvertes)"
ACTION_NEUF = "Personnes atteintes"


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})

    # 🔴 Sans cette garde, la montée MEURT sur une base sans français.
    # `with_context(lang="fr_CA")` lève `UserError: Invalid language code:
    # fr_CA` dès qu'Odoo 18 touche un champ traduit (api.py, propriété `lang`),
    # et l'étape `end` n'a pas de filet : la registry échoue, le `-u` sort en
    # 255 et la base reste à la version précédente avec le code neuf déjà sur
    # le disque. Vécu en production sur une base qui n'a que `en_US` ; les
    # bases qui ont `fr_CA` ne le montrent pas, d'où un défaut invisible au
    # environnement d'essai.
    # Rien à rafraîchir sur une base sans français : il n'y a pas de
    # traduction périmée à corriger.
    if not env["res.lang"].search_count([("code", "=", "fr_CA"),
                                         ("active", "=", True)]):
        _logger.info(
            "bf_securetransfer 1.23.0 : fr_CA n'est pas une langue active, "
            "aucun libellé français à rafraîchir")
        return

    touches = 0

    for (model, name), (perime, neuf) in LIBELLES.items():
        champ = env["ir.model.fields"].search(
            [("model", "=", model), ("name", "=", name)], limit=1)
        if not champ:
            continue
        actuel = champ.with_context(lang="fr_CA").field_description
        if actuel != perime:
            continue
        champ.update_field_translations("field_description", {"fr_CA": neuf})
        touches += 1

    action = env.ref(ACTION_XMLID, raise_if_not_found=False)
    if action and action.with_context(lang="fr_CA").name == ACTION_PERIME:
        action.update_field_translations("name", {"fr_CA": ACTION_NEUF})
        touches += 1

    _logger.info(
        "bf_securetransfer 1.23.0 : %s libellé(s) français périmé(s) "
        "rafraîchi(s)", touches)

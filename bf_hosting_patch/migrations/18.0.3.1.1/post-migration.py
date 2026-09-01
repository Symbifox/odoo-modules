# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Le cron des machines muettes visait encore `hosting.endpoint`.

La 18.0.2.0.0 a déplacé `_cron_refresh_patch_state` vers `bf.patch.system` —
le système est devenu le porteur de l'état — mais le cron, lui, est resté sur
l'ancien modèle. Odoo évalue le code du cron avec
`model = env[cron.model_id.model]` : depuis la migration, il lève
`AttributeError("'hosting.endpoint' object has no attribute
'_cron_refresh_patch_state'")` toutes les 4 h, et l'état « muet » n'est plus
jamais rejoué. La panne est exactement celle que le module existe pour
éliminer : un poste qui cesse de parler garde son dernier état vert.

⚠️ Corriger le fichier XML ne suffit PAS. Il est en `noupdate="1"`, et
`convert.py` teste l'attribut du FICHIER avant même de lire la base : le
rechargement sort sans rien écrire. L'enregistrement déjà posé se répare ici,
nommément — voir [[reference_noupdate_data_refresh_needs_convert_file]].
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    cron = env.ref(
        "bf_hosting_patch.cron_refresh_patch_state", raise_if_not_found=False
    )
    if not cron:
        _logger.error(
            "bf_hosting_patch : cron_refresh_patch_state introuvable, "
            "l'état « muet » n'est rejoué par personne."
        )
        return

    cron.write({"model_id": env["ir.model"]._get_id("bf.patch.system")})

    # Odoo désactive un cron après 5 échecs espacés de 7 jours. Le compteur a
    # déjà commencé à monter ; le laisser en l'état ferait partir la réparation
    # avec une dette, et une seule mauvaise passe suffirait à éteindre le cron.
    cron.write({
        "active": True, "failure_count": 0, "first_failure_date": False,
    })
    _logger.info(
        "bf_hosting_patch : cron « machines muettes » repointé sur "
        "bf.patch.system, compteur d'échecs remis à zéro."
    )

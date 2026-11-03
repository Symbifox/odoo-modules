# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Le jeton propre aux demandes de sauvegarde.

🔴 POURQUOI UN SECOND JETON. Celui des rapports (`hosting.backup_api_token`) sert
la télémétrie : l'hôte raconte ce qu'il a fait. Les deux routes de demandes, elles,
pilotent une machine à états. Avec un seul jeton, un porteur pouvait prendre les
demandes d'un gestionnaire, ou les fermer en « terminée » en citant le numéro d'une
vraie exécution nocturne, et donc peindre un service en vert sans qu'une sauvegarde
ait tourné. deux jetons.

Le jeton est engendré ici, parce que les routes refusent s'il manque et qu'un
module posé doit marcher sans geste préalable. Il n'est JAMAIS journalisé : on le
lit en base pour le poser sur l'hôte, et nulle part ailleurs.
"""
import logging
import secrets

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

CLE = "hosting.backup_request_token"


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    icp = env["ir.config_parameter"].sudo()
    if (icp.get_param(CLE) or "").strip():
        _logger.info("%s existe déjà, laissé tel quel", CLE)
        return
    icp.set_param(CLE, secrets.token_urlsafe(32))
    # Sans le contenu : un secret dans un journal est un secret public.
    _logger.info(
        "%s engendré. Le poser sur l'hôte, dans le fichier que l'agent des "
        "demandes lit (droits 0600) : sans lui, il refuse de tourner.", CLE
    )

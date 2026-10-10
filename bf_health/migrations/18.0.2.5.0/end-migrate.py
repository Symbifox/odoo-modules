"""bf_health 18.0.2.5.0 : si Gen porte déjà le verrou, les
conversations déjà rattachées à une fiche santé deviennent privées.

Les portées de Healthy Fox naissent avec la 2.5.0. Si Gen 1.36.0 est monté
AVANT elle, il a calculé ces conversations sans portée : on les relit ici.
`end-migrate` : Gen est chargé, quel que soit l'ordre des modules.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    if "claude.chat.session" not in env:
        return
    Session = env["claude.chat.session"]
    if not hasattr(Session, "_bf_gen_rafraichir_portees"):
        return  # Gen sans verrou : sa propre montée vers la 1.36.0 s'en chargera.
    n = Session._bf_gen_rafraichir_portees()
    _logger.info("bf_health 2.5.0 : %s conversation(s) Gen sur une fiche santé rendue(s) privée(s)", n)

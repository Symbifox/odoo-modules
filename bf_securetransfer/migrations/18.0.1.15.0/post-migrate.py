"""Rattrapage : masquer les liens de partage déjà retenus dans le suivi.

Le correctif de 1.15.0 empêche un nouveau transfert de conserver son lien en
clair dans le chatter, mais les courriels déjà envoyés y sont toujours. Cette
passe applique la même règle à l'historique : le jeton est masqué partout où
aucun code destinataire ne garde l'accès au lien.

Idempotente (un corps déjà masqué ne contient plus le jeton) et sans effet sur
les transferts dont le contenu est retenu derrière un code.
"""


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    rewritten = env["secure.transfer"].search([])._redact_chatter_links()
    if rewritten:
        import logging
        logging.getLogger(__name__).info(
            "bf_securetransfer 1.15.0 : %s corps de message masqué(s).",
            rewritten)

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Figer le nom et l'adresse au reçu des adhésions déjà payées.

    Avant cette version, le reçu relisait le contact au moment de délivrer.
    Les adhésions payées reçoivent l'instantané du contact tel qu'il est
    aujourd'hui ; les suivantes le reçoivent au paiement.
    """
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["bf.membership"]._fill_missing_donor_snapshots()

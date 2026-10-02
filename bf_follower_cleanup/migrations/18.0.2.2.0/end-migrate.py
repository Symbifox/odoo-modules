"""Rend aux clients l'abonnement à leurs factures que le cron effaçait.

Odoo abonne le client facturé quand une facture est comptabilisée, et le portail
n'affiche une facture qu'à ses abonnés : jusqu'en 18.0.2.1.0, le cron retirait cet
abonnement dans les cinq minutes. On le repose sur chaque facture et avoir client
comptabilisé, comme Odoo l'aurait laissé. `message_subscribe` n'envoie rien.

`end-migrate` et non `post-migrate` : le module ne dépend que de `mail`, donc
`account` n'est pas encore chargé quand son `post-migrate` s'exécute.
"""
import logging
from collections import defaultdict

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    if "account.move" not in env:
        return
    moves = env["account.move"].with_context(active_test=False).search([
        ("move_type", "in", ("out_invoice", "out_refund")),
        ("state", "=", "posted"),
        ("partner_id", "!=", False),
        # Odoo n'abonne pas un contact archivé : le compter mentirait au journal.
        ("partner_id.active", "=", True),
    ])
    by_partner = defaultdict(lambda: env["account.move"])
    for move in moves:
        partner = move.partner_id
        # Même règle que le cron : la société enregistrée sur la facture, sauf
        # facture émise à un particulier rattaché depuis à une société. Un
        # contact passé à une autre société n'est pas réabonné.
        owner = (
            partner.commercial_partner_id
            if move.commercial_partner_id == partner
            else move.commercial_partner_id
        )
        if partner.commercial_partner_id != owner:
            continue
        if partner not in move.message_partner_ids:
            by_partner[partner] |= move
    count = 0
    for partner, partner_moves in by_partner.items():
        partner_moves.message_subscribe(partner.ids)
        count += len(partner_moves)
    _logger.info(
        "bf_follower_cleanup: customer re-subscribed to %d posted invoice(s)/credit note(s) "
        "across %d partner(s)", count, len(by_partner),
    )

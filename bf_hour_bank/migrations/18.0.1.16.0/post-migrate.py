"""Préremplir le produit et le taux des banques existantes.

Pris sur la dernière ligne de facture validée que chaque banque compte : c'est le
taux réellement facturé au client, que le prix catalogue ne donne pas (chaque client a
souvent son taux négocié). Une
banque sans facture comptée reste vide. Rejouable : un champ déjà rempli est gardé.

Seulement un produit d'heures : listé dans un filtre « inclure », ou vendu à l'heure.
En filtre « toutes les lignes », la dernière ligne peut être un forfait à l'unité
(hébergement, gestion de projet) : son prix n'est pas un taux horaire.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    banks = env['hour.bank.client'].with_context(active_test=False).search([])
    for bank in banks:
        last = bank._bf_last_credit_line()
        if not last:
            continue
        product = last.product_id
        listed = bank.product_filter_mode == 'include' and product in bank.filter_product_ids
        hourly = product.uom_id == env.ref('uom.product_uom_hour', raise_if_not_found=False)
        if not product or not (listed or hourly):
            _logger.info("bf_hour_bank 18.0.1.16.0 : banque %s non préremplie, %s n'est pas un "
                         "produit d'heures (facture %s)", bank.id, product.display_name,
                         last.move_id.name)
            continue
        vals = {}
        if not bank.invoice_product_id and bank._bf_product_credits(product):
            vals['invoice_product_id'] = product.id
        if not bank.invoice_hourly_rate:
            vals['invoice_hourly_rate'] = last.price_unit
        if vals:
            bank.with_context(tracking_disable=True).write(vals)
            _logger.info("bf_hour_bank 18.0.1.16.0 : banque %s préremplie %s (facture %s)",
                         bank.id, vals, last.move_id.name)

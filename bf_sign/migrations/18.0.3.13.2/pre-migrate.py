"""Retire the phantom ``libresign_aes`` value of ``signature_method``.

The selection offered « LibreSign — signature avancée (AES) » and the form let
the user pick it, but nothing implemented it: the request went through the
ordinary SES pipeline either way. v18.0.3.13.2 drops the value; any row still
carrying it must be coerced, otherwise the ORM reads a value absent from the
selection (blank in the form, ``ValueError`` on some writes).

Coercing to ``native_ses`` does not downgrade anything: the record already WAS
a simple electronic signature — only the label claimed otherwise.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE bf_sign_request
           SET signature_method = 'native_ses'
         WHERE signature_method = 'libresign_aes'
    """)
    if cr.rowcount:
        _logger.warning(
            "bf_sign: %s demande(s) portaient signature_method='libresign_aes' "
            "(option jamais implémentée) et ont été ramenées à 'native_ses'.",
            cr.rowcount,
        )

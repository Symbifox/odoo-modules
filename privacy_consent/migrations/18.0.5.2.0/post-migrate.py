"""Les jetons publics déjà émis reçoivent une échéance de 90 jours, comptée
depuis la montée, plutôt que depuis leur émission : aucun lien
envoyé ne tombe échu le jour même de la mise à jour."""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE privacy_consent
           SET access_token_expires_at = (now() AT TIME ZONE 'UTC') + INTERVAL '90 days'
         WHERE access_token IS NOT NULL
           AND access_token_expires_at IS NULL
    """)
    _logger.info("privacy_consent : échéance de 90 jours posée sur %s jeton(s) existant(s)", cr.rowcount)

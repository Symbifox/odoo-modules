"""Shared Fernet key lookup for bf_appointment.

Lookup order: BF_APPOINTMENT_FERNET_KEY env var > odoo.conf `bf_appointment_fernet_key`
> ir.config_parameter `bf_appointment.encryption_key` (auto-generated fallback).

Used by both res.config.settings (encrypt) and resource.booking (decrypt)
so the two paths stay symmetric even when the key is sourced from env/conf.
"""

import logging
import os

_logger = logging.getLogger(__name__)


def get_encryption_key(env, auto_generate=True):
    key = os.environ.get("BF_APPOINTMENT_FERNET_KEY")
    if key:
        return key
    from odoo.tools import config
    key = config.get("bf_appointment_fernet_key")
    if key:
        return key
    ICP = env["ir.config_parameter"].sudo()
    key = ICP.get_param("bf_appointment.encryption_key")
    if key:
        return key
    if not auto_generate:
        return None
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    ICP.set_param("bf_appointment.encryption_key", key)
    _logger.warning(
        "bf_appointment: encryption key auto-generated in database. "
        "For better security, set BF_APPOINTMENT_FERNET_KEY env var."
    )
    return key

"""Fernet helpers shared by res.config.settings and bf.llm.provider.

Ported from the bf_claude_chat pattern (env var → odoo.conf key → fail closed),
namespaced to bf_llm. The encryption key is NEVER read from the database; the
encrypted API keys themselves live in ir.config_parameter.
"""
import logging
import os

from odoo import _
from odoo.exceptions import UserError
from odoo.tools import config

_logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    Fernet = None
    InvalidToken = Exception
    _logger.warning(
        "cryptography package not installed. "
        "API key encryption will not be available for bf_llm."
    )

ENV_VAR = "BF_LLM_FERNET_KEY"
CONF_KEY = "bf_llm_fernet_key"


def get_encryption_key():
    """Return the Fernet key from env var or odoo.conf. Never from the DB."""
    if not Fernet:
        return None
    # Priority 1: environment variable
    key = os.environ.get(ENV_VAR)
    if key:
        return key.encode()
    # Priority 2: odoo.conf
    try:
        key = config.get(CONF_KEY)
        if key:
            return key.encode()
    except Exception:
        pass
    _logger.warning(
        "bf_llm: no Fernet key configured. "
        "Set %s env var or %s in odoo.conf.",
        ENV_VAR,
        CONF_KEY,
    )
    return None


def encrypt(value):
    """Encrypt a plaintext value. Raises UserError when no key is available."""
    if not value:
        return ""
    if not Fernet:
        raise UserError(
            _(
                "Le paquet 'cryptography' est requis pour stocker "
                "la cle API de maniere securisee."
            )
        )
    key = get_encryption_key()
    if not key:
        raise UserError(
            _(
                "Cle de chiffrement manquante. Configurez "
                "%(env)s dans l'environnement ou %(conf)s dans odoo.conf.",
                env=ENV_VAR,
                conf=CONF_KEY,
            )
        )
    return Fernet(key).encrypt(value.encode()).decode()


def decrypt(encrypted):
    """Decrypt an encrypted value. Fails closed to "" (never raises)."""
    if not encrypted:
        return ""
    key = get_encryption_key()
    if not key:
        _logger.warning("bf_llm: cannot decrypt API key - no Fernet key")
        return ""
    try:
        return Fernet(key).decrypt(encrypted.encode()).decode()
    except InvalidToken:
        _logger.warning(
            "bf_llm: API key decryption failed "
            "(key rotation or legacy plaintext value)"
        )
        return ""
    except Exception:
        _logger.exception("bf_llm: API key decryption error")
        return ""

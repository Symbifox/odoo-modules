# -*- coding: utf-8 -*-
"""Chiffrement des identifiants de canal.

La clé ne vient JAMAIS de la base : variable d'environnement ou fichier de
configuration, comme pour la passerelle LLM. Une base restaurée ailleurs ne
livre donc aucun secret utilisable.
"""

import logging

_logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover
    Fernet = None
    InvalidToken = Exception

from odoo.tools import config

ENV_VAR = "BF_SOCIAL_FERNET_KEY"
CONF_KEY = "bf_social_fernet_key"


def get_encryption_key():
    """La clé Fernet, depuis l'environnement ou odoo.conf. Jamais la base."""
    if not Fernet:
        return None
    import os

    key = os.environ.get(ENV_VAR) or config.get(CONF_KEY)
    return key.encode() if isinstance(key, str) else key


def encrypt(value):
    """Chiffrer, ou refuser. On ne stocke pas un secret en clair par défaut."""
    if not value:
        return ""
    key = get_encryption_key()
    if not (Fernet and key):
        raise ValueError(
            "bf_editorial_social : aucune clé de chiffrement configurée. "
            "Poser %s dans l'environnement ou %s dans odoo.conf."
            % (ENV_VAR, CONF_KEY)
        )
    return Fernet(key).encrypt(value.encode()).decode()


def decrypt(encrypted):
    """Déchiffrer, ou rendre une chaîne vide (échec fermé)."""
    if not encrypted:
        return ""
    key = get_encryption_key()
    if not (Fernet and key):
        _logger.warning("bf_editorial_social : pas de clé, déchiffrement impossible")
        return ""
    try:
        return Fernet(key).decrypt(encrypted.encode()).decode()
    except InvalidToken:
        _logger.warning("bf_editorial_social : jeton invalide, clé changée ?")
        return ""

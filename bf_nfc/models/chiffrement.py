"""La clé qui chiffre les clés des pastilles signées.

Même ordre de recherche que ``bf_appointment`` : la variable d'environnement
``BF_NFC_FERNET_KEY``, puis ``bf_nfc_fernet_key`` dans ``odoo.conf``, et en
dernier recours une clé tirée au hasard et rangée dans la base.

🔴 **Ce que protège chaque cas, dit franchement.** Rangée hors de la base
(environnement ou ``odoo.conf``), la clé fait qu'une copie de la base ne livre
pas les clés des puces. Rangée DANS la base, elle ne protège que contre la
lecture à l'écran et par l'API : quiconque emporte la base emporte aussi de quoi
déchiffrer. La page Réglages dit lequel des deux est en place, au lieu de
laisser croire que « chiffré » veut dire la même chose partout.
"""
import logging
import os

from odoo.tools import config

_logger = logging.getLogger(__name__)

VARIABLE = "BF_NFC_FERNET_KEY"
CLE_CONF = "bf_nfc_fernet_key"
PARAM_BASE = "bf_nfc.cle_de_chiffrement"


def origine_de_la_cle(env):
    """« environnement », « configuration », « base » ou « absente »."""
    if os.environ.get(VARIABLE):
        return "environnement"
    if config.get(CLE_CONF):
        return "configuration"
    if env["ir.config_parameter"].sudo().get_param(PARAM_BASE):
        return "base"
    return "absente"


def _cle(env, creer=True):
    cle = os.environ.get(VARIABLE) or config.get(CLE_CONF)
    if cle:
        return cle
    icp = env["ir.config_parameter"].sudo()
    cle = icp.get_param(PARAM_BASE)
    if cle or not creer:
        return cle
    from cryptography.fernet import Fernet
    cle = Fernet.generate_key().decode()
    icp.set_param(PARAM_BASE, cle)
    _logger.warning(
        "bf_nfc : clé de chiffrement tirée et rangée dans la base. Posez %s dans "
        "l'environnement pour qu'une copie de la base ne livre pas les clés des puces.",
        VARIABLE)
    return cle


def chiffrer(env, texte):
    from cryptography.fernet import Fernet
    return Fernet(_cle(env).encode()).encrypt(texte.encode()).decode()


def dechiffrer(env, jeton):
    """Le texte en clair, ou None si la clé a changé depuis.

    ⚠️ Une clé de chiffrement remplacée rend les clés des puces illisibles : on
    rend None et la vérification refuse, plutôt que de lever au milieu d'un
    tapotement. La page Réglages le montre (« clés à reposer »).
    """
    if not jeton:
        return None
    from cryptography.fernet import Fernet, InvalidToken
    cle = _cle(env, creer=False)
    if not cle:
        return None
    try:
        return Fernet(cle.encode()).decrypt(jeton.encode()).decode()
    except (InvalidToken, ValueError):
        return None

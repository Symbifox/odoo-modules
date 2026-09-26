# -*- coding: utf-8 -*-
"""Chiffrement des phrases de passe de disque sequestrees.

POURQUOI UNE CLE QUI NE VIT PAS DANS LA BASE
Le sequestre introduit le seul secret du module qui doive rester RELISIBLE. Le
jeton d'une machine, lui, n'est garde qu'en sha256 justement parce que personne
n'a jamais besoin de le relire (cf. bf.policy.machine). Une phrase de passe de
disque, si : c'est tout l'interet de la ranger quelque part.

La mettre en clair dans une colonne reviendrait a poser les clefs de tout le
parc dans chaque sauvegarde de base et dans la portee de n'importe quel acces
administrateur. On chiffre donc avec une cle lue AILLEURS que dans la base :

    1. variable d'environnement  BF_POLICY_ESCROW_KEY
    2. odoo.conf                 bf_policy_escrow_key = <cle>

Un dump de base vole ne suffit alors plus a ouvrir un seul disque. La
contrepartie est explicite et doit etre assumee : perdre cette cle, c'est
perdre tout le sequestre d'un coup. Elle se sauvegarde ailleurs que dans la
base qu'elle protege, et sa rotation est un chantier a part (il faut re-chiffrer
les fiches existantes, ce que ce module ne fait pas encore).

Generer une cle :

    python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

FAIL-CLOSED, ET C'EST LE POINT IMPORTANT
Pas de cle configuree, ou pas de `cryptography` disponible : on REFUSE de
stocker, et on le dit a l'appelant. L'installateur retombe alors sur la saisie
manuelle de la phrase de passe. Accepter la phrase pour la jeter en silence
serait le pire des deux mondes : un poste installe avec une phrase generee que
personne au monde ne possede.
"""
import logging
import os

_logger = logging.getLogger(__name__)

try:  # pragma: no cover — depend de l'environnement, pas de la logique
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover
    Fernet = None
    InvalidToken = Exception

ENV_VAR = "BF_POLICY_ESCROW_KEY"
CONFIG_KEY = "bf_policy_escrow_key"


class EscrowUnavailable(Exception):
    """Le sequestre ne peut pas fonctionner : pas de cle, ou pas de librairie.

    Distincte d'une erreur de dechiffrement : ici rien n'est casse, le service
    n'est simplement pas configure. L'appelant doit degrader, pas planter.
    """


def _raw_key():
    """Rend la cle brute, ou "" si aucune source ne la fournit.

    L'environnement l'emporte sur odoo.conf : c'est ce qui permet de la poser
    sur un conteneur sans reecrire le fichier de configuration, et de la retirer
    d'un banc d'essai sans y toucher non plus.
    """
    key = (os.environ.get(ENV_VAR) or "").strip()
    if key:
        return key
    try:
        from odoo.tools import config
    except ImportError:  # pragma: no cover — hors Odoo
        return ""
    return (config.get(CONFIG_KEY) or "").strip()


def _fernet():
    if Fernet is None:
        raise EscrowUnavailable(
            "le module python `cryptography` est absent de cette installation")
    key = _raw_key()
    if not key:
        raise EscrowUnavailable(
            f"aucune cle de sequestre configuree ({ENV_VAR} ou "
            f"{CONFIG_KEY} dans odoo.conf)")
    try:
        return Fernet(key.encode())
    except Exception as exc:  # noqa: BLE001 — cle malformee
        raise EscrowUnavailable(f"cle de sequestre invalide : {exc}") from exc


def available():
    """True si le sequestre est utilisable ici et maintenant.

    Ne leve jamais : sert a afficher un etat et a decider d'une degradation,
    pas a garder un chemin critique.
    """
    try:
        _fernet()
    except EscrowUnavailable as exc:
        _logger.info("[bf_policy] sequestre indisponible : %s", exc)
        return False
    return True


def unavailable_reason():
    """Rend la raison lisible, ou "" quand tout va bien."""
    try:
        _fernet()
    except EscrowUnavailable as exc:
        return str(exc)
    return ""


def encrypt(plaintext):
    """Chiffre une phrase de passe. Leve EscrowUnavailable si non configure."""
    if not plaintext:
        raise ValueError("rien a chiffrer")
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext):
    """Dechiffre une phrase de passe sequestree.

    ⚠️ Une cle qui a change depuis le chiffrement leve InvalidToken, pas
    EscrowUnavailable : la difference compte, parce que le premier cas veut dire
    « ce coffre-ci est perdu » et le second « le coffre n'est pas branche ».
    """
    if not ciphertext:
        return ""
    return _fernet().decrypt(ciphertext.encode()).decode()

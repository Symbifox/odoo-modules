"""Retirer l'adresse de bureau d'abus héritée d'un AUTRE locataire.

Jusqu'en 18.0.1.17.1, le réglage `st_abuse_email` portait un `default=` codé en
dur vers une boîte de l'éditeur. Il s'est donc **installé tel quel** chez chaque
locataire, et l'avis d'abus — qui contient l'expéditeur, la LISTE COMPLÈTE des
destinataires, le motif et l'IP du signalant — était adressé à une autre
organisation par défaut. Retirer le `default=` du champ ne suffit pas : la
valeur est déjà écrite en base.

Règle appliquée ici, qui se décide toute seule et n'a besoin d'aucune liste
d'adresses : **on efface le réglage seulement si son domaine n'appartient à
aucune société du locataire.** Le locataire qui a légitimement mis sa propre
adresse la garde (le domaine correspond), celui qui a hérité de celle d'un
autre repart sur le repli — le courriel de sa société — sans que personne ait à
y penser. Effacer plutôt qu'écrire une valeur devinée : le repli est calculé à
l'envoi (`secure.transfer._abuse_desk_email`), donc il suit la fiche société
si elle change.

Idempotente : au second passage le réglage est vide, et on sort tout de suite.
"""
import logging

_logger = logging.getLogger(__name__)

_PARAM = "bf_securetransfer.abuse_email"


def _domaine(adresse):
    adresse = (adresse or "").strip().lower()
    return adresse.rsplit("@", 1)[-1] if "@" in adresse else ""


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    icp = env["ir.config_parameter"].sudo()
    actuel = (icp.get_param(_PARAM) or "").strip()
    if not actuel:
        _logger.info(
            "bf_securetransfer: bureau d'abus non renseigné — le courriel de "
            "la société prend le relais.")
        return

    domaines_du_locataire = {
        _domaine(c.email) for c in env["res.company"].sudo().search([]) if c.email
    } - {""}
    if _domaine(actuel) in domaines_du_locataire:
        _logger.info(
            "bf_securetransfer: bureau d'abus « %s » conservé (domaine du "
            "locataire).", actuel)
        return

    icp.set_param(_PARAM, "")
    _logger.warning(
        "bf_securetransfer: bureau d'abus « %s » EFFACÉ — son domaine n'est "
        "celui d'aucune société de ce locataire (%s). Les avis d'abus iront "
        "désormais au courriel de la société.",
        actuel, ", ".join(sorted(domaines_du_locataire)) or "aucun")

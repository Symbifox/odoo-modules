"""Empreinte les jetons d'appareil déjà émis, sans déconnecter personne.

Avant la 18.0.12.0.0, ``bf.otp.device`` gardait le jeton porteur **en clair**
dans la base : un dump livrait de quoi ouvrir le coffre de chaque personne
appariée. Les autres registres d'appareils étaient déjà passés à l'empreinte
SHA-256 ; celui-ci était resté.

La migration calcule l'empreinte de chaque jeton existant et efface le clair.
Le téléphone n'a rien à faire : il présente le même jeton, et ``_resolve`` le
reconnaît par son empreinte.

⚠️ Écrit en SQL, pas par l'ORM : ``device_token`` et ``token_hash`` portent un
``groups="base.group_system"``, et une migration n'a pas d'usager. Et le
SHA-256 se calcule en Python plutôt qu'avec ``digest()`` de pgcrypto, qui n'est
pas garanti présent sur toutes les bases.
"""
import hashlib
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        "SELECT id, device_token FROM bf_otp_device "
        "WHERE device_token IS NOT NULL AND token_hash IS NULL")
    lignes = cr.fetchall()
    if not lignes:
        _logger.info("Coffre de tokens : aucun jeton en clair à empreindre.")
        return
    # 🔴 Une collision d'empreinte ferait tomber la contrainte d'unicité au
    # milieu du lot et laisserait la moitié des appareils en clair. Deux jetons
    # identiques ne devraient pas exister (contrainte sur le clair), mais la
    # table peut porter des lignes d'avant cette contrainte : on dédoublonne.
    vues = set()
    empreints = 0
    for identifiant, brut in lignes:
        empreinte = hashlib.sha256((brut or "").encode("utf-8")).hexdigest()
        if empreinte in vues:
            _logger.warning(
                "Coffre de tokens : appareil %s partage son jeton avec un autre, "
                "laissé tel quel et à révoquer à la main.", identifiant)
            continue
        vues.add(empreinte)
        cr.execute(
            "UPDATE bf_otp_device SET token_hash = %s, device_token = NULL "
            "WHERE id = %s", (empreinte, identifiant))
        empreints += 1
    _logger.info("Coffre de tokens : %d jeton(s) empreint(s), clair effacé.",
                 empreints)

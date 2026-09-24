"""Rechiffrer les secrets avec une clé qui n'a jamais vécu dans la base.

Avant cette version, la clé Fernet était rangée dans
`ir.config_parameter` : chaque `pg_dump` emportait donc la clé ET le chiffré, et
tout dump déjà pris ouvre encore les valeurs d'aujourd'hui. Déplacer la clé ne
suffit pas ; il faut aussi que le chiffré CHANGE, sinon les dumps d'hier
gardent leur pouvoir.

La migration lit chaque secret avec l'ancienne clé et le réécrit avec la
nouvelle, prise hors de la base (variable d'environnement ou `odoo.conf`).

⚠️ En SQL brut, et non par l'ORM : `password_encrypted` et `api_key_encrypted`
portent un `groups="base.group_system"`, or une migration n'a pas d'usager.

⚠️ La nouvelle clé doit être posée AVANT la montée. Sans elle, la migration
lève et la montée échoue : c'est voulu. Une montée qui « réussit » en laissant
les secrets chiffrés par la clé de la base serait un succès en apparence.
"""

import base64
import logging
import os

_logger = logging.getLogger(__name__)

PARAM_HERITE = 'project_credential.encryption_key'
CLE_ENV = 'BF_CREDENTIALS_FERNET_KEY'
CLE_CONF = 'bf_credentials_fernet_key'
CHAMPS = ('password_encrypted', 'api_key_encrypted')


def _est_un_jeton_fernet(valeur):
    """Forme d'un jeton Fernet : base64 url-safe, premier octet 0x80."""
    if not valeur:
        return False
    try:
        brut = base64.urlsafe_b64decode(valeur.encode())
    except (ValueError, TypeError):
        return False
    return len(brut) > 57 and brut[0] == 0x80


def _cle_hors_base():
    from odoo.tools import config
    return os.environ.get(CLE_ENV) or config.get(CLE_CONF)


def migrate(cr, version):
    if not version:
        return

    from cryptography.fernet import Fernet, InvalidToken

    cr.execute(
        "SELECT id, password_encrypted, api_key_encrypted FROM project_credential "
        "WHERE password_encrypted IS NOT NULL OR api_key_encrypted IS NOT NULL"
    )
    lignes = cr.fetchall()
    if not lignes:
        # Un locataire qui porte le module sans aucun secret : il n'y a rien à
        # rechiffrer, donc rien à protéger, donc aucune raison de lui bloquer sa
        # montée. La clé lui sera réclamée au premier secret qu'il voudra
        # ranger, par `_exige_une_cle`, avec le même message.
        _logger.info(
            "bf_credentials 18.0.3.0.0 : aucun secret en base, rien à "
            "rechiffrer. La clé hors base reste exigée dès la première écriture."
        )
        return

    cr.execute("SELECT value FROM ir_config_parameter WHERE key = %s",
               (PARAM_HERITE,))
    ligne = cr.fetchone()
    cle_heritee = ligne[0] if ligne else None

    cle_neuve = _cle_hors_base()
    if not cle_neuve:
        raise ValueError(
            "bf_credentials 18.0.3.0.0 : aucune clé de chiffrement hors de la "
            "base. Posez %s dans l'environnement ou %s dans odoo.conf AVANT "
            "de monter le module, puis relancez. La clé actuelle vit dans le "
            "paramètre système %s : gardez-la, elle est la seule à ouvrir les "
            "dumps déjà pris." % (CLE_ENV, CLE_CONF, PARAM_HERITE)
        )

    if cle_heritee and cle_heritee == cle_neuve:
        _logger.warning(
            "bf_credentials : la clé hors base est la MÊME que celle du "
            "paramètre système. Rien n'est rechiffré, et tout dump déjà pris "
            "ouvre encore les secrets d'aujourd'hui. Pour fermer le défaut "
            "pour de bon, posez une clé NEUVE et relancez la montée."
        )
        return

    f_neuf = Fernet(cle_neuve.encode())
    f_ancien = Fernet(cle_heritee.encode()) if cle_heritee else None

    rechiffres = deja_faits = clair_chiffre = 0
    inconnus = []

    for cred_id, *valeurs in lignes:
        for champ, stocke in zip(CHAMPS, valeurs):
            if not stocke:
                continue
            repere = '%s.%s' % (cred_id, champ)

            if not _est_un_jeton_fernet(stocke):
                # Du clair, jamais chiffré : le repli silencieux de l'ancien
                # code. On le chiffre, c'est la seule issue qui le referme.
                neuf = f_neuf.encrypt(stocke.encode()).decode()
                clair_chiffre += 1
            else:
                try:
                    # Déjà passé : la migration est rejouable sans casse.
                    f_neuf.decrypt(stocke.encode())
                    deja_faits += 1
                    continue
                except InvalidToken:
                    pass
                if not f_ancien:
                    inconnus.append(repere)
                    continue
                try:
                    clair = f_ancien.decrypt(stocke.encode()).decode()
                except InvalidToken:
                    inconnus.append(repere)
                    continue
                neuf = f_neuf.encrypt(clair.encode()).decode()
                rechiffres += 1

            cr.execute(
                "UPDATE project_credential SET %s = %%s WHERE id = %%s" % champ,
                (neuf, cred_id),
            )

    if inconnus:
        # Ni l'ancienne clé ni la nouvelle ne les ouvrent : on ne touche à rien
        # et la montée échoue. Réécrire à l'aveugle perdrait le secret.
        raise ValueError(
            "bf_credentials 18.0.3.0.0 : %d valeur(s) ne s'ouvrent avec aucune "
            "des deux clés, rien n'a été modifié. Identifiants concernés : %s"
            % (len(inconnus), ', '.join(inconnus))
        )

    _logger.info(
        "bf_credentials 18.0.3.0.0 : %d secret(s) rechiffrés avec la clé hors "
        "base, %d déjà à jour, %d valeur(s) en clair chiffrées. Le paramètre "
        "système %s est CONSERVÉ : il ouvre les dumps d'avant la bascule. "
        "Le retirer est un geste séparé, une fois la montée éprouvée.",
        rechiffres, deja_faits, clair_chiffre, PARAM_HERITE,
    )

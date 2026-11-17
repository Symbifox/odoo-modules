"""CST-001 : « Anonymiser » devient « Supprimer ».

🔴 **Le défaut.** La règle de conservation livrée « Enregistrements de
consentement » (**CST-001**) porte ``destruction_method = anonymize``, et
``privacy.consent`` est dans l'ensemble classifiable du socle. Depuis le lot de
la 18.0.6.0.0, l'anonymiseur du consentement LÈVE toujours : son champ
« Sujet » est obligatoire, donc la personne resterait désignée quoi qu'on vide,
et la seule sortie honnête est la suppression. Chez un locataire qui a classé
ses consentements sous CST-001, chaque ligne de campagne tombait donc en
« Échec », la classification restait active, ``action_scan`` ne déduplique que
sur « en attente » et « fait », et la campagne suivante recréait la ligne et
rééchouait. **L'obligation de l'art. 23 n'était jamais honorée, mois après
mois**, et rien dans la montée ne le disait.

règle retenue du 2026-11-15 : la règle bascule vers « Supprimer », et
**uniquement là où elle porte ENCORE la valeur livrée**. Un locataire qui a
choisi sa méthode lui-même n'est pas touché, et c'est journalisé.

⚠ La règle est en ``noupdate`` : la montée ne réécrit pas sa méthode, donc le
nouveau fichier de données ne sert qu'aux installations neuves, et c'est cette
migration qui corrige les bases existantes.

⚠ **Une migration qui lève ne se rejoue pas** au prochain ``-u`` : le module
passerait en 18.0.6.0.0 sans elle, et personne ne le saurait. Tout est donc
dans un ``try`` : l'absence de la règle, sa suppression par le locataire, ou
n'importe quelle surprise se consignent au journal sans faire tomber la montée.
Rejouée, la migration ne trouve plus rien à faire.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    try:
        env = api.Environment(cr, SUPERUSER_ID, {})
        touchees = env["privacy.retention.calendar"]._privacy_switch_delivered_consent_rule()
        if touchees:
            _logger.info(
                "privacy_consent 18.0.6.0.0 : CST-001 basculé vers « Supprimer » "
                "(%s règle(s)) : « Anonymiser » rendait les consentements "
                "indestructibles.", len(touchees))
        else:
            _logger.info(
                "privacy_consent 18.0.6.0.0 : CST-001 laissé tel quel (absent, "
                "ou méthode déjà choisie par le locataire).")
    except Exception:  # noqa: BLE001 : une migration qui lève ne se rejoue pas
        _logger.exception(
            "privacy_consent 18.0.6.0.0 : la bascule de CST-001 a échoué. La "
            "montée continue ; la règle doit être revue à la main, car "
            "« Anonymiser » sur un enregistrement de consentement ne détruit "
            "RIEN.")

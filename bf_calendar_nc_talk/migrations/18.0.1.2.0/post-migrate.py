"""🔴 La fiche Apps est `noupdate` : une clé fr_CA déjà en base GAGNE sur le catalogue.

Mesuré sur une base réelle : elle rendait un nom de module que le catalogue ne
portait plus, et `_load_module_terms(..., overwrite=True)` n'y changeait RIEN.

La cause : `base.module_bf_calendar_nc_talk` porte `noupdate: True`, et
`TranslationImporter.save()` compose `noupdate_value || valeur_en_base || update_value`.
La valeur en base l'emporte donc, pour toujours — aucune montée ne la corrigera.

La parade : retirer la clé fr_CA ICI, à l'étape
`post`, qu'Odoo joue JUSTE AVANT de charger le catalogue du module (pre → données → post
→ chargement des traductions → end). Le catalogue la réécrit aussitôt, à la marque de CET
arbre.

⚠️ Une valeur retouchée à la main par un client serait donc remplacée. On la JOURNALISE
avant de la retirer, pour qu'elle reste récupérable au journal du serveur.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """ SELECT shortdesc ->> 'fr_CA', summary ->> 'fr_CA'
            FROM ir_module_module WHERE name = %s """,
        ("bf_calendar_nc_talk",),
    )
    ligne = cr.fetchone()
    if not ligne or not any(ligne):
        return
    _logger.info(
        "bf_calendar_nc_talk: fiche Apps fr_CA retirée pour laisser passer le catalogue "
        "(nom=%r, résumé=%r)", ligne[0], ligne[1],
    )
    cr.execute(
        """ UPDATE ir_module_module
            SET shortdesc = shortdesc - 'fr_CA',
                summary   = summary   - 'fr_CA'
            WHERE name = %s """,
        ("bf_calendar_nc_talk",),
    )

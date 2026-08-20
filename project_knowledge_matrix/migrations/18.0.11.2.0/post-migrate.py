"""Retirer les trois tâches planifiées fondues dans l'entretien quotidien.

Les enregistrements sont en ``noupdate="1"`` : les sortir du fichier de données
ne les efface pas de la base, Odoo ne nettoie les données d'un module qu'à sa
désinstallation. Sans ce passage, les trois anciennes tâches continueraient de
tourner À CÔTÉ de la nouvelle, et chaque activité serait créée deux fois.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

FONDUES = (
    'ir_cron_document_pending_acknowledgments',
    'ir_cron_document_review_expiration',
    'ir_cron_document_outdated_client_docs',
)


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    donnees = env['ir.model.data'].search([
        ('module', '=', 'project_knowledge_matrix'),
        ('model', '=', 'ir.cron'),
        ('name', 'in', list(FONDUES)),
    ])
    if not donnees:
        _logger.info(
            'project_knowledge_matrix : aucune ancienne tâche de documents à retirer.')
        return

    crons = env['ir.cron'].browse(donnees.mapped('res_id')).exists()

    # Une passe que quelqu'un avait volontairement désactivée se retrouverait
    # rallumée par la fusion. Ça ne doit pas passer inaperçu.
    for cron in crons:
        if not cron.active:
            _logger.warning(
                'project_knowledge_matrix : la tâche « %s » était DÉSACTIVÉE et '
                'sa passe reprend du service dans « Documents : entretien '
                'quotidien ». À revoir si la désactivation était voulue.',
                cron.cron_name,
            )
        else:
            _logger.info(
                'project_knowledge_matrix : retrait de la tâche « %s » '
                '(prochaine exécution prévue le %s).',
                cron.cron_name, cron.nextcall,
            )

    nombre = len(crons)
    crons.unlink()
    donnees.unlink()
    _logger.info(
        'project_knowledge_matrix : %s tâche(s) planifiée(s) fondues dans '
        '« Documents : entretien quotidien ».', nombre)

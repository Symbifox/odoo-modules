"""Forcer le rafraîchissement du gabarit du rapport bimensuel.

Le gabarit vit dans un fichier ``noupdate="1"`` : une mise à niveau ne le
recharge pas, et basculer la colonne ``noupdate`` de ``ir_model_data`` n'y
change rien : Odoo honore l'attribut du FICHIER au chargement, pas le drapeau
en base. Or ce gabarit gagne ici deux conditions d'affichage sans lesquelles il
continuerait d'annoncer les distributions, remplies de zéros, tous les quinze
jours.

Le supprimer AVANT le chargement des fichiers de données le fait recréer neuf
sous le même identifiant externe. Le post-migrate remet ensuite les trois
créneaux de langue en phase.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

XMLID = 'mail_template_document_dashboard_report'


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    donnee = env['ir.model.data'].search([
        ('module', '=', 'project_knowledge_matrix'),
        ('model', '=', 'mail.template'),
        ('name', '=', XMLID),
    ], limit=1)
    if not donnee:
        _logger.info(
            'project_knowledge_matrix : aucun gabarit de rapport à rafraîchir.')
        return

    gabarit = env['mail.template'].browse(donnee.res_id).exists()
    if gabarit:
        gabarit.unlink()
    donnee.unlink()
    _logger.info(
        'project_knowledge_matrix : gabarit du rapport bimensuel supprimé pour '
        'être recréé par le chargement des données.')

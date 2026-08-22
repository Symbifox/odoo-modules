"""Détacher les menus, remettre les créneaux du gabarit, et dire l'état de l'interrupteur.

Trois choses, dans cet ordre.

1. Les trois menus de distribution portaient « Documents / Utilisateur ». Le
   fichier leur donne désormais le groupe interrupteur, mais un
   ``<menuitem groups="...">`` ne produit que des ``Command.link`` : il AJOUTE
   le nouveau groupe et ne retire jamais l'ancien. Sans ce passage, les trois
   menus resteraient visibles pour tout utilisateur de documents, interrupteur
   éteint ou non, et le rangement n'aurait rien rangé.

2. Le gabarit recréé par le pre-migrate ne porte que le créneau de la langue
   source. Les trois créneaux (``en_US``, ``fr_CA``, ``en_CA``) portaient le
   même texte français avant la mise à niveau : le corps est écrit en français
   dans le XML, il n'y a donc pas de traduction à préserver, seulement des
   créneaux à ne pas laisser tomber.

3. Le sous-système de distribution est ÉTEINT sur toute base existante, parce
   que le groupe interrupteur naît sans être implicite. C'est la décision, et
   elle se renverse d'une case dans les paramètres, mais elle ne doit pas
   passer inaperçue sur une instance qui distribuait vraiment. On compte donc ce
   qui existe et on le journalise.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    # 1. Détacher les trois menus de « Documents / Utilisateur ».
    porteur_menus = env.ref(
        'project_knowledge_matrix.group_document_user', raise_if_not_found=False)
    if porteur_menus:
        for xmlid in ('menu_distributions', 'menu_distributions_internal',
                      'menu_distributions_outdated'):
            menu = env.ref(f'project_knowledge_matrix.{xmlid}',
                           raise_if_not_found=False)
            if menu and porteur_menus in menu.groups_id:
                menu.write({'groups_id': [(3, porteur_menus.id)]})
                _logger.info(
                    'project_knowledge_matrix : menu « %s » détaché de '
                    '« Documents / Utilisateur ».', menu.name)

    # 2. Les créneaux de langue du gabarit recréé.
    gabarit = env.ref(
        'project_knowledge_matrix.mail_template_document_dashboard_report',
        raise_if_not_found=False)
    if gabarit:
        cr.execute("""
            UPDATE mail_template
               SET body_html = jsonb_set(
                       jsonb_set(body_html, '{fr_CA}', body_html->'en_US'),
                       '{en_CA}', body_html->'en_US')
             WHERE id = %s
               AND body_html ? 'en_US'
        """, (gabarit.id,))
        _logger.info(
            'project_knowledge_matrix : créneaux fr_CA et en_CA du gabarit du '
            'rapport remis sur le texte source.')

    # 3. L'état du sous-système, et ce qu'il y a derrière.
    interrupteur = env.ref(
        'project_knowledge_matrix.group_document_distribution',
        raise_if_not_found=False)
    porteur = env.ref(
        'project_knowledge_matrix.group_document_user',
        raise_if_not_found=False)
    allume = bool(interrupteur and porteur and interrupteur in porteur.implied_ids)

    if allume:
        _logger.info(
            'project_knowledge_matrix : la distribution est allumée, rien à '
            'signaler.')
        return

    Distribution = env['project.document.distribution']
    total = Distribution.search_count([])
    vivantes = Distribution.search_count([('state', 'in', ('pending', 'acknowledged'))])

    if not total:
        _logger.info(
            'project_knowledge_matrix : distribution éteinte (aucune '
            'distribution en base). Réactivable dans Paramètres > Base de '
            'connaissances.')
        return

    _logger.warning(
        'project_knowledge_matrix : distribution ÉTEINTE alors que la base '
        'porte %s distribution(s), dont %s en attente ou accusée(s). Les '
        'données restent intactes ; les menus, les deux passes d\'entretien et '
        'les blocs du tableau de bord et du rapport cessent de les montrer. '
        'Réactivable dans Paramètres > Base de connaissances > Distribution et '
        'accusés de réception.',
        total, vivantes)

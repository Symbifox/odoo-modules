# -*- coding: utf-8 -*-
"""la pièce d'une ressource doit lui appartenir.

Les pièces libres (sans dossier) déjà posées sur une ressource lui sont
rattachées, sinon la route du code QR ne les servirait plus. Les ressources
qui pointent vers la pièce d'un autre dossier sont signalées au journal : leur
code QR rend désormais 404, à revoir à la main.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE ir_attachment a
           SET res_model = 'bf.process.node.resource', res_id = r.id
          FROM (SELECT DISTINCT ON (attachment_id) id, attachment_id
                  FROM bf_process_node_resource
                 WHERE attachment_id IS NOT NULL
                 ORDER BY attachment_id, id) r
         WHERE a.id = r.attachment_id
           AND a.res_model IS NULL
           AND COALESCE(a.res_id, 0) = 0
           AND a.res_field IS NULL
    """)
    _logger.info("bf_process : %s pièce(s) libre(s) rattachée(s) à leur ressource",
                 cr.rowcount)
    cr.execute("""
        SELECT r.id, r.name, a.id, a.res_model, a.res_id
          FROM bf_process_node_resource r
          JOIN ir_attachment a ON a.id = r.attachment_id
          JOIN bf_process_node n ON n.id = r.node_id
     LEFT JOIN bf_process_node_resource soeur
            ON a.res_model = 'bf.process.node.resource' AND soeur.id = a.res_id
         WHERE a.res_field IS NOT NULL
            OR NOT (
                (a.res_model = 'bf.process.node.resource' AND a.res_id = r.id)
             OR (a.res_model = 'bf.process.node' AND a.res_id = r.node_id)
             OR (a.res_model = 'bf.process' AND a.res_id = n.process_id)
             OR (soeur.process_id = n.process_id))
    """)
    for rid, name, aid, model, res_id in cr.fetchall():
        _logger.warning(
            "bf_process : ressource %s (%s) pointe vers la pièce %s de %s,%s ;"
            " son code QR ne la sert plus", rid, name, aid, model, res_id)

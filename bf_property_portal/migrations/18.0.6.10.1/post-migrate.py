"""Rattache aux pièces du portail les fichiers déposés avant la 18.0.6.10.1.

La route de téléchargement ne sert plus que le fichier rattaché à la pièce
(`res_model`, `res_id`) : sans ce rattrapage, toutes les pièces déjà publiées
redirigeraient vers la liste. Même règle que `bf_rental` 18.0.2.4.0 : seul un
fichier orphelin cité par UNE seule pièce est rattaché. Les autres sont
signalés au journal, à revoir à la main.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute(
        """
        UPDATE ir_attachment a
           SET res_model = 'bf.property.document', res_id = d.document_id
          FROM (SELECT attachment_id, min(id) AS document_id
                  FROM bf_property_document
                 WHERE attachment_id IS NOT NULL
                 GROUP BY attachment_id
                HAVING count(*) = 1) d
         WHERE a.id = d.attachment_id
           AND COALESCE(a.res_id, 0) = 0
           AND a.res_field IS NULL
           AND (a.res_model IS NULL OR a.res_model = 'bf.property.document')
        """
    )
    _logger.info("bf_property_portal : %s fichier(s) rattaché(s) à leur pièce", cr.rowcount)
    cr.execute(
        """
        SELECT d.id, a.id, a.res_model, a.res_id
          FROM bf_property_document d
          JOIN ir_attachment a ON a.id = d.attachment_id
         WHERE a.res_field IS NOT NULL
            OR a.res_model IS DISTINCT FROM 'bf.property.document'
            OR a.res_id IS DISTINCT FROM d.id
        """
    )
    for doc_id, att_id, model, res_id in cr.fetchall():
        _logger.warning(
            "bf_property_portal : la pièce %s cite le fichier %s de %s,%s ;"
            " le portail ne le sert plus", doc_id, att_id, model, res_id)

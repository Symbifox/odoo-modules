"""Rattache aux baux les formulaires signés déposés avant la 18.0.2.4.0.

Un fichier déposé sur un bail neuf naissait sans `res_id` : Odoo le réservait à
la personne qui l'avait déposé, et tout autre gestionnaire recevait un refus
d'accès en ouvrant le bail. `create` et `write` rattachent désormais la pièce ;
ceci rattrape celles qui existent déjà.

Seul un fichier cité par UN seul bail est rattaché : un fichier partagé entre
deux baux n'a pas de propriétaire évident, et on ne le devine pas.
"""


def migrate(cr, version):
    cr.execute(
        """
        UPDATE ir_attachment a
           SET res_model = 'bf.rental.lease', res_id = rel.lease_id
          FROM (SELECT ir_attachment_id, min(bf_rental_lease_id) AS lease_id
                  FROM bf_rental_lease_ir_attachment_rel
                 GROUP BY ir_attachment_id
                HAVING count(*) = 1) rel
         WHERE a.id = rel.ir_attachment_id
           AND COALESCE(a.res_id, 0) = 0
           AND a.res_field IS NULL
           AND (a.res_model IS NULL OR a.res_model = 'bf.rental.lease')
        """
    )

"""Noms des règles d'accès en anglais.

`ir.rule.name` n'est pas traduisible en Odoo 18 : le catalogue ne peut rien pour
lui, et ces quatre règles sont `noupdate`, donc la montée ne relit pas le fichier.
On renomme SEULEMENT si la base porte encore le nom français livré : un nom
retouché par le locataire reste le sien.
"""
import logging

_logger = logging.getLogger(__name__)

NOMS = {
    "rule_hour_bank_manager": (
        "Banque d'heures : Accès complet gestionnaire",
        "Hour bank: full access for managers",
    ),
    "rule_hour_bank_user": (
        "Banque d'heures : Lecture utilisateur projet",
        "Hour bank: read access for project users",
    ),
    "rule_hour_bank_portal": (
        "Banque d'heures : Lecture portail client",
        "Hour bank: client portal read access",
    ),
    "rule_hour_bank_adjustment_portal": (
        "Ajustement : Lecture portail client",
        "Adjustment: client portal read access",
    ),
}


def migrate(cr, version):
    if not version:
        return
    for xmlid, (francais, anglais) in NOMS.items():
        cr.execute(
            """
            UPDATE ir_rule r SET name = %s
              FROM ir_model_data d
             WHERE d.module = 'bf_hour_bank' AND d.name = %s
               AND d.model = 'ir.rule' AND d.res_id = r.id
               AND r.name = %s
            """,
            (anglais, xmlid, francais),
        )
        if cr.rowcount:
            _logger.info("bf_hour_bank: règle %s renommée en anglais", xmlid)
        else:
            _logger.info("bf_hour_bank: règle %s laissée telle quelle "
                         "(nom retouché, ou déjà anglais)", xmlid)

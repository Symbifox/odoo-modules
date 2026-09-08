# -*- coding: utf-8 -*-
"""Le lien vers le profil, et rien de plus.

⚠️ Aucun champ de ce module n'apparaît sur la fiche employé visible des
collègues ou des RH. Afficher « célèbre / ne célèbre pas » sur la fiche
transformerait un retrait discret en information publique, ce qui est
exactement ce que le module cherche à éviter. Le seul champ posé ici sert au
domaine des crons, qui tournent en sudo.
"""

from odoo import fields, models


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    celebration_profile_id = fields.One2many(
        "bf.celebration.profile", "employee_id",
        string="Profil de célébration",
        groups="base.group_system",
    )

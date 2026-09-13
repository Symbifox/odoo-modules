# -*- coding: utf-8 -*-
from odoo import fields, models


class ResourceBookingType(models.Model):
    _inherit = "resource.booking.type"

    bf_create_agenda = fields.Boolean(
        string="Créer un ordre du jour",
        default=False,
        help="Fabrique un ordre du jour dès qu'un rendez-vous de ce type est "
             "confirmé, et joint son lien de contribution à la confirmation "
             "envoyée au demandeur.\n\n"
             "Décoché par défaut : tous les rendez-vous ne se préparent pas. "
             "Une démonstration ou un café n'ont pas d'ordre du jour à tenir.",
    )

    def _bf_agenda_project(self):
        """Le projet où ranger l'ordre du jour d'un rendez-vous de ce type.

        Le projet du type d'abord ; à défaut celui de repli de la société.
        Rend un recordset vide quand ni l'un ni l'autre n'est posé — et
        l'appelant s'abstient alors de créer quoi que ce soit.
        """
        self.ensure_one()
        if self.project_id:
            return self.project_id
        company = self.company_id or self.env.company
        return company.sudo().bf_appointment_agenda_project_id

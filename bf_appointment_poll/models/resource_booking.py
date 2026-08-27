# -*- coding: utf-8 -*-
"""Ce que « Renvoyer les invitations » doit faire quand la réservation vient
d'un sondage : servir TOUT LE MONDE, et non le seul demandeur.

Le module parent renvoie sa confirmation au demandeur (`object.partner_id`),
parce que c'est ce qu'une réservation publique a : une personne. Un sondage en
a plusieurs, chacun avec son fuseau et son propre fichier d'agenda.
"""

from odoo import models


class ResourceBooking(models.Model):
    _inherit = "resource.booking"

    def _bf_resend_invitations(self):
        self.ensure_one()
        source = self._bf_source_record()
        if source and source._name == "appointment.poll" and source.participant_ids:
            source.participant_ids._send_scheduled_notice()
            return len(source.participant_ids)
        return super()._bf_resend_invitations()

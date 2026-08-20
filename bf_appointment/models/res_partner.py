# -*- coding: utf-8 -*-
"""Un lien de rendez-vous depuis la fiche d'un contact."""

from odoo import _, models
from odoo.exceptions import UserError


class ResPartner(models.Model):
    _inherit = "res.partner"

    def action_bf_booking_link(self):
        """Fabrique un lien personnel et l'affiche, prêt à être copié."""
        self.ensure_one()
        if not self.email:
            raise UserError(_(
                "%s n'a pas d'adresse courriel. Le lien lui serait rattaché "
                "sans qu'on puisse lui écrire.", self.display_name))
        societe = self.env.company
        booking_type = societe.appointment_quick_link_type_id or self.env[
            "resource.booking.type"].search(
            [("is_public", "=", True), ("listed_on_landing", "=", True)],
            order="sequence, id", limit=1)
        if not booking_type:
            raise UserError(_(
                "Aucun type de rendez-vous public n'est configuré. Choisissez-en "
                "un dans Configuration, sous « Type pour les liens rapides »."))
        booking = booking_type._bf_create_onetime_link(self)
        return booking._bf_action_show_link()

"""Le colis prévient son destinataire, s'il a consenti.

⚠️ L'avis part à la CRÉATION du colis, pas à sa remise : c'est l'arrivée qui
intéresse la personne. Et il ne part qu'une fois, même si la fiche est modifiée
ensuite.
"""
from odoo import _, api, models

from .bf_property_organisation import PARCEL_TEMPLATE


class BfPropertyParcel(models.Model):
    _inherit = "bf.property.parcel"

    @api.model_create_multi
    def create(self, vals_list):
        parcels = super().create(vals_list)
        for parcel in parcels:
            parcel._notify_arrival()
        return parcels

    def _notify_arrival(self):
        """Prévient, ou consigne pourquoi il n'a pas prévenu.

        ⚠️ Ne lève jamais. Un colis s'enregistre même quand personne ne peut
        être joint : le concierge a le colis dans les mains, il n'a pas à voir
        une erreur parce que l'occupant n'a pas donné son numéro. La raison va
        au fil, où elle sert à quelqu'un.
        """
        self.ensure_one()
        body = PARCEL_TEMPLATE % {"syndicat": self.organisation_id.name}
        sent, reason = self.organisation_id.sudo()._send_property_sms(
            self.partner_id, "parcel", body
        )
        # ⚠️ `_message_log`, pas `message_post` :
        # la note exige une adresse à son auteur, et un concierge sans courriel
        # voyait l'enregistrement du colis MOURIR sur « configurez l'adresse de
        # l'expéditeur ». Ce sont des notes internes : elles n'ont personne à
        # joindre, donc pas besoin d'expéditeur.
        if sent:
            self._message_log(body=_("Avis par texto envoyé au destinataire."))
        else:
            self._message_log(body=_("Aucun avis par texto : %s.") % reason)
        return sent

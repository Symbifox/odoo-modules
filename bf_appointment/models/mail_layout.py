"""Ce dont la mise en page commune des courriels a besoin.

Les gabarits de rendez-vous ne portent plus de coquille : `send_mail` les habille
avec `bf_onboarding_base.bf_mail_layout`, au nom de la société que
`_mail_get_companies` désigne. Une réservation et un invité n'ont pas de
`company_id` : sans ce qui suit, Odoo prendrait la société de l'utilisateur qui
envoie (le public, la tâche planifiée), et l'en-tête pourrait nommer une autre
société que le contenu, qui suit celle du type de rendez-vous.
"""
import re

from odoo import models

# Les défauts des réglages Rendez-vous : jamais cités dans un courriel.
CONTACT_FACTICE = {"service@example.com", "+15555555555", "555-555-5555"}


class ResCompany(models.Model):
    _inherit = "res.company"

    def bf_appointment_contact(self):
        """Adresse et téléphone que cite la phrase de contact des courriels.

        Le réglage Rendez-vous d'abord, sauf une valeur factice des défauts ; sinon
        l'adresse et le téléphone de la société. Une valeur vide fait taire la
        phrase dans le gabarit.
        """
        company = self[:1]

        def reel(valeur):
            valeur = (valeur or "").strip()
            return "" if valeur in CONTACT_FACTICE else valeur

        email = reel(company.appointment_brand_support_email) or (company.email or "")
        phone = reel(company.appointment_brand_support_phone)
        affiche = reel(company.appointment_brand_support_phone_display)
        if not phone and not affiche:
            phone = affiche = company.phone or ""
        return {
            "email": email,
            "phone": re.sub(r"[^\d+]", "", phone or affiche),
            "phone_display": affiche or phone,
        }


class ResourceBooking(models.Model):
    _inherit = "resource.booking"

    def _mail_get_companies(self, default=False):
        defaut = default or self.env["res.company"]
        return {booking.id: booking.type_id.company_id or defaut for booking in self}


class ResourceBookingGuest(models.Model):
    _inherit = "resource.booking.guest"

    def _mail_get_companies(self, default=False):
        defaut = default or self.env["res.company"]
        return {guest.id: guest.booking_id.type_id.company_id or defaut for guest in self}

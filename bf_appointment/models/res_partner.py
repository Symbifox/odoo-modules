# -*- coding: utf-8 -*-
"""Un lien de rendez-vous depuis la fiche d'un contact, et la recherche du
consentement actif d'une personne.

La recherche vivait dans le contrôleur, en fonction de module. Elle est
partner-scoped : c'est le CONTACT qui porte ses consentements, pas la page qui
les lit. Remontée ici, elle sert aussi le modèle `resource.booking`, qui ne
peut décemment pas importer un contrôleur pour savoir ce qu'il a au dossier.
"""

from odoo import _, fields, models
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

    def _bf_active_consent(self, purpose_code, notice_id=False):
        """Le `privacy.consent` actif pour (ce contact, cet objet), ou rien.

        « Actif » = accordé, non archivé, non révoqué, non expiré, ET rattaché
        à l'avis courant : une révision majeure de l'avis force donc une
        nouvelle demande.
        """
        self.ensure_one()
        if not purpose_code:
            return False
        domain = [
            ("subject_partner_id", "=", self.id),
            ("status", "=", "granted"),
            ("active", "=", True),
            ("withdrawn_at", "=", False),
            ("purpose_id.code", "=", purpose_code),
            "|", ("expires_at", "=", False),
            ("expires_at", ">", fields.Datetime.now()),
        ]
        if notice_id:
            domain.append(("notice_id", "=", notice_id))
        consent = self.env["privacy.consent"].sudo().search(
            domain, order="granted_at desc", limit=1)
        return consent or False

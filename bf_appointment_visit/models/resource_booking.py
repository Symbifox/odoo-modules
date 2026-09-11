"""Le raccord entre une réservation et une visite.

Le module ne réécrit pas le parcours de réservation : il s'y accroche. Une
réservation dont le type appartient à une inscription produit une visite, et
c'est la visite qui porte le registre, l'approbation et l'avis au locataire.

L'accroche est `action_confirm`, parce que c'est le seul point par où passent
les quatre chemins qui aboutissent à un rendez-vous : la page publique, le lien
personnel à usage unique, le sondage de disponibilités, et une création au
back-office.
"""

import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class ResourceBooking(models.Model):
    _inherit = "resource.booking"

    visit_id = fields.Many2one(
        "bf.visit", string="Visite", copy=False, ondelete="set null",
    )

    def _bf_visit_listing(self):
        """L'inscription derrière cette réservation, s'il y en a une."""
        self.ensure_one()
        return self.type_id.visit_listing_id

    def action_confirm(self):
        result = super().action_confirm()
        for booking in self:
            inscription = booking._bf_visit_listing()
            if not inscription:
                continue
            try:
                booking._bf_ensure_visit(inscription)
            except Exception:  # noqa: BLE001
                # Une visite qui ne se pose pas ne doit pas faire perdre le
                # rendez-vous : le créneau est déjà retenu, et le registre se
                # complète à la main. On veut la trace, pas la panne.
                _logger.exception(
                    "Visite non créée pour la réservation %s", booking.id
                )
        return result

    def _bf_ensure_visit(self, inscription):
        """Créer la visite si elle manque, puis lancer son parcours une fois.

        🔴 La garde du double envoi est posée SUR LA FICHE (le champ
        `setup_done` de la visite), pas sur le destinataire ni sur un drapeau
        de contexte : `action_confirm` est appelée plus d'une fois dans la vie
        d'une réservation, et deux courriels de demande au même vendeur pour la
        même visite, c'est une visite qui a l'air d'avoir été demandée deux
        fois.
        """
        self.ensure_one()
        Visit = self.env["bf.visit"].sudo()
        visite = self.visit_id or Visit.search([("booking_id", "=", self.id)], limit=1)
        demandeur = self.partner_ids[:1]
        valeurs = {
            "listing_id": inscription.id,
            "booking_id": self.id,
            "start": self.start,
            "duration": self.duration,
            "visitor_partner_id": demandeur.id if demandeur else False,
            "visitor_name": demandeur.name if demandeur else self.name,
            "visitor_email": demandeur.email if demandeur else False,
            "visitor_phone": (demandeur.phone or demandeur.mobile) if demandeur else False,
        }
        if not visite:
            visite = Visit.create(valeurs)
        else:
            visite.write({"start": self.start, "duration": self.duration})
        if self.visit_id != visite:
            self.visit_id = visite
        if not visite.setup_done:
            visite.setup_done = True
            visite._post_booking_setup()
        return visite

    def write(self, vals):
        result = super().write(vals)
        if "start" in vals or "duration" in vals:
            for booking in self.filtered("visit_id"):
                booking.visit_id.sudo().write({
                    "start": booking.start,
                    "duration": booking.duration,
                })
        return result

    def _send_appointment_email(self, template, attach_ics=True, recipient=None):
        """Taire la confirmation du socle quand la visite n'est pas accordée.

        Une inscription « sur approbation » retient bien le créneau dès la
        réservation, sinon deux personnes demanderaient la même heure. Mais le
        visiteur, lui, n'a encore rien obtenu : lui écrire « votre rendez-vous
        est confirmé » à cette seconde-là est faux, et c'est le vendeur qui le
        découvre quand le visiteur se présente.

        Le module envoie son propre mot (« votre demande est transmise »), puis
        la vraie confirmation à l'approbation.
        """
        confirmation = self.env.ref(
            "bf_appointment.mail_template_appointment_confirmation",
            raise_if_not_found=False,
        )
        retenue = (
            recipient == "booker"
            and confirmation
            and template
            and template.id == confirmation.id
            and len(self) == 1
            and self.visit_id
            and self.visit_id.state == "requested"
        )
        if retenue:
            # ⚠️ `message_post` exige une adresse sur l'auteur; une note de
            # journal passe par `_message_log`.
            self.visit_id.sudo()._message_log(
                body=_(
                    "Confirmation automatique retenue : la visite attend la "
                    "décision du vendeur."
                )
            )
            return None
        return super()._send_appointment_email(
            template, attach_ics=attach_ics, recipient=recipient
        )

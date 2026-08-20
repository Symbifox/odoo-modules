# -*- coding: utf-8 -*-
"""Invités saisis par le demandeur sur le formulaire public.

Le demandeur peut nommer des personnes à convier. **Rien ne leur est envoyé
tant qu'il ne l'a pas confirmé depuis sa boîte**, en cliquant dans le courriel
qu'il reçoit à la réservation. C'est ce qui empêche le formulaire de servir de
tremplin : il faut posséder l'adresse du demandeur pour que la moindre
invitation parte.

⚠️ Un `res.partner` n'est créé qu'à la confirmation. Tant qu'une adresse n'est
pas validée, elle reste une simple chaîne : un formulaire public ne doit pas
pouvoir remplir le carnet d'adresses.
"""

import re

from odoo import _, api, fields, models

# Volontairement permissif : on écarte les saisies manifestement fautives, on
# ne prétend pas valider une adresse — seule une livraison le prouve.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ResourceBookingGuest(models.Model):
    _name = "resource.booking.guest"
    _description = "Invité additionnel d'un rendez-vous"
    _order = "id"

    booking_id = fields.Many2one(
        "resource.booking",
        string="Réservation",
        required=True,
        ondelete="cascade",
        index=True,
    )
    email = fields.Char(string="Courriel", required=True)
    name = fields.Char(string="Nom")
    state = fields.Selection(
        [
            ("pending", "En attente de confirmation"),
            ("confirmed", "Confirmé"),
            ("declined", "Écarté"),
        ],
        string="État",
        default="pending",
        required=True,
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Contact",
        readonly=True,
        help="Créé seulement à la confirmation. Une adresse non confirmée ne "
             "doit jamais entrer dans le carnet d'adresses.",
    )
    confirmed_at = fields.Datetime(readonly=True)
    declined_at = fields.Datetime(readonly=True)

    _sql_constraints = [
        (
            "email_unique_per_booking",
            "UNIQUE(booking_id, email)",
            "Cette adresse figure déjà parmi les invités de ce rendez-vous.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals["email"] = (vals.get("email") or "").strip().lower()
            if not vals.get("name") and vals["email"]:
                vals["name"] = vals["email"].split("@")[0]
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Analyse d'une saisie libre
    # ------------------------------------------------------------------

    @api.model
    def _bf_parse_emails(self, brut, exclure=None, maximum=0):
        """Extrait des adresses d'une saisie libre, en écartant les doublons.

        Le demandeur tape ce qu'il veut : une par ligne, séparées par des
        virgules, avec ou sans nom. On ne cherche pas à tout comprendre, on
        retient ce qui ressemble à une adresse.

        :param exclure: adresses à ne jamais retenir (celle du demandeur)
        :param maximum: plafond, 0 = pas de plafond
        :return: (adresses retenues, nombre d'entrées écartées)
        """
        if not brut:
            return [], 0
        exclure = {(a or "").strip().lower() for a in (exclure or []) if a}
        vues, gardees, ecartees = set(), [], 0
        for morceau in re.split(r"[\n,;]+", brut):
            candidat = morceau.strip()
            if not candidat:
                continue
            # « Nom <adresse> » aussi bien qu'une adresse nue
            trouve = re.search(r"<([^>]+)>", candidat)
            adresse = (trouve.group(1) if trouve else candidat).strip().lower()
            if not _EMAIL_RE.match(adresse) or adresse in exclure or adresse in vues:
                ecartees += 1
                continue
            vues.add(adresse)
            gardees.append(adresse)
            if maximum and len(gardees) >= maximum:
                break
        return gardees, ecartees

    # ------------------------------------------------------------------
    # Confirmation
    # ------------------------------------------------------------------

    def _bf_confirm(self):
        """Matérialise les invités : contacts, participants, invitations."""
        Partner = self.env["res.partner"].sudo()
        a_prevenir = self.env["resource.booking.guest"]
        for invite in self.filtered(lambda g: g.state == "pending"):
            partenaire = invite.partner_id or Partner.search(
                [("email", "=ilike", invite.email)], limit=1)
            if not partenaire:
                partenaire = Partner.create({
                    "name": invite.name or invite.email,
                    "email": invite.email,
                })
            invite.write({
                "state": "confirmed",
                "partner_id": partenaire.id,
                "confirmed_at": fields.Datetime.now(),
            })
            a_prevenir |= invite
        if a_prevenir:
            reservations = a_prevenir.booking_id
            # Purement additif : `_bf_ensure_meeting_attendees` n'enlève rien de
            # ce qui a été ajouté à la main sur l'événement.
            reservations.sudo().write({
                "partner_ids": [(4, p.id, 0)
                                for p in a_prevenir.mapped("partner_id")],
            })
            reservations.sudo()._bf_ensure_meeting_attendees()
            # La confirmation change ce que la description a le droit de dire :
            # il faut la refaire, sinon l'agenda garde les réponses du
            # formulaire alors qu'un invité vient d'arriver.
            reservations.sudo()._bf_sync_meeting_description()
            a_prevenir._bf_send_invitation()
        return True

    def _bf_decline(self):
        self.filtered(lambda g: g.state == "pending").write({
            "state": "declined",
            "declined_at": fields.Datetime.now(),
        })
        return True

    def _bf_send_invitation(self):
        """⚠️ N'est appelé QUE depuis `_bf_confirm`. Aucun autre chemin ne doit
        écrire à un invité : c'est la confirmation du demandeur qui autorise
        l'envoi, et rien d'autre."""
        gabarit = self.env.ref(
            "bf_appointment.mail_template_guest_invitation",
            raise_if_not_found=False,
        )
        if not gabarit:
            return False
        for invite in self.filtered(lambda g: g.state == "confirmed"):
            gabarit.send_mail(invite.id, force_send=False)
        return True

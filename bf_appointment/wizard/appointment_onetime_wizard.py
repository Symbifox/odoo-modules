# -*- coding: utf-8 -*-
"""Fabrique de liens de réservation personnels.

Une réservation « en attente » porte déjà un jeton d'accès et une page de choix
de créneau : c'est, tel quel, un lien de réservation à usage personnel. Le
mécanisme existait donc; ce qui manquait, c'était de pouvoir en fabriquer un
sans ouvrir un shell, avec une durée de vie et des invités.

L'assistant tient en deux temps : on règle, on obtient le lien. Le second écran
existe parce qu'un lien qu'on doit aller rechercher dans une fiche n'est pas un
lien « facile à créer ».
"""

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AppointmentOnetimeWizard(models.TransientModel):
    _name = "bf.appointment.onetime.wizard"
    _description = "Créer un lien de réservation personnel"

    state = fields.Selection(
        [("config", "Réglage"), ("done", "Lien prêt")],
        default="config",
    )

    type_id = fields.Many2one(
        "resource.booking.type",
        string="Type de rendez-vous",
        required=True,
        help="Détermine les disponibilités, la durée par défaut, la salle "
             "visio et les courriels. Pour une rencontre qui ne ressemble à "
             "aucun type existant, prenez un type non listé et changez la "
             "durée et le lieu ci-dessous.",
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Destinataire",
        required=True,
        help="La personne à qui vous transmettez le lien. C'est elle qui "
             "choisira le créneau, et son nom sert à titrer la rencontre.",
    )
    guest_partner_ids = fields.Many2many(
        "res.partner",
        string="Autres participants",
        help="Personnes à inviter en plus du destinataire. Elles reçoivent "
             "l'invitation d'agenda une fois le créneau choisi, mais ne "
             "choisissent pas la date.",
    )

    custom_name = fields.Char(
        string="Titre de la rencontre",
        help="Ce qui s'affichera dans les deux agendas et dans l'invitation. "
             "Pré-rempli avec le titre calculé (« Type - Organisation x "
             "Marque ») : on peut le remplacer par le vrai sujet quand la "
             "rencontre ne se résume pas à son type.",
    )
    # Mémoire de la dernière suggestion, pour distinguer « l'usager n'a rien
    # écrit » de « l'usager a écrit exactement ça ». Sans elle, changer le
    # destinataire écraserait un titre saisi à la main.
    suggested_name = fields.Char(readonly=True)

    duration = fields.Float(
        string="Durée (heures)",
        help="Vide = la durée du type.",
    )
    location = fields.Char(string="Lieu")

    expires_in_days = fields.Integer(
        string="Expire dans (jours)",
        default=14,
        help="0 = pas d'expiration. Un lien personnel qui traîne des mois "
             "dans une boîte de réception finit par être suivi au mauvais "
             "moment.",
    )
    single_use = fields.Boolean(
        string="Usage unique",
        default=True,
        help="Une fois le rendez-vous pris, le lien ne permet plus d'en "
             "choisir un autre. La personne garde l'accès à sa page de "
             "confirmation pour voir ou annuler.",
    )

    booking_id = fields.Many2one("resource.booking", readonly=True)
    # ⚠️ CALCULÉS, non stockés, et ce n'est pas une économie de colonne.
    #
    # `url` vaut jeton d'accès : qui l'a peut réserver. Un modèle transitoire
    # écrit dans une VRAIE table — l'enregistrement y survit jusqu'au passage
    # du ramasse-miettes (une heure par défaut) et part dans toute sauvegarde
    # prise entre-temps. Le jeton vit déjà sur la réservation, qui est ici en
    # `booking_id` : le recopier ailleurs, c'était s'en donner une seconde
    # copie à surveiller pour rien.
    #
    # `expires_display` suit, pour la même raison de bon sens : un seul
    # écrivain. Au passage il se rend maintenant dans la langue de qui lit,
    # au lieu d'être figé dans celle de qui a créé le lien.
    url = fields.Char(
        string="Lien à transmettre", compute="_compute_link_display", readonly=True)
    expires_display = fields.Char(compute="_compute_link_display", readonly=True)

    @api.depends("booking_id")
    def _compute_link_display(self):
        for assistant in self:
            reservation = assistant.booking_id
            assistant.url = reservation.one_time_url or ""
            assistant.expires_display = (
                fields.Datetime.to_string(reservation.link_expires_at)
                if reservation.link_expires_at else _("aucune expiration")
            ) if reservation else ""

    @api.onchange("type_id")
    def _onchange_type_id(self):
        if self.type_id and not self.duration:
            self.duration = self.type_id.duration
        self._bf_suggest_name()

    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        self._bf_suggest_name()

    def _bf_suggest_name(self):
        """Pré-remplit le titre avec celui que la fabrique calculerait.

        Un champ vide n'apprend rien : on ne sait pas ce qu'on remplace, ni
        même qu'il existe un titre par défaut. On montre donc le titre
        calculé, et on le laisse modifiable. Tant que l'usager n'y a pas
        touché, il suit le type et le destinataire; dès qu'il l'a réécrit, on
        n'y retouche plus.
        """
        for wizard in self:
            if not wizard.type_id or not wizard.partner_id:
                continue
            suggestion = self.env["resource.booking"]._bf_build_title(
                wizard.type_id,
                partner=wizard.partner_id,
                lang=wizard.partner_id.lang or self.env.user.lang,
            )
            if not wizard.custom_name or wizard.custom_name == wizard.suggested_name:
                wizard.custom_name = suggestion
            wizard.suggested_name = suggestion

    def action_create_link(self):
        """Crée la réservation en attente et rend le lien.

        Pas de `start` : c'est tout l'objet du lien, la personne choisit son
        créneau. On ne passe donc pas par `_bf_create_booking`, dont le
        garde-fou porte justement sur l'heure demandée.
        """
        self.ensure_one()
        vals = {}
        if self.duration:
            vals["duration"] = self.duration
        if self.location:
            vals["location"] = self.location
        if self.custom_name and self.custom_name.strip():
            vals["name"] = self.custom_name.strip()
        booking = self.type_id._bf_create_onetime_link(
            self.partner_id,
            guests=self.guest_partner_ids,
            expires_in_days=self.expires_in_days,
            single_use=self.single_use,
            vals=vals,
        )
        self.write({"state": "done", "booking_id": booking.id})
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "context": self.env.context,
        }

    def action_open_booking(self):
        self.ensure_one()
        if not self.booking_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": "resource.booking",
            "res_id": self.booking_id.id,
            "view_mode": "form",
        }

    def action_new_link(self):
        """Enchaîner un second lien sans repasser par le menu."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "view_mode": "form",
            "target": "new",
            "context": {"default_type_id": self.type_id.id},
        }

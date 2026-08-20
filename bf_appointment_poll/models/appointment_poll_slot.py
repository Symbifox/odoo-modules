# -*- coding: utf-8 -*-
"""Un créneau candidat, et la retenue non bloquante qu'il pose dans l'agenda."""

import logging

import pytz

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class AppointmentPollSlot(models.Model):
    _name = "appointment.poll.slot"
    _description = "Créneau proposé au vote"
    _order = "start"

    poll_id = fields.Many2one(
        "appointment.poll",
        string="Sondage",
        required=True,
        ondelete="cascade",
        index=True,
    )
    start = fields.Datetime(string="Début", required=True)
    stop = fields.Datetime(string="Fin", required=True)
    vote_ids = fields.One2many(
        "appointment.poll.vote", "slot_id", string="Réponses"
    )

    proposed_by_id = fields.Many2one(
        "appointment.poll.participant",
        string="Proposé par",
        ondelete="set null",
        help="Vide quand la plage vient de l'organisateur. Renseigné en mode "
             "« un invité amorce » et « chacun propose ».",
    )
    is_shortlisted = fields.Boolean(
        string="Retenu",
        default=False,
        help="Sert en mode « chacun propose » : on retient d'abord quelques "
             "plages parmi le recoupement, et la retenue d'agenda ne porte "
             "que sur celles-là.",
    )

    hold_event_id = fields.Many2one(
        "calendar.event",
        string="Retenue dans l'agenda",
        readonly=True,
        copy=False,
        ondelete="set null",
        help="Événement marqué « disponible » qui rend le sondage visible dans "
             "l'agenda de l'organisateur sans bloquer les réservations "
             "publiques sur cette plage.",
    )

    yes_count = fields.Integer(compute="_compute_counts", string="Oui")
    ifneedbe_count = fields.Integer(
        compute="_compute_counts", string="Si nécessaire"
    )
    no_count = fields.Integer(compute="_compute_counts", string="Non")
    is_viable = fields.Boolean(
        compute="_compute_is_viable",
        store=True,
        string="Encore viable",
        help="Faux dès qu'un participant OBLIGATOIRE a répondu Non.",
    )
    is_complete = fields.Boolean(
        compute="_compute_is_complete",
        string="Tous les obligatoires ont répondu",
        help="Un créneau viable mais incomplet reste en attente : il manque "
             "la réponse d'au moins une personne dont la présence est requise.",
    )

    # ⚠️ Trois calculs distincts, et non un seul. Odoo avertit qu'un calcul
    # mêlant champs stockés et non stockés peut recalculer et RÉÉCRIRE le champ
    # stocké en lisant simplement un compteur, ce qui produit des écritures
    # inattendues. `is_viable` est stocké (il sert au tri et aux domaines),
    # les compteurs ne le sont pas : ils vivent séparés.

    @api.depends("vote_ids.answer")
    def _compute_counts(self):
        for slot in self:
            votes = slot.vote_ids
            slot.yes_count = len(votes.filtered(lambda v: v.answer == "yes"))
            slot.ifneedbe_count = len(
                votes.filtered(lambda v: v.answer == "ifneedbe")
            )
            slot.no_count = len(votes.filtered(lambda v: v.answer == "no"))

    @api.depends("vote_ids.answer", "vote_ids.participant_id.required")
    def _compute_is_viable(self):
        for slot in self:
            slot.is_viable = not slot.vote_ids.filtered(
                lambda v: v.answer == "no" and v.participant_id.required
            )

    @api.depends("vote_ids.participant_id.required",
                 "poll_id.participant_ids.required")
    def _compute_is_complete(self):
        for slot in self:
            requis = slot.poll_id.participant_ids.filtered("required")
            repondus = slot.vote_ids.filtered(
                lambda v: v.participant_id.required
            ).mapped("participant_id")
            slot.is_complete = bool(requis) and repondus == requis

    # -- Retenues dans l'agenda -------------------------------------------

    def _create_hold(self):
        """Pose la retenue d'agenda, au niveau demandé par le sondage.

        Deux niveaux, et la différence n'est pas cosmétique :

        * `visible` → `show_as='free'`. Depuis le correctif 18.0.2.32.0 du
          module parent, un événement marqué disponible ne bloque plus le
          sélecteur public. On voit le sondage en cours dans l'agenda, et les
          clients continuent de réserver sur ces plages.
        * `blocking` → `show_as='busy'`. La plage est réellement fermée à toute
          autre réservation le temps du sondage. C'est ce qu'il faut pour une
          rencontre qu'on ne peut pas se permettre de perdre, et c'est aussi
          pour ça que ce n'est jamais le défaut.
        """
        Event = self.env["calendar.event"]
        for slot in self.filtered(lambda s: not s.hold_event_id):
            poll = slot.poll_id
            if poll.hold_mode == "none":
                continue
            bloquant = poll.hold_mode == "blocking"
            slot.hold_event_id = Event.with_context(
                no_mail_to_attendees=True, dont_notify=True
            ).create({
                "name": _("Sondage : %s", poll.name),
                "start": slot.start,
                "stop": slot.stop,
                "user_id": poll.user_id.id,
                "partner_ids": [(6, 0, poll.user_id.partner_id.ids)],
                "show_as": "busy" if bloquant else "free",
                "description": _(
                    "Créneau soumis au vote. Cet événement se libère tout seul "
                    "à la clôture du sondage."
                ),
            })
        return True

    def _release_hold(self):
        """Libère les retenues. Appelé à la clôture, à l'annulation, et dès
        qu'un participant obligatoire écarte le créneau."""
        events = self.mapped("hold_event_id")
        if events:
            events.with_context(dont_notify=True).unlink()
        return True

    def unlink(self):
        self._release_hold()
        return super().unlink()

    # ------------------------------------------------------------------
    # Rendu pour la page publique
    #
    # Le fuseau et les noms de jours sont un piège éprouvé de ce module :
    # `strftime('%A')` suit la locale C du serveur et rendait des jours en
    # anglais à des lecteurs francophones. On passe donc par babel, comme le
    # parent, et jamais par strftime pour un nom de jour ou de mois.
    # ------------------------------------------------------------------

    def _poll_tzname(self):
        """Fuseau d'affichage du sondage.

        Faute de détection côté navigateur sur cette page, on rend dans le
        calendrier de disponibilité du type et on l'ÉTIQUETTE. Annoncer une
        heure sans dire dans quel fuseau est le défaut qui a déjà fait
        annoncer deux heures différentes pour une même rencontre.
        """
        self.ensure_one()
        return self.env["bf.timezone"].resolve([
            self.env.context.get("tz"),
            self.poll_id.type_id.resource_calendar_id.tz
            if self.poll_id.type_id else None,
        ])

    def _local(self, value):
        if not value:
            return None
        aware = pytz.utc.localize(value) if value.tzinfo is None else value
        return aware.astimezone(pytz.timezone(self._poll_tzname()))

    def display_day(self, en=False):
        """« mardi 25 août » / « Tuesday 25 August »."""
        self.ensure_one()
        local = self._local(self.start)
        if not local:
            return ""
        locale = "en_CA" if en else (
            (self.env.context.get("lang") or self.env.lang or "fr_CA").replace("-", "_")
        )
        try:
            from babel.dates import format_date
            return format_date(local.date(), format="EEEE d MMMM", locale=locale)
        except Exception:  # pragma: no cover - babel absent ou locale inconnue
            return local.strftime("%Y-%m-%d")

    def display_time(self):
        """« 14:00 – 15:00 »."""
        self.ensure_one()
        debut, fin = self._local(self.start), self._local(self.stop)
        if not debut:
            return ""
        if not fin:
            return debut.strftime("%H:%M")
        return "%s – %s" % (debut.strftime("%H:%M"), fin.strftime("%H:%M"))

    def display_tz_label(self):
        """Ville du fuseau, pour lever toute ambiguïté sur l'heure annoncée."""
        self.ensure_one()
        tzname = self._poll_tzname()
        return self.env["bf.timezone"].sudo().tz_city(tzname) or tzname

    @api.depends("start")
    def _compute_display_name(self):
        """Odoo 18 a RETIRÉ `name_get` : une surcharge y est du code mort, et
        le modèle retombe alors sur un libellé technique dans les widgets."""
        for slot in self:
            slot.display_name = fields.Datetime.to_string(slot.start) or _("Créneau")

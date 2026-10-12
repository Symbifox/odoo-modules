# -*- coding: utf-8 -*-
"""Le sondage lui-même : un jeu de créneaux candidats soumis à un groupe."""

import logging
import re
import secrets
from datetime import timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import email_normalize

_logger = logging.getLogger(__name__)

# Fenêtre par défaut dans laquelle on va chercher les créneaux candidats.
_DEFAULT_HORIZON_DAYS = 21

# Plafond du bassin offert au choix. Trois semaines d'heures ouvrables
# dépassent vite la centaine de plages : au-delà, la page devient
# illisible et le taux de réponse s'effondre.
_POOL_MAX = 60


class AppointmentPoll(models.Model):
    _name = "appointment.poll"
    _description = "Availability poll"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    name = fields.Char(
        string="Subject",
        required=True,
        tracking=True,
        help="What participants will see as the poll title.",
    )
    user_id = fields.Many2one(
        "res.users",
        string="Organizer",
        required=True,
        default=lambda self: self.env.user,
        tracking=True,
        help="The person whose calendar provides the candidate slots and "
             "who will decide at closing.",
    )
    type_id = fields.Many2one(
        "resource.booking.type",
        string="Appointment type",
        required=True,
        tracking=True,
        help="Sets the duration, the availability calendar and everything "
             "that follows at closing: video room, reminders, templates.",
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("open", "In progress"),
            ("closed", "Closed"),
            ("scheduled", "Meeting scheduled"),
            ("cancelled", "Cancelled"),
        ],
        string="State",
        default="draft",
        required=True,
        tracking=True,
    )
    description = fields.Html(
        string="Message to participants",
        sanitize=True,
        help="Shown at the top of the voting page.",
    )

    slot_ids = fields.One2many(
        "appointment.poll.slot", "poll_id", string="Proposed slots"
    )
    participant_ids = fields.One2many(
        "appointment.poll.participant", "poll_id", string="Participants"
    )

    close_date = fields.Datetime(
        string="Response deadline",
        tracking=True,
        help="After this time, the scheduled job closes the poll and "
             "notifies the organizer. Empty = no automatic closing.",
    )
    max_slots = fields.Integer(
        string="Maximum number of slots",
        default=8,
        help="Cap on the number of slots put to the vote. Beyond about "
             "ten, the response rate collapses.",
    )
    slot_source = fields.Selection(
        [
            ("organizer", "I propose the slots"),
            ("seeder", "A guest seeds the grid"),
            ("open", "Everyone proposes their slots"),
        ],
        string="Who proposes the slots",
        default="organizer",
        required=True,
        tracking=True,
        help="\"I propose\": you choose the slots, and everyone answers "
             "them.\n\"A guest seeds\": the first grid comes from a "
             "guest, and the others answer it afterwards. Useful when "
             "someone leads the matter and knows the group's constraints "
             "better than you do.\n\"Everyone proposes\": each person "
             "checks what suits them within your availability, and the "
             "overlap forms on its own. This is the mode that spares you "
             "from guessing the slots.",
    )
    seeder_participant_id = fields.Many2one(
        "appointment.poll.participant",
        string="Seeding guest",
        domain="[('poll_id', '=', id)]",
        help="Leave empty for it to simply be the first person who "
             "answers. Designating someone keeps the luck of the inbox "
             "from deciding who frames the meeting.",
    )
    seeded_by_id = fields.Many2one(
        "appointment.poll.participant",
        string="Grid seeded by",
        readonly=True,
        copy=False,
        help="Who actually set the grid. Once filled in, the grid is "
             "frozen for the next respondents.",
    )
    max_picks_per_participant = fields.Integer(
        string="Slots per person",
        default=5,
        help="Cap on the slots one person can propose. Without it, "
             "someone checks thirty and drowns the overlap.",
    )

    send_invitations = fields.Boolean(
        string="Send invitations on opening",
        default=True,
        help="Unchecked, opening the poll emails no one: the poll becomes "
             "your link to paste into your own email. Each participant's "
             "link can be copied from the Participants tab.",
    )

    # -- Inscription libre --------------------------------------------------
    # Éteint par défaut, et pour la même raison que l'audience ouverte du
    # transfert sécurisé : un lien où l'on s'inscrit soi-même accepte une
    # adresse que personne n'a validée en amont. C'est un pouvoir qu'on
    # accorde sondage par sondage, pas un défaut qu'on subit.
    self_signup = fields.Boolean(
        string="Open sign-up link",
        default=False,
        tracking=True,
        help="Publishes a link where people sign themselves up, then "
             "answer. Useful when you do not know all the group's "
             "addresses in advance.\n\n⚠️ In \"everyone proposes\" mode, "
             "a person who comes in through this link picks slots in YOUR "
             "calendar, and therefore places the matching hold. That is "
             "what the allowed domains and the sign-up cap are for.",
    )
    self_signup_max = fields.Integer(
        string="Max. sign-ups",
        default=25,
        help="Maximum number of people accepted through the sign-up link. "
             "0 = unlimited (not recommended: the cap is what keeps an "
             "overshared link from filling the poll with strangers).",
    )
    self_signup_domains = fields.Text(
        string="Allowed domains",
        help="Allowlist applied to the addresses entered: a full address, "
             "or a domain (\"@client.com\"), one per line or separated by "
             "commas. Empty = any address is accepted, up to the cap.",
    )
    signup_url = fields.Char(
        string="Sign-up link",
        compute="_compute_signup_url",
        groups="base.group_user",
        help="Public address where people sign themselves up. Empty until "
             "open sign-up is enabled.",
    )

    show_votes = fields.Boolean(
        string="Show everyone's answers",
        default=True,
        help="Shows, on the voting page, who answered what. This is how "
             "consumer polls behave, and it helps: seeing that a slot "
             "already has three \"yes\" answers encourages people to "
             "rally to it.\n\nUncheck it when people's availability is "
             "nobody else's business (a committee whose members do not "
             "know each other, or a poll that mixes people from several "
             "organizations). Everyone then sees their own answers, and "
             "nothing else.",
    )
    hold_mode = fields.Selection(
        [
            ("none", "No hold"),
            ("visible", "Visible in my calendar, not blocking"),
            ("blocking", "Actually book the slots"),
        ],
        string="Calendar hold",
        default="none",
        required=True,
        help="\"Visible\" places an event marked as free: you see the "
             "ongoing poll in your calendar, and public bookings still go "
             "through on those slots.\n\"Actually book\" closes the slots "
             "to any other booking for the duration of the poll. Keep it "
             "for meetings you cannot afford to lose.\n\nThe hold is "
             "placed slot by slot, as someone picks it, and is released "
             "as soon as a required person answers No. In \"actually "
             "book\" mode, expect each chosen slot to leave your public "
             "appointment page for the duration of the poll: the slot cap "
             "is what limits the effect.",
    )

    date_opened = fields.Datetime(
        string="Opened on",
        readonly=True,
        copy=False,
        help="Timestamp of when voting opened. Reminders are counted from "
             "IT, never from `write_date`: the latter moves with the "
             "slightest change (a corrected subject, an added slot), "
             "which would push reminders back indefinitely without anyone "
             "noticing.",
    )
    booking_id = fields.Many2one(
        "resource.booking",
        string="Appointment from the poll",
        readonly=True,
        copy=False,
        help="Created at closing by the parent module. It carries the "
             "final calendar event, the ICS file and the reminders.",
    )
    access_token = fields.Char(
        string="Poll token",
        copy=False,
        groups="base.group_user",
        help="Token of the poll itself. Each participant ALSO has their "
             "own token: that is what identifies the person voting.",
    )

    # -- Compteurs ---------------------------------------------------------

    slot_count = fields.Integer(compute="_compute_counts")
    participant_count = fields.Integer(compute="_compute_counts")
    responded_count = fields.Integer(compute="_compute_counts")
    pending_required_count = fields.Integer(
        compute="_compute_counts",
        string="Required, not answered",
    )
    viable_slot_count = fields.Integer(
        compute="_compute_counts",
        string="Slots still viable",
    )

    @api.depends(
        "slot_ids.is_viable",
        "participant_ids.responded_at",
        "participant_ids.required",
    )
    def _compute_counts(self):
        for poll in self:
            participants = poll.participant_ids
            poll.slot_count = len(poll.slot_ids)
            poll.participant_count = len(participants)
            poll.responded_count = len(participants.filtered("responded_at"))
            poll.pending_required_count = len(
                participants.filtered(lambda p: p.required and not p.responded_at)
            )
            poll.viable_slot_count = len(poll.slot_ids.filtered("is_viable"))

    # -- Cycle de vie ------------------------------------------------------

    @api.depends("access_token", "self_signup")
    def _compute_signup_url(self):
        """Lien public d'inscription, reconstruit à la lecture.

        Jamais stocké : il suit le jeton et l'adresse de base, donc
        l'organisateur voit toujours le lien vivant plutôt qu'une copie prise
        le jour où le sondage a été créé.
        """
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        for poll in self:
            poll.signup_url = (
                "%s/appointment/poll/join/%s" % (base, poll.access_token)
                if poll.self_signup and poll.access_token else False
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("access_token"):
                vals["access_token"] = secrets.token_urlsafe(24)
        return super().create(vals_list)

    def action_propose_slots(self):
        """Remplit les créneaux candidats depuis les disponibilités réelles.

        Passe par `_bf_candidate_slots()` du parent : les plages proposées
        sont donc déjà libres dans le calendrier de l'organisateur, et rien
        n'est réservé au passage.
        """
        for poll in self:
            if poll.state not in ("draft", "open"):
                raise UserError(_("A closed poll no longer takes slots."))
            horizon_start = fields.Datetime.context_timestamp(
                poll, fields.Datetime.now()
            ) + timedelta(hours=1)
            horizon_end = horizon_start + timedelta(days=_DEFAULT_HORIZON_DAYS)
            existing = set(poll.slot_ids.mapped("start"))
            candidates = poll.type_id._bf_candidate_slots(
                horizon_start, horizon_end, limit=poll.max_slots * 4 or 0
            )
            duration = timedelta(hours=poll.type_id.duration or 1.0)
            # Le budget se calcule UNE fois. Le relire à chaque tour ferait
            # descendre la cible au rythme des créations (`max_slots` moins un
            # compteur qui monte), et le sondage s'arrêterait à la moitié des
            # créneaux demandés — constaté à la sonde : 2 créés pour 3 voulus.
            budget = (
                max(poll.max_slots - len(poll.slot_ids), 0)
                if poll.max_slots
                else len(candidates)
            )
            created = 0
            for candidate in candidates:
                if created >= budget:
                    break
                # `_bf_candidate_slots` rend des datetimes AVERTIS, dans le
                # fuseau d'affichage. Odoo stocke en UTC naïf : convertir, et
                # non pas simplement retirer le fuseau, sinon le créneau se
                # décale de l'offset (le piège classique de ce module).
                start_utc = candidate.astimezone(pytz.utc).replace(tzinfo=None)
                if start_utc in existing:
                    continue
                poll.env["appointment.poll.slot"].create({
                    "poll_id": poll.id,
                    "start": start_utc,
                    "stop": start_utc + duration,
                })
                existing.add(start_utc)
                created += 1
            _logger.info("Sondage %s : %d créneaux proposés", poll.id, created)
        return True

    def action_open(self):
        """Ouvre le vote : pose les retenues, envoie les invitations."""
        for poll in self:
            if not poll.participant_ids and not poll.self_signup:
                raise UserError(_(
                    "Add at least one participant, or enable the open "
                    "sign-up link."))
            if poll.slot_source == "organizer" and not poll.slot_ids:
                raise UserError(_("Propose at least one slot before opening."))
            if poll.slot_source == "seeder" and poll.slot_ids:
                raise UserError(_(
                    "In \"a guest seeds\" mode, the grid must come from "
                    "the guest. Remove the slots, or switch back to \"I "
                    "propose\"."
                ))
            if (poll.slot_source == "seeder" and poll.seeder_participant_id
                    and poll.seeder_participant_id.poll_id != poll):
                raise UserError(_("The seeding guest must belong to this poll."))
            poll.state = "open"
            poll.date_opened = fields.Datetime.now()
            # Tient la grille déjà connue. En « chacun propose », elle est
            # normalement vide à l'ouverture et chaque plage prendra sa
            # retenue au moment où quelqu'un la choisit — mais une grille
            # pré-remplie doit être tenue comme les autres, sans quoi les
            # plages de l'organisateur seraient les seules à ne pas l'être.
            if poll.hold_mode != "none":
                poll.slot_ids._create_hold()
            # ⚠ L'ouverture n'écrit pas toujours. Quand l'organisateur veut
            # coller le lien dans son propre courriel, envoyer d'office ferait
            # un doublon que personne n'a demandé — et il arriverait AVANT le
            # sien, sans le contexte qu'il voulait donner.
            if poll.send_invitations:
                poll.participant_ids._send_invitation()
        return True

    def action_send_invitations(self):
        """Envoie l'invitation à ceux qui ne l'ont pas encore reçue.

        Le filtre porte sur `invitation_sent_on`, pas sur l'état du sondage :
        c'est ce qui rend le bouton sûr à cliquer deux fois, et ce qui permet
        d'inviter quelqu'un ajouté après l'ouverture sans réécrire à tout le
        monde.
        """
        for poll in self:
            if poll.state != "open":
                raise UserError(_(
                    "Open voting before inviting: the link would refuse "
                    "answers."))
            cibles = poll.participant_ids.filtered(
                lambda p: not p.invitation_sent_on)
            if not cibles:
                raise UserError(_(
                    "Everyone has already received their invitation. To "
                    "follow up with someone, copy their link from the "
                    "Participants tab."))
            cibles._send_invitation()
        return True

    def action_close(self):
        """Clôt le vote sans encore fixer la rencontre."""
        for poll in self:
            poll.state = "closed"
        return True

    def action_cancel(self):
        for poll in self:
            poll.slot_ids._release_hold()
            poll.state = "cancelled"
        return True

    def action_back_to_draft(self):
        for poll in self:
            poll.slot_ids._release_hold()
            poll.state = "draft"
        return True

    def action_schedule_and_open(self):
        """Demande SUR QUEL créneau fixer, puis laisse l'assistant conclure.

        🔴 Ce bouton fixait la rencontre sans rien demander, sur le premier
        créneau non rejeté dans l'ordre chronologique. Un sondage sert à
        décider ; le bouton qui conclut doit montrer ce que le sondage a dit et
        laisser trancher.

        L'assistant présélectionne le mieux classé : le geste normal reste un
        clic, mais il est éclairé.
        """
        self.ensure_one()
        if self.booking_id:
            raise UserError(_("This poll has already led to an appointment."))
        if not self.slot_ids.filtered("is_viable"):
            raise UserError(_("No viable slot to choose."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Which slot?"),
            "res_model": "appointment.poll.schedule.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_poll_id": self.id},
        }

    def action_schedule(self, slot=None):
        """Fixe la rencontre sur un créneau et laisse le parent faire le reste.

        Tout ce qui suit — événement d'agenda, ICS, salle visio, rappels
        programmés — vient du module de rendez-vous. Le sondage ne duplique
        aucune de ces mécaniques : il choisit une heure et des personnes.
        """
        self.ensure_one()
        # 🔴 `slot_ids` est trié par HEURE. Prendre `[:1]` dessus fixait la
        # rencontre sur le premier créneau que personne n'avait rejeté, même si
        # personne ne l'avait choisi non plus — « viable » veut seulement dire
        # qu'aucun obligatoire n'a dit Non, et un créneau que nul n'a regardé
        # l'est. Mesuré le 2026-08-26 : deux « oui » sur 20 h 30, aucune
        # réponse sur 19 h 30, et c'est 19 h 30 qui était réservé. Le
        # classement écrit pour cette décision existait depuis l'origine et
        # n'était appelé nulle part.
        slot = slot or self._ranked_slots().filtered("is_viable")[:1]
        if not slot:
            raise UserError(_("No viable slot to choose."))
        if self.booking_id:
            raise UserError(_("This poll has already led to an appointment."))
        partners = self.participant_ids._ensure_partners()
        # 🔴 Libérer la retenue de CE créneau AVANT de réserver. En mode
        # « réserver réellement », le sondage pose un événement `busy` sur la
        # plage ; `_bf_create_booking` demande alors une heure réellement
        # disponible et refuse la sienne : « Aucune ressource n'est disponible
        # le … ». Le module se bloquait lui-même, et le mode le plus protecteur
        # était le seul à ne pas pouvoir conclure.
        #
        # Seulement celle-ci : les autres plages restent tenues jusqu'à ce que
        # la rencontre soit acquise, sans quoi une réservation publique
        # pourrait s'y glisser pendant qu'on conclut. Si la création échoue, la
        # transaction ramène la retenue avec elle.
        slot._release_hold()
        booking = self.type_id._bf_create_booking(
            slot.start,
            partners=partners,
            vals={
                "name": self.name,
                "user_id": self.user_id.id,
                "bf_source": "poll",
                "bf_source_ref": "appointment.poll,%d" % self.id,
            },
        )
        # 🔴 L'événement d'agenda naît parfois avec le seul organisateur.
        # `_prepare_meeting_vals` du module parent construit pourtant la bonne
        # liste, et sa garde `_bf_ensure_meeting_attendees` la rejoue — les
        # deux tournent AU MILIEU de la cascade de recalculs de la création,
        # et sur banc propre elles suffisent. En production, non : une
        # rencontre est sortie avec un seul `calendar.attendee`, celui de
        # l'organisateur, alors que la réservation portait bien les trois
        # personnes. Douze des vingt dernières réservations, sondage ou page
        # publique, montrent la même perte.
        #
        # On repose donc la question ICI, une fois la création terminée et la
        # transaction à plat : c'est le premier endroit où la liste ne peut
        # plus être un artefact de cache.
        self._ensure_meeting_attendees(booking)
        # Les autres, une fois la rencontre acquise.
        self.slot_ids._release_hold()
        self.booking_id = booking
        self.state = "scheduled"
        self.message_post(
            body=_("Meeting scheduled on the slot chosen from the poll.")
        )
        # Dernier geste, et le seul qui sorte du système : la rencontre est
        # acquise, l'agenda est écrit, et c'est seulement là qu'on annonce.
        self.participant_ids._send_scheduled_notice()
        return booking

    def _ensure_meeting_attendees(self, booking):
        """Pose les participants du sondage sur l'événement d'agenda.

        Purement additif : ce que quelqu'un a ajouté à la main sur l'événement
        reste en place, et un participant déjà présent n'est pas retouché.

        L'ajout est SILENCIEUX. La confirmation brandée du module part déjà
        avec son `.ics` ; l'invitation d'agenda d'Odoo ferait doublon, et c'est
        à l'organisateur de décider de l'envoyer depuis la rencontre. Deux
        courriels pour une rencontre, c'est un de trop.
        """
        self.ensure_one()
        meeting = booking.meeting_id
        attendus = self.participant_ids.mapped("partner_id")
        if not (meeting and attendus):
            return meeting
        # ⚠️ Lire la BASE, pas le cache. La liste vient d'être écrite au milieu
        # d'une cascade de recalculs, et c'est précisément là que l'ajout se
        # perd : un contrôle qui relit le cache reproduirait l'erreur qu'il
        # est censé attraper, et se déclarerait vert.
        meeting.env.flush_all()
        meeting.invalidate_recordset(["partner_ids", "attendee_ids"])
        manquants = attendus - meeting.partner_ids
        if manquants:
            meeting.sudo().with_context(
                no_mail_to_attendees=True,
                dont_notify=True,
            ).write({
                "partner_ids": [(4, partner.id, 0) for partner in manquants],
            })
            meeting.env.flush_all()
            meeting.invalidate_recordset(["partner_ids", "attendee_ids"])
        # Le contrôle qui tranche : ce que la base rend APRÈS l'écriture. Une
        # garde qui ne peut pas échouer bruyamment ne garde rien.
        reste = attendus - meeting.partner_ids
        if reste:
            # Ne pas défaire la clôture pour autant : la rencontre est acquise,
            # l'agenda est écrit, et personne n'est mieux servi par un
            # rollback. Mais le dire, sinon l'absence se découvre le jour où on
            # cherche à qui on a donné rendez-vous.
            _logger.warning(
                "Sondage %s : %s participant(s) toujours absent(s) de "
                "l'événement %s après ajout (%s)",
                self.id, len(reste), meeting.id, reste.mapped("email"),
            )
        return meeting

    def action_view_booking(self):
        self.ensure_one()
        if not self.booking_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": "resource.booking",
            "res_id": self.booking_id.id,
            "view_mode": "form",
        }

    # -- Cron --------------------------------------------------------------

    def _bf_organizer_lang(self):
        """La langue des notes écrites pour l'organisateur sans qu'il agisse.

        Le travail planifié tourne sous OdooBot, l'inscription par le lien sous
        le visiteur : leurs notes au fil sont lues par l'organisateur. Sa
        langue si elle est installée, sinon celle de la société, sinon
        l'anglais (la langue de la source).
        """
        self.ensure_one()
        installees = {code for code, _nom in self.env["res.lang"].get_installed()}
        for lang in (self.user_id.lang, (self.company_id or self.env.company).partner_id.lang):
            if lang in installees:
                return lang
        return "en_US"

    @api.model
    def _cron_poll_maintenance(self):
        """Relances aux non-répondants, puis clôture des sondages échus.

        Deux relances, à J+2 et J+5, puis on s'arrête : au-delà, le taux de
        réponse baisse au lieu de monter. Le rythme reste une constante
        assumée plutôt qu'un réglage — un sondage dont la fenêtre demande
        un troisième rappel demande surtout une relance humaine.

        ⚠️ Ce cron est livré INACTIF (`data/poll_cron.xml`). Il écrit à des
        tiers : l'allumer est une décision d'exploitation, par locataire.
        """
        now = fields.Datetime.now()
        open_polls = self.search([("state", "=", "open")])
        open_polls.participant_ids._send_reminders()
        overdue = open_polls.filtered(
            lambda p: p.close_date and p.close_date <= now
        )
        for poll in overdue:
            lu = poll.with_context(lang=poll._bf_organizer_lang())
            # Le suivi « En cours → Clos » s'écrit lui aussi pour l'organisateur.
            lu.action_close()
            # Odoo écrit le suivi au pré-commit, dans le contexte du PREMIER sondage
            # suivi de la transaction : deux organisateurs de langues différentes clos
            # dans le même passage recevraient le même libellé. On le finalise ici,
            # dans la langue de celui-ci (le rappel du pré-commit ne trouve plus rien).
            lu._track_finalize()
            poll.message_post(
                body=lu.env._("Deadline reached: the poll is closed.")
            )
        return True

    def _others_votes(self, participant):
        """Réponses des AUTRES participants, par créneau.

        Rendu au gabarit public seulement quand `show_votes` est vrai. Le
        filtrage vit ici, pas dans la vue : une page publique ne doit pas avoir
        sous la main des données qu'elle n'affiche pas, sans quoi une condition
        mal écrite les divulgue.

        :return: {slot_id: [(nom, réponse, obligatoire), …]}
        """
        self.ensure_one()
        out = {}
        for slot in self.slot_ids:
            lignes = []
            for vote in slot.vote_ids:
                autre = vote.participant_id
                if autre == participant:
                    continue
                # 🔴 Jamais l'adresse complète d'un autre. Un nom
                # vidé dans la fiche retombait sur le courriel, lisible par
                # tous les porteurs d'un lien ; on retombe sur ce que la
                # création pose déjà, le début de l'adresse.
                lignes.append((
                    autre.name or (autre.email or "").split("@")[0] or "?",
                    vote.answer,
                    autre.required,
                ))
            out[slot.id] = lignes
        return out

    def duration_display(self):
        """« 1 h », « 30 min » — même découpage que le module parent.

        Les gabarits de courriel l'appellent : une durée brute en flottant
        (« 0.5 ») dans un courriel client est le genre de détail qui trahit
        l'outil, et le QA du parent l'avait déjà relevé.
        """
        self.ensure_one()
        heures = self.type_id.duration or 0.0
        minutes = int(round(heures * 60))
        if minutes >= 60:
            h, m = divmod(minutes, 60)
            return "%d h %02d" % (h, m) if m else "%d h" % h
        return "%d min" % minutes

    def close_display(self):
        """Date limite de réponse, rendue dans le fuseau d'affichage du sondage."""
        self.ensure_one()
        if not self.close_date:
            return ""
        tzname = (
            self.slot_ids[:1]._poll_tzname() if self.slot_ids
            else (self.type_id.resource_calendar_id.tz or "UTC")
        )
        local = pytz.utc.localize(self.close_date).astimezone(pytz.timezone(tzname))
        locale = (self.env.context.get("lang") or self.env.lang or "fr_CA").replace("-", "_")
        try:
            from babel.dates import format_datetime
            return format_datetime(local, format="EEEE d MMMM, HH:mm", locale=locale)
        except Exception:  # pragma: no cover
            return local.strftime("%Y-%m-%d %H:%M")

    # ------------------------------------------------------------------
    # Qui a le droit de proposer des plages, et lesquelles
    #
    # Les trois modes ne sont pas trois mécaniques de vote : c'est la même,
    # avec une seule question qui change — qui peut AJOUTER un créneau. Les
    # réponses, la viabilité et la clôture sont identiques partout.
    # ------------------------------------------------------------------

    def _participant_can_add_slots(self, participant):
        """Le participant peut-il ajouter des plages en ce moment ?

        Contrôle côté SERVEUR. La page publique masque déjà le sélecteur quand
        la réponse est non, mais un masquage n'est pas une autorisation : la
        route de proposition repose la question avant d'écrire.
        """
        self.ensure_one()
        if self.state != "open" or participant.poll_id != self:
            return False
        if self.slot_source == "organizer":
            return False
        if self.slot_source == "seeder":
            # ⚠ Une personne entrée par le lien d'inscription n'amorce JAMAIS :
            # la première grille posée devient celle de tout le groupe, et un
            # inconnu arrivé le premier cadrerait la rencontre pour les autres.
            # En mode « chacun propose », ce risque n'existe pas — chacun
            # ajoute SES plages, personne ne fige rien — et le refus y vidait
            # le lien d'inscription de son sens : l'inscrit tombait sur une
            # page sans bassin, sans rien à cocher.
            if participant.self_signup:
                return False
            # Une fois la grille amorcée, elle est figée pour tout le monde,
            # y compris pour celui qui l'a posée : sinon le premier répondant
            # continue de bouger le cadre sous les suivants.
            if self.seeded_by_id:
                return False
            if self.seeder_participant_id:
                return participant == self.seeder_participant_id
            return True  # personne de désigné : le premier qui répond amorce
        # open : chacun propose, dans ses limites
        if self.max_picks_per_participant and \
                participant.proposed_count >= self.max_picks_per_participant:
            return False
        if self.max_slots and len(self.slot_ids) >= self.max_slots:
            return False
        return True

    def _picks_left(self, participant):
        """(ce qu'il reste à cette personne, ce qu'elle pouvait poser en tout).

        🔴 DEUX plafonds se disputent la réponse : celui de la personne
        (`max_picks_per_participant`) et celui du sondage (`max_slots`).
        `_participant_can_add_slots` les applique tous les deux, mais la page
        n'affichait que le premier : sur un sondage plafonné à huit dont cinq
        plages étaient déjà prises, le deuxième arrivant lisait « 5/5 » et
        n'en aurait obtenu que trois. Un compteur qui promet plus que ce qui
        sera accepté est pire que pas de compteur du tout.

        La BASE rendue n'est pas le quota nominal, c'est ce que cette personne
        pouvait poser en tout : sinon « 3/5 » laisserait croire qu'elle en a
        déjà utilisé deux, alors que c'est le sondage qui est presque plein.

        0 = illimité, des deux côtés. Rend (0, 0) quand rien ne borne, et la
        page n'affiche alors aucun compteur.
        """
        self.ensure_one()
        poses = participant.proposed_count
        perso = (self.max_picks_per_participant - poses
                 if self.max_picks_per_participant else None)
        sondage = (self.max_slots - len(self.slot_ids)
                   if self.max_slots else None)
        bornes = [b for b in (perso, sondage) if b is not None]
        if not bornes:
            return 0, 0
        bases = [b for b in (self.max_picks_per_participant,
                             (sondage + poses) if sondage is not None else None)
                 if b]
        return max(min(bornes), 0), min(bases) if bases else 0

    # -- Inscription libre --------------------------------------------------

    def _self_signup_state(self):
        """(ouvert, motif) de la page d'inscription. Le motif nomme le refus."""
        self.ensure_one()
        if not self.self_signup or self.state != "open":
            return False, "closed"
        if self.close_date and self.close_date <= fields.Datetime.now():
            return False, "closed"
        if self.self_signup_max and len(
                self.participant_ids.filtered("self_signup")) >= self.self_signup_max:
            return False, "full"
        return True, ""

    @api.model
    def _email_matches_list(self, email, raw):
        """L'adresse figure-t-elle dans la liste, ou relève-t-elle d'un de ses
        domaines ? Liste vide = aucune restriction."""
        raw = (raw or "").strip()
        if not raw:
            return True
        email = (email or "").strip().lower()
        if "@" not in email:
            return False
        domaine = email.rsplit("@", 1)[1]
        for entree in re.split(r"[\s,;]+", raw.lower()):
            if not entree:
                continue
            if entree.startswith("@"):
                if domaine == entree[1:]:
                    return True
            elif "@" in entree:
                if email == entree:
                    return True
            elif domaine == entree:
                return True
        return False

    def _self_signup_join(self, name, email, tz=None):
        """Inscrit une personne par le lien, ou lui rend sa place si elle revient.

        Rend (participant, motif) : le participant est vide quand le motif dit
        pourquoi on refuse.

        ⚠ Sous le verrou de la ligne du sondage. Deux onglets ouverts au même
        instant produiraient sinon deux participants pour la même adresse,
        chacun avec son jeton, et on ne saurait plus lequel porte les réponses.
        """
        self.ensure_one()
        email = email_normalize(email) or ""
        if not email:
            return self.env["appointment.poll.participant"], "invalid"

        # 🔴 (18.0.1.15.4) Les contrôles d'ouverture et de domaine
        # passent AVANT toute recherche : le lien partagé ne doit rien rendre
        # d'un sondage fermé, ni à une adresse hors des domaines permis.
        ouvert, motif = self._self_signup_state()
        if not ouvert and motif != "full":
            return self.env["appointment.poll.participant"], motif
        if not self._email_matches_list(email, self.self_signup_domains):
            return self.env["appointment.poll.participant"], "domain"

        # 🔴 Une personne invitée nommément ne récupère JAMAIS son lien à
        # l'écran par le lien partagé : n'importe qui connaissant son adresse
        # entrait sous son nom. On lui renvoie son invitation par courriel.
        invitee = self._self_signup_invitee(email)
        if invitee:
            invitee.sudo()._send_invitation()
            return self.env["appointment.poll.participant"], "sent"

        # ⚠ Une personne DÉJÀ inscrite par le lien repasse, plafond atteint ou
        # non : sinon le 26e arrivant fermerait la porte aux 25 premiers qui
        # reviennent corriger leurs réponses. C'est aussi ce qui rend le lien
        # utilisable sans courriel de confirmation — on retrouve sa place en
        # ressaisissant son adresse (la modification des réponses reste gardée
        # par un code, cf. `_edit_needs_otp`).
        deja = self._self_signup_find(email)
        if deja:
            deja._remember_tz(tz)
            return deja, ""
        if not ouvert:
            return self.env["appointment.poll.participant"], motif

        self.env.cr.execute(
            "SELECT id FROM appointment_poll WHERE id = %s FOR UPDATE", (self.id,))
        self.invalidate_recordset(["participant_ids"])
        deja = self._self_signup_find(email)
        if deja:
            return deja, ""
        ouvert, motif = self._self_signup_state()
        if not ouvert:
            return self.env["appointment.poll.participant"], motif

        participant = self.env["appointment.poll.participant"].sudo().create({
            "poll_id": self.id,
            "name": (name or "").strip()[:120] or email.split("@")[0],
            "email": email,
            # ⚠ Facultative, toujours. La viabilité d'un créneau se calcule sur
            # les seules personnes obligatoires : un inconnu marqué obligatoire
            # rendrait toutes les plages incomplètes tant qu'il n'a pas répondu,
            # et un seul « Non » de sa part écarterait le créneau pour tous.
            "required": False,
            "self_signup": True,
            # Capté du navigateur : sans lui, on écrirait à cette personne
            # dans le fuseau de quelqu'un d'autre.
            "tz": self.env["bf.timezone"].resolve([tz], fallback=False) or False,
        })
        # ⚠️ `partner_ids` n'est pas décoratif. Sans lui, le message se dépose
        # au fil du sondage et ne notifie PERSONNE — vérifié le 2026-08-25 :
        # `notified_partner_ids` restait vide alors même que l'organisateur est
        # abonné au fil. Il fallait ouvrir le sondage pour apprendre que
        # quelqu'un était arrivé, ce qui vide un lien d'inscription de son
        # intérêt : on le diffuse justement pour ne pas avoir à surveiller.
        # Écrit par la page publique, dans la langue du visiteur : lu par l'organisateur.
        lu = self.with_context(lang=self._bf_organizer_lang())
        self.message_post(
            body=lu.env._("%(nom)s (%(courriel)s) signed up through the link.",
                   nom=participant.name, courriel=email),
            partner_ids=self.user_id.partner_id.ids,
        )
        return participant, ""

    def _self_signup_find(self, email):
        """Le participant déjà inscrit PAR LE LIEN à cette adresse.

        🔴 Seuls les inscrits libres sont cherchés : rendre ici une
        personne invitée nommément livrait son jeton personnel à quiconque
        connaissait son adresse. Voir `_self_signup_invitee`."""
        self.ensure_one()
        email = (email or "").strip().lower()
        return self.participant_ids.filtered(
            lambda p: p.self_signup
            and (p.email or "").strip().lower() == email)[:1]

    def _self_signup_invitee(self, email):
        """La personne invitée nommément à cette adresse, s'il y en a une."""
        self.ensure_one()
        email = (email or "").strip().lower()
        return self.participant_ids.filtered(
            lambda p: not p.self_signup
            and (p.email or "").strip().lower() == email)[:1]

    def _waiting_for_seeder(self):
        """Vrai quand un répondant arrive avant que la grille existe."""
        self.ensure_one()
        return (
            self.slot_source == "seeder"
            and not self.seeded_by_id
            and not self.slot_ids
        )

    def _slot_pool(self, participant, horizon_days=None):
        """Plages libres offertes au choix, moins celles déjà proposées.

        Calculé à la volée depuis `_bf_candidate_slots()` du module parent
        plutôt que stocké : un bassin de trois semaines représente des dizaines
        de plages, et les matérialiser toutes en base pour n'en retenir que
        quelques-unes serait du gaspillage. Un enregistrement de créneau ne
        naît qu'au moment où quelqu'un le choisit.

        :return: liste de datetimes naïfs UTC, triée
        """
        self.ensure_one()
        depart = fields.Datetime.context_timestamp(
            self, fields.Datetime.now()
        ) + timedelta(hours=1)
        fin = depart + timedelta(days=horizon_days or _DEFAULT_HORIZON_DAYS)
        deja = set(self.slot_ids.mapped("start"))
        pool = []
        for candidat in self.type_id._bf_candidate_slots(depart, fin, limit=_POOL_MAX):
            utc = candidat.astimezone(pytz.utc).replace(tzinfo=None)
            if utc not in deja:
                pool.append(utc)
        return pool

    def _add_slot_from_pool(self, participant, start_utc):
        """Retient une plage choisie par un participant.

        Deux cas, et les confondre vide le mode de son sens :

        * **Quelqu'un l'a déjà proposée.** On ne crée rien, on inscrit un
          « oui » sur le créneau existant. C'est LÀ que naît le recoupement :
          si chaque personne repartait sur son propre enregistrement, deux
          disponibilités identiques ne se rencontreraient jamais. Rejoindre ne
          consomme pas le quota de propositions — on n'a rien proposé.
        * **Personne ne l'a proposée.** On la matérialise, dans les limites.

        ⚠️ La date vient d'un formulaire public : elle n'est JAMAIS prise pour
        argent comptant. Pour une plage neuve, on exige qu'elle figure dans le
        bassin réellement calculé depuis les disponibilités de l'organisateur.
        Sans ce contrôle, n'importe qui poserait une rencontre à 3 h du matin
        un dimanche dans son agenda.
        """
        self.ensure_one()
        existant = self.slot_ids.filtered(lambda s: s.start == start_utc)
        if existant:
            self._register_yes(participant, existant[:1])
            return existant[:1]
        if not self._participant_can_add_slots(participant):
            return self.env["appointment.poll.slot"]
        if start_utc not in self._slot_pool(participant):
            return self.env["appointment.poll.slot"]
        creneau = self.env["appointment.poll.slot"].create({
            "poll_id": self.id,
            "start": start_utc,
            "stop": start_utc + timedelta(hours=self.type_id.duration or 1.0),
            "proposed_by_id": participant.id,
        })
        # Proposer vaut « je peux » : personne ne coche une heure qui ne lui
        # convient pas. Sans ce vote, la plage naîtrait avec zéro appui et le
        # recoupement compterait un participant de moins que la réalité.
        self._register_yes(participant, creneau)
        if self.slot_source == "seeder" and not self.seeded_by_id:
            self.seeded_by_id = participant
        # La retenue suit la proposition, dans les deux modes où la plage naît
        # d'un participant : en « un invité amorce » parce que la grille est
        # définitive dès qu'elle est posée, en « chacun propose » parce que
        # c'est la sélection elle-même que l'organisateur a demandé à voir
        # tenue dans son agenda. Le recoupement ne s'en trouve pas empêché :
        # il se forme dans la GRILLE, que tout le monde continue de voter, et
        # non dans le bassin — d'où une plage retenue sort de toute façon,
        # retenue ou pas (`_slot_pool` écarte déjà les plages existantes).
        if self.hold_mode != "none" and self.slot_source in ("seeder", "open"):
            creneau._create_hold()
        return creneau

    # ------------------------------------------------------------------
    # Recoupement : ce que l'organisateur regarde pour trancher
    # ------------------------------------------------------------------

    def _ranked_slots(self):
        """Créneaux du meilleur au moins bon.

        L'ordre encode la décision : d'abord ce qui est encore viable, puis ce
        qui couvre tous les obligatoires, puis le nombre de « oui ». Un
        « si nécessaire » compte pour moitié — il porte une vraie information,
        mais pas la même qu'un franc oui.
        """
        self.ensure_one()
        def cle(creneau):
            return (
                creneau.is_viable,
                creneau.is_complete,
                creneau.yes_count + creneau.ifneedbe_count * 0.5,
                -(creneau.start.timestamp() if creneau.start else 0),
            )
        return self.slot_ids.sorted(key=cle, reverse=True)

    def action_hold_shortlist(self):
        """Repose la retenue sur les créneaux présélectionnés.

        Depuis 18.0.1.4.0, une plage choisie prend sa retenue toute seule : ce
        bouton n'est plus le seul chemin, il est le rattrapage. Il sert quand
        une retenue a été libérée par un « Non » qu'on est revenu corriger, et
        pour les sondages ouverts avant ce changement, dont la grille n'a
        jamais rien tenu.
        """
        self.ensure_one()
        if self.hold_mode == "none":
            raise UserError(_("No hold is requested on this poll."))
        retenus = self.slot_ids.filtered("is_shortlisted")
        if not retenus:
            raise UserError(_(
                "First check the slots to hold in the \"Proposed slots\" tab."
            ))
        retenus._create_hold()
        return True

    def scheduled_display(self, participant=None, en=False):
        """« jeudi 3 septembre · 17:30 – 18:00 (Montréal) », pour la confirmation.

        ⚠️ Rendu dans le fuseau du SONDAGE, celui que la page de vote affiche
        et étiquette. C'est dans ce fuseau que les gens ont lu les créneaux et
        coché : confirmer dans un autre les obligerait à refaire la conversion
        à l'envers. Un participant n'a d'ailleurs pas de fuseau à lui — seul un
        contact en a un, et un inscrit libre n'en est pas un.
        """
        self.ensure_one()
        if not self.booking_id:
            return ""
        creneau = self.slot_ids.filtered(
            lambda s: s.start == self.booking_id.start)[:1]
        if not creneau:
            # La rencontre a été déplacée depuis, ou le créneau retiré : on
            # rend l'heure de la RÉSERVATION, qui fait foi.
            creneau = self.env["appointment.poll.slot"].new({
                "poll_id": self.id,
                "start": self.booking_id.start,
                "stop": self.booking_id.stop,
            })
        # 🔴 Le fuseau du LECTEUR, pas celui de la session qui expédie. Sans
        # `with_context`, `_poll_tzname()` prend d'abord `env.context['tz']` :
        # un organisateur en Nouvelle-Zélande a ainsi envoyé des heures
        # d'Auckland à des participants de Montréal.
        tzname = (participant._display_tz() if participant
                  else self.env["bf.timezone"].resolve([
                      self.type_id.resource_calendar_id.tz
                      if self.type_id else None]))
        creneau = creneau.with_context(tz=tzname)
        return "%s · %s (%s)" % (creneau.display_day(en), creneau.display_time(),
                                 creneau.display_tz_label())

    def _pool_tzname(self):
        self.ensure_one()
        return self.env["bf.timezone"].resolve([
            self.env.context.get("tz"),
            self.type_id.resource_calendar_id.tz if self.type_id else None,
        ])

    def _pool_by_day(self, participant, en=False):
        """Bassin regroupé par journée locale, prêt pour la page publique.

        Rend {libellé du jour: [(valeur ISO, heure), …]}. La valeur ISO est ce
        que le formulaire repostera ; elle est revalidée contre le bassin à
        l'écriture, donc son contenu n'engage rien.

        Le regroupement par jour n'est pas décoratif : une colonne de soixante
        heures d'affilée est illisible, et c'est le premier endroit où un
        sondage perd ses répondants.
        """
        self.ensure_one()
        tz = pytz.timezone(self._pool_tzname())
        locale = "en_CA" if en else (
            (self.env.context.get("lang") or self.env.lang or "fr_CA").replace("-", "_")
        )
        try:
            from babel.dates import format_date
        except Exception:  # pragma: no cover
            format_date = None
        groupes = {}
        for utc in self._slot_pool(participant):
            local = pytz.utc.localize(utc).astimezone(tz)
            if format_date:
                jour = format_date(local.date(), format="EEEE d MMMM", locale=locale)
            else:
                jour = local.strftime("%Y-%m-%d")
            groupes.setdefault(jour, []).append(
                (fields.Datetime.to_string(utc), local.strftime("%H:%M"))
            )
        return groupes

    def _register_yes(self, participant, creneau):
        """Inscrit un « oui » sans écraser une réponse déjà donnée.

        Choisir une plage vaut « je peux » : personne ne coche une heure qui ne
        lui convient pas. Mais si la personne avait déjà répondu « si
        nécessaire » sur ce créneau, on ne la contredit pas — sa nuance est
        plus informative que notre déduction.
        """
        self.ensure_one()
        Vote = self.env["appointment.poll.vote"]
        existant = Vote.search([
            ("participant_id", "=", participant.id),
            ("slot_id", "=", creneau.id),
        ], limit=1)
        if existant:
            return existant
        return Vote.create({
            "participant_id": participant.id,
            "slot_id": creneau.id,
            "answer": "yes",
        })

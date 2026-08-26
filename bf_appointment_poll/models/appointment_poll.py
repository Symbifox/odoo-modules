# -*- coding: utf-8 -*-
"""Le sondage lui-même : un jeu de créneaux candidats soumis à un groupe."""

import logging
import secrets
from datetime import timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Fenêtre par défaut dans laquelle on va chercher les créneaux candidats.
_DEFAULT_HORIZON_DAYS = 21

# Plafond du bassin offert au choix. Trois semaines d'heures ouvrables
# dépassent vite la centaine de plages : au-delà, la page devient
# illisible et le taux de réponse s'effondre.
_POOL_MAX = 60


class AppointmentPoll(models.Model):
    _name = "appointment.poll"
    _description = "Sondage de disponibilités"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    name = fields.Char(
        string="Objet",
        required=True,
        tracking=True,
        help="Ce que les participants verront en titre du sondage.",
    )
    user_id = fields.Many2one(
        "res.users",
        string="Organisateur",
        required=True,
        default=lambda self: self.env.user,
        tracking=True,
        help="La personne dont le calendrier fournit les créneaux candidats "
             "et qui tranchera à la clôture.",
    )
    type_id = fields.Many2one(
        "resource.booking.type",
        string="Type de rendez-vous",
        required=True,
        tracking=True,
        help="Détermine la durée, le calendrier de disponibilité et tout ce "
             "qui suivra à la clôture : salle visio, rappels, gabarits.",
    )
    company_id = fields.Many2one(
        "res.company",
        string="Société",
        required=True,
        default=lambda self: self.env.company,
    )
    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("open", "En cours"),
            ("closed", "Clos"),
            ("scheduled", "Rencontre fixée"),
            ("cancelled", "Annulé"),
        ],
        string="État",
        default="draft",
        required=True,
        tracking=True,
    )
    description = fields.Html(
        string="Message aux participants",
        sanitize=True,
        help="Affiché en tête de la page de vote.",
    )

    slot_ids = fields.One2many(
        "appointment.poll.slot", "poll_id", string="Créneaux proposés"
    )
    participant_ids = fields.One2many(
        "appointment.poll.participant", "poll_id", string="Participants"
    )

    close_date = fields.Datetime(
        string="Date limite de réponse",
        tracking=True,
        help="Passé ce moment, le cron clôt le sondage et prévient "
             "l'organisateur. Vide = pas de clôture automatique.",
    )
    max_slots = fields.Integer(
        string="Nombre maximal de créneaux",
        default=8,
        help="Plafond du nombre de plages soumises au vote. Au-delà d'une "
             "dizaine, le taux de réponse s'effondre.",
    )
    slot_source = fields.Selection(
        [
            ("organizer", "Je propose les créneaux"),
            ("seeder", "Un invité amorce la grille"),
            ("open", "Chacun propose ses plages"),
        ],
        string="Qui propose les créneaux",
        default="organizer",
        required=True,
        tracking=True,
        help="« Je propose » : vous choisissez les plages, tout le monde y "
             "répond.\n"
             "« Un invité amorce » : la première grille vient d'un invité, les "
             "autres y répondent ensuite. Utile quand quelqu'un mène le "
             "dossier et connaît mieux que vous les contraintes du groupe.\n"
             "« Chacun propose » : chaque personne coche ce qui lui convient "
             "dans vos disponibilités, et le recoupement se forme tout seul. "
             "C'est le mode qui vous évite d'avoir à deviner les plages.",
    )
    seeder_participant_id = fields.Many2one(
        "appointment.poll.participant",
        string="Invité qui amorce",
        domain="[('poll_id', '=', id)]",
        help="Laisser vide pour que ce soit simplement la première personne "
             "qui répond. Désigner quelqu'un évite que le hasard de la boîte "
             "de réception décide qui cadre la rencontre.",
    )
    seeded_by_id = fields.Many2one(
        "appointment.poll.participant",
        string="Grille amorcée par",
        readonly=True,
        copy=False,
        help="Qui a effectivement posé la grille. Une fois renseigné, la "
             "grille est figée pour les répondants suivants.",
    )
    max_picks_per_participant = fields.Integer(
        string="Plages par personne",
        default=5,
        help="Plafond de plages qu'une même personne peut proposer. Sans lui, "
             "quelqu'un en coche trente et noie le recoupement.",
    )

    show_votes = fields.Boolean(
        string="Montrer les réponses de chacun",
        default=True,
        help="Affiche, sur la page de vote, qui a répondu quoi. C'est le "
             "comportement des sondages grand public, et il aide : voir qu'une "
             "plage rassemble déjà trois « oui » pousse à s'y rallier.\n\n"
             "À décocher quand les disponibilités des uns ne regardent pas les "
             "autres — un comité où les participants ne se connaissent pas, ou "
             "un sondage qui mêle des gens de plusieurs organisations. Chacun "
             "voit alors ses propres réponses, et rien d'autre.",
    )
    hold_mode = fields.Selection(
        [
            ("none", "Aucune retenue"),
            ("visible", "Visible dans mon agenda, sans bloquer"),
            ("blocking", "Réserver réellement les plages"),
        ],
        string="Retenue dans l'agenda",
        default="none",
        required=True,
        help="« Visible » pose un événement marqué disponible : vous voyez le "
             "sondage en cours dans votre agenda, et les réservations "
             "publiques passent quand même sur ces plages.\n"
             "« Réserver réellement » ferme les plages à toute autre "
             "réservation le temps du sondage. À garder pour les rencontres "
             "qu'on ne peut pas se permettre de perdre.\n\n"
             "La retenue se pose plage par plage, à mesure que quelqu'un la "
             "choisit, et se libère dès qu'une personne obligatoire y répond "
             "Non. En mode « réserver réellement », comptez donc que chaque "
             "plage choisie sort de votre page publique de rendez-vous le "
             "temps du sondage : c'est le plafond de plages qui borne "
             "l'effet.",
    )

    date_opened = fields.Datetime(
        string="Ouvert le",
        readonly=True,
        copy=False,
        help="Horodatage de l'ouverture du vote. Les relances se comptent à "
             "partir de LUI, jamais de `write_date` : ce dernier bouge au "
             "moindre changement (une correction de l'objet, un créneau "
             "ajouté), ce qui repousserait les relances indéfiniment sans que "
             "personne ne s'en aperçoive.",
    )
    booking_id = fields.Many2one(
        "resource.booking",
        string="Rendez-vous issu du sondage",
        readonly=True,
        copy=False,
        help="Créé à la clôture par le module parent. C'est lui qui porte "
             "l'événement d'agenda définitif, l'ICS et les rappels.",
    )
    access_token = fields.Char(
        string="Jeton du sondage",
        copy=False,
        groups="base.group_user",
        help="Jeton du sondage lui-même. Chaque participant a EN PLUS son "
             "propre jeton : c'est lui qui identifie la personne qui vote.",
    )

    # -- Compteurs ---------------------------------------------------------

    slot_count = fields.Integer(compute="_compute_counts")
    participant_count = fields.Integer(compute="_compute_counts")
    responded_count = fields.Integer(compute="_compute_counts")
    pending_required_count = fields.Integer(
        compute="_compute_counts",
        string="Obligatoires sans réponse",
    )
    viable_slot_count = fields.Integer(
        compute="_compute_counts",
        string="Créneaux encore viables",
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
                raise UserError(_("Un sondage clos ne prend plus de créneaux."))
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
            if not poll.participant_ids:
                raise UserError(_("Ajoutez au moins un participant."))
            if poll.slot_source == "organizer" and not poll.slot_ids:
                raise UserError(_("Proposez au moins un créneau avant d'ouvrir."))
            if poll.slot_source == "seeder" and poll.slot_ids:
                raise UserError(_(
                    "En mode « un invité amorce », la grille doit venir de "
                    "l'invité. Retirez les créneaux, ou repassez en « je "
                    "propose »."
                ))
            if (poll.slot_source == "seeder" and poll.seeder_participant_id
                    and poll.seeder_participant_id.poll_id != poll):
                raise UserError(_("L'invité qui amorce doit appartenir à ce sondage."))
            poll.state = "open"
            poll.date_opened = fields.Datetime.now()
            # Tient la grille déjà connue. En « chacun propose », elle est
            # normalement vide à l'ouverture et chaque plage prendra sa
            # retenue au moment où quelqu'un la choisit.
            if poll.hold_mode != "none":
                poll.slot_ids._create_hold()
            poll.participant_ids._send_invitation()
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
        """Version bouton : fixe la rencontre PUIS ouvre le rendez-vous créé.

        `action_schedule` rend un recordset, ce qu'un bouton de vue ne sait pas
        exploiter (le client web l'ignore et l'utilisateur reste sur place,
        sans savoir si quelque chose s'est passé).
        """
        self.ensure_one()
        self.action_schedule()
        return self.action_view_booking()

    def action_schedule(self, slot=None):
        """Fixe la rencontre sur un créneau et laisse le parent faire le reste.

        Tout ce qui suit — événement d'agenda, ICS, salle visio, rappels
        programmés — vient du module de rendez-vous. Le sondage ne duplique
        aucune de ces mécaniques : il choisit une heure et des personnes.
        """
        self.ensure_one()
        slot = slot or self.slot_ids.filtered("is_viable")[:1]
        if not slot:
            raise UserError(_("Aucun créneau viable à retenir."))
        if self.booking_id:
            raise UserError(_("Ce sondage a déjà donné lieu à un rendez-vous."))
        partners = self.participant_ids._ensure_partners()
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
        self.slot_ids._release_hold()
        self.booking_id = booking
        self.state = "scheduled"
        self.message_post(
            body=_("Rencontre fixée au créneau retenu à partir du sondage.")
        )
        return booking

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
            poll.action_close()
            poll.message_post(
                body=_("Date limite atteinte : le sondage est clos.")
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
                lignes.append((
                    autre.name or autre.email,
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
        # 🔴 La retenue suit la proposition dans les DEUX modes où la plage
        # naît d'un participant. En « chacun propose », elle n'était posée
        # nulle part avant la présélection : « réserver réellement » ne
        # réservait donc rien. Le recoupement ne s'en trouve pas empêché — il
        # se forme dans la GRILLE, que tout le monde continue de voter, et non
        # dans le bassin, d'où une plage retenue sort de toute façon.
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
        """Pose la retenue sur les créneaux retenus, en mode « chacun propose ».

        C'est le pendant du choix de conception : bloquer tout le bassin le
        viderait au fur et à mesure des choix, et aucun recoupement ne pourrait
        se former. On retient donc d'abord, on bloque ensuite.
        """
        self.ensure_one()
        if self.hold_mode == "none":
            raise UserError(_("Aucune retenue n'est demandée sur ce sondage."))
        retenus = self.slot_ids.filtered("is_shortlisted")
        if not retenus:
            raise UserError(_(
                "Cochez d'abord les créneaux à retenir dans l'onglet « Créneaux »."
            ))
        retenus._create_hold()
        return True

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

# -*- coding: utf-8 -*-
"""Les personnes consultées, et la distinction obligatoire / facultatif."""

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

import pytz

from odoo import _, api, fields, models  # noqa: F401

_logger = logging.getLogger(__name__)

# Rythme des relances, en jours après l'ouverture. Deux, puis on s'arrête.
_REMINDER_DAYS = (2, 5)

# Code à usage unique. Quinze minutes : assez pour aller chercher le courriel,
# trop court pour qu'un code oublié dans une boîte serve trois jours plus tard.
_OTP_TTL_MIN = 15
# Cinq essais, puis le code est brûlé. Six chiffres tirés au sort donnent une
# chance sur deux cent mille de tomber juste en cinq coups.
_OTP_MAX_ATTEMPTS = 5
# Une minute entre deux envois : de quoi renvoyer si le premier s'est perdu,
# sans faire du bouton un robinet à courriels.
_OTP_RESEND_SECONDS = 60


class AppointmentPollParticipant(models.Model):
    _name = "appointment.poll.participant"
    _description = "Participant à un sondage"
    _order = "required desc, id"

    poll_id = fields.Many2one(
        "appointment.poll",
        string="Sondage",
        required=True,
        ondelete="cascade",
        index=True,
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Contact",
        help="Optionnel : un participant peut n'être qu'une adresse. Le "
             "contact est créé au moment de fixer la rencontre, pas avant — "
             "un sondage sans suite ne doit pas laisser de fiches derrière lui.",
    )
    name = fields.Char(string="Nom")
    email = fields.Char(string="Courriel", required=True)
    tz = fields.Selection(
        lambda self: [(t, t) for t in sorted(pytz.all_timezones)],
        string="Fuseau horaire",
        help="Celui dans lequel cette personne lit les heures. Capté du "
             "navigateur quand elle passe par le lien, sinon repris de sa "
             "fiche de contact. À défaut, celui du calendrier de "
             "disponibilité, puis le fuseau par défaut réglé dans les "
             "Paramètres.",
    )
    required = fields.Boolean(
        string="Présence obligatoire",
        default=False,
        help="Un créneau cesse d'être viable dès qu'une personne obligatoire "
             "y répond Non. Les réponses des personnes facultatives comptent "
             "dans le décompte sans jamais écarter un créneau.",
    )

    self_signup = fields.Boolean(
        string="Inscrit par le lien",
        readonly=True,
        copy=False,
        help="La personne est entrée d'elle-même par le lien d'inscription, "
             "elle n'a pas été invitée nommément. Elle est facultative, et "
             "son identité ne tient qu'à l'adresse qu'elle a saisie : c'est "
             "pourquoi modifier des réponses déjà données lui demande un code.",
    )
    otp_hash = fields.Char(
        string="Empreinte du code",
        readonly=True,
        copy=False,
        groups="base.group_system",
        help="⚠️ Le code n'est jamais gardé en clair. On en stocke une "
             "empreinte signée avec le secret de la base et l'identifiant du "
             "participant : elle ne vaut rien ailleurs, et ne se remonte pas.",
    )
    otp_expires_at = fields.Datetime(
        string="Code valable jusqu'à", readonly=True, copy=False)
    otp_attempts = fields.Integer(
        string="Essais sur le code", default=0, readonly=True, copy=False)
    otp_sent_at = fields.Datetime(
        string="Code envoyé le", readonly=True, copy=False)
    invitation_sent_on = fields.Datetime(
        string="Invitation envoyée le",
        readonly=True,
        copy=False,
        help="Vide = cette personne n'a jamais reçu d'invitation du module. "
             "C'est ce que regarde le bouton « Envoyer les invitations » pour "
             "ne pas écrire deux fois à la même personne.",
    )
    vote_url = fields.Char(
        string="Lien de vote",
        compute="_compute_vote_url",
        groups="base.group_user",
        help="Le lien personnel de cette personne. Copiez-le pour l'inclure "
             "dans votre propre courriel, ou pour relancer quelqu'un de la "
             "main.",
    )

    access_token = fields.Char(
        string="Jeton",
        copy=False,
        groups="base.group_user",
        help="Identifie la personne qui vote. C'est le lien personnel envoyé "
             "dans l'invitation.",
    )
    vote_ids = fields.One2many(
        "appointment.poll.vote", "participant_id", string="Réponses"
    )
    proposed_slot_ids = fields.One2many(
        "appointment.poll.slot", "proposed_by_id", string="Plages proposées"
    )
    proposed_count = fields.Integer(
        compute="_compute_proposed_count", string="Nombre de plages proposées"
    )
    responded_at = fields.Datetime(string="A répondu le", readonly=True)
    reminder_count = fields.Integer(string="Relances envoyées", default=0, readonly=True)
    last_reminder_date = fields.Datetime(string="Dernière relance", readonly=True)

    _sql_constraints = [
        (
            "email_unique_per_poll",
            "UNIQUE(poll_id, email)",
            "Cette adresse est déjà invitée à ce sondage.",
        ),
    ]

    @api.depends("proposed_slot_ids")
    def _compute_proposed_count(self):
        for participant in self:
            participant.proposed_count = len(participant.proposed_slot_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("access_token"):
                vals["access_token"] = secrets.token_urlsafe(24)
            if not vals.get("name") and vals.get("email"):
                vals["name"] = vals["email"].split("@")[0]
        return super().create(vals_list)

    @api.depends("access_token")
    def _compute_vote_url(self):
        for participant in self:
            participant.vote_url = (
                participant._vote_url() if participant.access_token else False
            )

    def _vote_url(self):
        self.ensure_one()
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        return f"{base}/appointment/poll/{self.access_token}"

    def _display_tz(self):
        """Le fuseau dans lequel ÉCRIRE à cette personne.

        🔴 Ne consulte JAMAIS `env.context['tz']`. C'est là qu'était le défaut :
        le fuseau de la session qui déclenche l'envoi gagnait, et l'organisateur
        travaillant depuis la Nouvelle-Zélande a expédié des confirmations en
        heure d'Auckland à des gens de Montréal. Le lecteur d'un courriel n'est
        jamais celui qui l'expédie.

        L'ordre dit qui sait le mieux : la personne elle-même (captée de son
        navigateur), sa fiche de contact, le calendrier de disponibilité du
        type de rencontre, puis le fuseau par défaut des Paramètres.
        """
        self.ensure_one()
        return self.env["bf.timezone"].resolve([
            self.tz,
            self.partner_id.tz,
            self.poll_id.type_id.resource_calendar_id.tz
            if self.poll_id.type_id else None,
        ])

    def _remember_tz(self, tzid):
        """Retient le fuseau du navigateur, sans jamais écraser un choix connu.

        Le site pose un témoin `tz` avec `Intl.DateTimeFormat()`. On le prend
        au passage : c'est la seule occasion d'apprendre où lit vraiment
        quelqu'un qui n'a pas de fiche de contact.
        """
        for participant in self:
            if participant.tz or not tzid:
                continue
            propre = self.env["bf.timezone"].normalize_name(tzid)
            if propre and propre in pytz.all_timezones:
                participant.sudo().tz = propre
        return True

    def _set_tz(self, tzid):
        """Retient un fuseau CHOISI à l'écran.

        Contrairement à `_remember_tz`, celui-ci écrase : la personne vient de
        dire elle-même dans quel fuseau elle lit, ce qui vaut mieux que ce que
        son navigateur annonce et que ce que porte sa fiche de contact. Le
        choix suit donc jusqu'à la confirmation, qui partira dans le même
        fuseau que la page où elle a répondu.
        """
        self.ensure_one()
        propre = self.env["bf.timezone"].normalize_name(tzid or "")
        if not propre or propre not in pytz.all_timezones:
            return False
        self.sudo().tz = propre
        return True

    @api.model
    def _tz_choices(self, courant=None):
        """La courte liste offerte à l'écran, plus le fuseau en cours.

        ⚠️ Pas les six cents fuseaux de la base : une liste déroulante de cette
        taille ne se lit pas, et personne ne cherche « America/Argentina/Salta »
        pour répondre à un sondage. On offre ce qu'un sondage de Blue Fox
        traverse réellement, et on ajoute TOUJOURS le fuseau courant — sinon
        quelqu'un venu d'ailleurs ne se verrait pas dans sa propre liste.
        """
        usuels = [
            "America/Toronto", "America/Montreal", "America/Halifax",
            "America/Winnipeg", "America/Edmonton", "America/Vancouver",
            "America/New_York", "America/Chicago", "America/Denver",
            "America/Los_Angeles", "America/Mexico_City", "America/Sao_Paulo",
            "Europe/London", "Europe/Paris", "Europe/Brussels", "Europe/Madrid",
            "Europe/Zurich", "Europe/Lisbon", "Europe/Bucharest",
            "Africa/Casablanca", "Africa/Abidjan", "Asia/Dubai",
            "Asia/Kolkata", "Asia/Shanghai", "Asia/Tokyo",
            "Australia/Sydney", "Pacific/Auckland", "UTC",
        ]
        connus = [t for t in usuels if t in pytz.all_timezones]
        if courant and courant not in connus and courant in pytz.all_timezones:
            connus.insert(0, courant)
        villes = self.env["bf.timezone"]
        return [(t, villes.tz_city(t) or t) for t in connus]

    # ------------------------------------------------------------------
    # Modifier ses réponses : prouver qu'on tient l'adresse
    # ------------------------------------------------------------------

    def _edit_needs_otp(self):
        """Cette personne doit-elle prouver qu'elle tient l'adresse ?

        🔴 Les inscrits libres SEULEMENT, et seulement une fois qu'ils ont
        répondu. Leur identité ne tient qu'à une adresse saisie, et
        `_self_signup_join` est idempotent dessus : sans ce verrou, connaître
        le lien d'inscription et l'adresse de quelqu'un suffisait à voir ET à
        modifier ses réponses. C'était écrit dans le README comme une
        contrepartie assumée ; ça ne l'est plus.

        Les personnes invitées nommément gardent leur lien tel quel : il leur
        est parvenu par courriel, ce qui prouve déjà qu'elles contrôlent
        l'adresse. Et rien n'est protégé tant qu'il n'y a pas de réponse : on
        ne met pas un code devant une page vide.
        """
        self.ensure_one()
        return bool(self.self_signup and self.responded_at)

    def _otp_digest(self, code):
        """Empreinte liée AU participant, signée avec le secret de la base.

        L'identifiant entre dans l'empreinte : sans lui, une empreinte volée
        sur un participant vaudrait sur un autre à code égal.
        """
        self.ensure_one()
        cle = self.env["ir.config_parameter"].sudo().get_param("database.secret") or ""
        message = "%s:%s" % (self.id, (code or "").strip())
        return hmac.new(cle.encode(), message.encode(), hashlib.sha256).hexdigest()

    def _otp_issue(self):
        """Pose un code neuf et rend sa version EN CLAIR, pour le courriel seul.

        ⚠️ Le clair ne retourne jamais en base. Une table de participants se
        lit par bien des chemins, et un code en clair y vaudrait le droit de
        modifier les réponses de quelqu'un.
        """
        self.ensure_one()
        code = "%06d" % secrets.randbelow(1000000)
        self.sudo().write({
            "otp_hash": self._otp_digest(code),
            "otp_expires_at": fields.Datetime.now() + timedelta(minutes=_OTP_TTL_MIN),
            "otp_attempts": 0,
            "otp_sent_at": fields.Datetime.now(),
        })
        return code

    def _otp_can_resend(self):
        self.ensure_one()
        if not self.sudo().otp_sent_at:
            return True
        ecoule = fields.Datetime.now() - self.sudo().otp_sent_at
        return ecoule.total_seconds() >= _OTP_RESEND_SECONDS

    def _otp_check(self, code):
        """(ok, motif). Compte les essais, et brûle le code au succès.

        Les motifs sont nommés pour la page, pas pour l'attaquant : ils disent
        « expiré » ou « épuisé », jamais si l'adresse existe — le participant
        est déjà résolu par son jeton à ce stade, il n'y a rien à énumérer.
        """
        self.ensure_one()
        moi = self.sudo()
        if not moi.otp_hash or not moi.otp_expires_at:
            return False, "absent"
        if moi.otp_expires_at <= fields.Datetime.now():
            return False, "expire"
        if moi.otp_attempts >= _OTP_MAX_ATTEMPTS:
            return False, "brule"
        moi.otp_attempts += 1
        # ⚠️ Comparaison à temps constant : un `==` sur une chaîne courte laisse
        # fuir la position du premier caractère faux.
        if not hmac.compare_digest(moi.otp_hash, moi._otp_digest(code)):
            return False, "faux"
        moi.write({"otp_hash": False, "otp_expires_at": False, "otp_attempts": 0})
        return True, ""

    def _otp_send(self):
        """Expédie le code.

        ⚠️ C'est le SEUL courriel que ce module envoie vers une adresse entrée
        par le lien d'inscription, et le choix de n'en envoyer aucun était
        délibéré : une confirmation expédiée à une adresse que personne n'a
        validée ferait du lien un relais signé DKIM. Celui-ci ne part qu'à une
        adresse DÉJÀ inscrite au sondage — l'ensemble atteignable est donc
        borné par le sondage lui-même, pas par ce qu'un inconnu saisit.
        """
        self.ensure_one()
        gabarit = self.env.ref(
            "bf_appointment_poll.mail_template_poll_otp", raise_if_not_found=False)
        if not gabarit:
            _logger.warning("Sondage : gabarit de code introuvable, rien envoyé")
            return False
        code = self._otp_issue()
        try:
            # `force_send` parce qu'un code qui attend le cron de la file
            # arriverait après son expiration. ⚠️ Et l'envoi est enveloppé :
            # un SMTP qui hoquette ne doit ni renvoyer une page en erreur, ni
            # laisser la page annoncer un code qui n'est jamais parti.
            gabarit.sudo().with_context(bf_poll_otp=code).send_mail(
                self.id, force_send=True)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Sondage %s : envoi du code refusé pour %s (%s)",
                            self.poll_id.id, self.id, exc)
            return False
        _logger.info("Sondage %s : code envoyé au participant %s",
                     self.poll_id.id, self.id)
        return True

    def _ensure_partners(self):
        """Rend (en les créant au besoin) les contacts des participants.

        Appelé au moment de fixer la rencontre seulement : tant que le sondage
        n'a pas abouti, une simple adresse reste une simple adresse.
        """
        Partner = self.env["res.partner"].sudo()
        partners = Partner.browse()
        for participant in self:
            partner = participant.partner_id
            if not partner:
                partner = Partner.search(
                    [("email", "=ilike", participant.email)], limit=1
                )
            if not partner:
                # 🔴 `res.partner.tz` prend par défaut le fuseau de la SESSION
                # qui crée la fiche. Un organisateur travaillant depuis la
                # Nouvelle-Zélande fabriquait donc des contacts montréalais
                # estampillés « Pacific/Auckland » — et la fiche survit au
                # sondage, dans le carnet d'adresses, pour tous les courriels
                # qui suivront. On pose le fuseau qu'on connaît, à défaut celui
                # des Paramètres, jamais celui de qui clique.
                partner = Partner.create({
                    "name": participant.name or participant.email,
                    "email": participant.email,
                    "tz": participant._display_tz(),
                })
            participant.partner_id = partner
            partners |= partner
        return partners

    # -- Courriels ---------------------------------------------------------

    def _send_invitation(self):
        """Envoie l'invitation à voter, sous la marque du locataire.

        Le gabarit sort de `data/poll_mail_templates.xml` et suit la même
        mise en marque que les autres courriels du module de rendez-vous —
        c'est un livrable client, pas un envoi technique. Un gabarit absent
        (module partiellement désinstallé) est journalisé, pas fatal.
        """
        template = self.env.ref(
            "bf_appointment_poll.mail_template_poll_invitation",
            raise_if_not_found=False,
        )
        if not template:
            _logger.warning("Gabarit d'invitation au sondage introuvable")
            return False
        now = fields.Datetime.now()
        for participant in self:
            template.send_mail(participant.id, force_send=False)
            participant.invitation_sent_on = now
        return True

    def _send_scheduled_notice(self):
        """Annonce la rencontre fixée, sous la marque du locataire.

        🔴 Le sondage n'écrivait à PERSONNE en fixant la rencontre. La
        confirmation brandée du module parent existe, mais elle n'est envoyée
        que par la page publique de réservation, et elle s'adresse à un seul
        destinataire (`object.partner_id`). Faute de quoi il fallait attraper
        le bouton « Partager » d'Odoo, qui expédie un texte générique parlant
        d'« accéder au/à la resource booking » — à un client.

        Chaque personne reçoit son propre courriel, avec le fichier d'agenda :
        un `.ics` par envoi, pour que chacun garde le sien.
        """
        template = self.env.ref(
            "bf_appointment_poll.mail_template_poll_scheduled",
            raise_if_not_found=False,
        )
        if not template:
            _logger.warning("Gabarit de confirmation du sondage introuvable")
            return False
        for participant in self:
            booking = participant.poll_id.booking_id
            valeurs = {}
            if booking:
                piece = booking._get_ics_attachment()
                if piece:
                    valeurs["attachment_ids"] = [(6, 0, piece.ids)]
            try:
                template.send_mail(participant.id, force_send=False,
                                   email_values=valeurs or None)
            except Exception as exc:  # noqa: BLE001
                # ⚠️ Un envoi qui échoue ne doit pas défaire la rencontre : à
                # ce stade elle est fixée, l'agenda est écrit, et un rollback
                # la retirerait pour un courriel manqué.
                _logger.warning(
                    "Sondage %s : confirmation non partie pour %s (%s)",
                    participant.poll_id.id, participant.id, exc)
        return True

    def _send_reminders(self):
        """Relance les personnes sans réponse, deux fois au plus."""
        template = self.env.ref(
            "bf_appointment_poll.mail_template_poll_reminder",
            raise_if_not_found=False,
        )
        if not template:
            return False
        now = fields.Datetime.now()
        for participant in self:
            poll = participant.poll_id
            if poll.state != "open" or participant.responded_at:
                continue
            if participant.reminder_count >= len(_REMINDER_DAYS):
                continue
            opened = poll.date_opened
            if not opened:
                continue
            due_after = _REMINDER_DAYS[participant.reminder_count]
            if (now - opened).days < due_after:
                continue
            template.send_mail(participant.id, force_send=False)
            participant.write({
                "reminder_count": participant.reminder_count + 1,
                "last_reminder_date": now,
            })
        return True

    def _record_response(self):
        self.write({"responded_at": fields.Datetime.now()})
        return True

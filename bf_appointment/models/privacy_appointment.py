# -*- coding: utf-8 -*-
"""Pont vie privée de la prise de rendez-vous (2.66.0).

Le module dépendait déjà de `privacy_consent` pour les deux consentements du
formulaire public (enregistrement et infolettre). Ce qui n'était déclaré nulle
part, c'est la réservation elle-même : qui a rencontré qui, quand, ses
coordonnées, les réponses au questionnaire d'accueil et les invités.

Ce fichier pose :

1. la classification de `resource.booking` (point d'extension
   `_privacy_classifiable_models()` du socle) ;
2. une catégorie de RP par question d'accueil, réglée à la configuration ;
3. l'anonymisation d'une réservation échue, et le cron qui l'applique selon la
   règle RDV-001 du calendrier de conservation.

Règles retenues : 3 ans après la fin de la rencontre, puis
anonymisation ; réponses et invités suivent la même échéance ; purge allumée ;
la copie Nextcloud ou Google de l'événement reçoit la version anonymisée ; un
crochet laisse les satellites retirer ce qu'ils ont recopié ; la salle Talk
est renommée.
"""

import logging
import re
import threading
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Catégories qui font d'une question un renseignement sensible au sens de
# l'art. 12 LPRPSP (haut degré d'attente raisonnable en matière de vie
# privée). L'identification et « Autre » n'en sont pas.
SENSITIVE_PI_CATEGORIES = (
    "medical",
    "biometric",
    "criminal",
    "opinion_political",
    "ethnic_racial",
    "minor",
    "financial",
    "geolocation",
)

# Ce que l'anonymisation vide sur la réservation elle-même. Les noms servent
# aussi à l'entrée de registre (`res_field`), qui doit nommer ce qui est parti.
# `location` n'est pas vidé mais remis au lieu du TYPE (voir l'anonymisation) :
# pour un type en personne sans lieu fixe, il porte l'adresse tapée par le
# demandeur.
_BOOKING_FIELDS_CLEARED = (
    "description",
    "bf_public_title",
    "cancellation_reason",
    "videocall_location",
    "video_room_token",
    "bf_source_ref",
    "bf_visitor_tz",
    "access_token",
)

# Ce que l'anonymisation vide sur l'événement d'agenda, quand le champ existe
# (les trois derniers viennent de bf_calendar_invite, qui n'est pas une
# dépendance). `bf_change_baseline` garde en JSON l'ancien titre, l'ancien lieu
# et l'ancien lien que les invités connaissent : c'est la mémoire d'avant.
_MEETING_FIELDS_CLEARED = (
    "description",
    "location",
    "videocall_location",
    "bf_change_baseline",
    "bf_change_since",
    "bf_cancellation_reason",
)

_TALK_CALL_RE = re.compile(r"/call/([A-Za-z0-9]+)/?$")


def _pi_category_selection(self):
    """La liste du socle, lue au lieu d'être recopiée : une catégorie ajoutée
    à `privacy.document.classification` arrive ici sans rouvrir ce fichier."""
    return self.env["privacy.document.classification"]._fields[
        "pi_category"
    ].selection


def _in_test():
    return getattr(threading.current_thread(), "testing", False)


def _is_internal_partner(partner):
    return bool(partner.user_ids) and any(u._is_internal() for u in partner.user_ids)


class PrivacyDocumentClassification(models.Model):
    _inherit = "privacy.document.classification"

    def _privacy_classifiable_models(self):
        """Rend la réservation classifiable.

        ⚠️ Seule la réservation est ouverte, pas ses réponses d'accueil ni ses
        invités. Ils lui sont rattachés en `ondelete='cascade'` et n'ont aucun
        cycle de vie propre : les classifier à part fabriquerait des
        classifications orphelines à la destruction du parent. Même
        raisonnement que pour les présences et les décisions d'un compte
        rendu. Une classification sur la réservation couvre ses lignes.
        """
        return super()._privacy_classifiable_models() | {"resource.booking"}


class AppointmentIntakeField(models.Model):
    _inherit = "appointment.intake.field"

    bf_pi_category = fields.Selection(
        selection=_pi_category_selection,
        string="Catégorie de RP",
        compute="_compute_bf_pi_category",
        store=True,
        readonly=False,
        copy=True,
        help="Catégorie de renseignement personnel que la question recueille, "
             "choisie à la configuration. Une question laissée vide est "
             "signalée sur le type de rendez-vous : rien ne la déclare.",
    )
    bf_pi_sensitive = fields.Boolean(
        string="Renseignement sensible",
        compute="_compute_bf_pi_sensitive",
        store=True,
        help="Vrai pour une catégorie à haut degré d'attente raisonnable en "
             "matière de vie privée (santé, biométrie, antécédents, opinions, "
             "origine, mineur, finances, localisation).",
    )

    @api.depends("field_type")
    def _compute_bf_pi_category(self):
        """Pré-remplit SEULEMENT ce que le type de champ dit à coup sûr.

        Un champ « Courriel » ou « Téléphone » recueille un identifiant direct,
        quel que soit son libellé. Pour tout le reste, le libellé ne suffit
        pas à deviner (« Nature de la rencontre » peut appeler une question de
        santé) : la catégorie reste vide jusqu'à ce que quelqu'un la choisisse.
        Une catégorie déjà choisie n'est jamais réécrite.
        """
        for field in self:
            if not field.bf_pi_category and field.field_type in ("email", "phone"):
                field.bf_pi_category = "identification"
            else:
                field.bf_pi_category = field.bf_pi_category

    @api.depends("bf_pi_category")
    def _compute_bf_pi_sensitive(self):
        for field in self:
            field.bf_pi_sensitive = field.bf_pi_category in SENSITIVE_PI_CATEGORIES


class ResourceBookingType(models.Model):
    _inherit = "resource.booking.type"

    bf_intake_unclassified_count = fields.Integer(
        string="Questions sans catégorie de RP",
        compute="_compute_bf_intake_privacy",
    )
    bf_intake_sensitive_count = fields.Integer(
        string="Questions sensibles",
        compute="_compute_bf_intake_privacy",
    )

    @api.depends("intake_field_ids.bf_pi_category", "intake_field_ids.bf_pi_sensitive")
    def _compute_bf_intake_privacy(self):
        for booking_type in self:
            questions = booking_type.intake_field_ids
            booking_type.bf_intake_unclassified_count = len(
                questions.filtered(lambda q: not q.bf_pi_category)
            )
            booking_type.bf_intake_sensitive_count = len(
                questions.filtered("bf_pi_sensitive")
            )


class ResPartner(models.Model):
    """Protège le contact « Personne anonymisée ».

    Il porte toutes les réservations anonymisées : le supprimer viderait leur
    liste de participants (champ obligatoire), l'archiver la viderait à la
    lecture, et le fusionner dans un vrai contact rattacherait toutes ces
    réservations à une personne réelle.
    """

    _inherit = "res.partner"

    def _bf_is_appointment_placeholder(self):
        placeholder = self.env.ref(
            "bf_appointment.partner_appointment_anonymized",
            raise_if_not_found=False,
        )
        return bool(placeholder) and placeholder in self

    @api.ondelete(at_uninstall=False)
    def _bf_unlink_except_appointment_placeholder(self):
        if self._bf_is_appointment_placeholder():
            raise UserError(_(
                "Le contact « Personne anonymisée » porte les rendez-vous "
                "anonymisés : il ne peut pas être supprimé."
            ))

    def write(self, vals):
        if "active" in vals and not vals["active"] and self._bf_is_appointment_placeholder():
            raise UserError(_(
                "Le contact « Personne anonymisée » porte les rendez-vous "
                "anonymisés : il ne peut pas être archivé."
            ))
        return super().write(vals)


class BasePartnerMergeAutomaticWizard(models.TransientModel):
    _inherit = "base.partner.merge.automatic.wizard"

    def _merge(self, partner_ids, dst_partner=None, extra_checks=True):
        partners = self.env["res.partner"].browse(partner_ids)
        if (partners | (dst_partner or self.env["res.partner"]))._bf_is_appointment_placeholder():
            raise UserError(_(
                "Le contact « Personne anonymisée » porte les rendez-vous "
                "anonymisés : il ne se fusionne avec aucun autre contact."
            ))
        return super()._merge(partner_ids, dst_partner=dst_partner, extra_checks=extra_checks)


class ResourceBooking(models.Model):
    _inherit = "resource.booking"

    bf_privacy_anonymized_at = fields.Datetime(
        string="Anonymisée le",
        readonly=True,
        copy=False,
        index=True,
        help="Date à laquelle la règle RDV-001 a retiré de cette réservation "
             "ce qui identifiait les personnes. Restent le type, l'organisateur, "
             "les ressources, la date, la durée et l'état.",
    )

    def _bf_privacy_guard_anonymized_at(self, vals_list):
        """Seule l'anonymisation pose ou retire la date d'anonymisation.

        Sans cette garde, n'importe quel gestionnaire pourrait, par RPC, poser
        une date (la réservation échappe au cron pour toujours et affiche un
        bandeau mensonger) ou la vider (deuxième anonymisation, deuxième
        entrée au registre).

        🔴 Le drapeau de contexte ne suffit pas : le contexte d'un appel RPC
        appartient à l'appelant, qui peut le poser. On exige aussi `su`, qu'un
        appel RPC n'obtient jamais et que `_bf_privacy_anonymize` a déjà.

        Sous le drapeau posé par une demande de destruction « anonymiser »,
        l'archivage d'une réservation lève aussi (voir
        `PrivacyDestructionRequest`).
        """
        allowed = self.env.su and self.env.context.get("bf_privacy_anonymizing")
        if allowed:
            return
        if any("bf_privacy_anonymized_at" in vals for vals in vals_list):
            raise UserError(_(
                "La date d'anonymisation d'un rendez-vous ne se modifie pas : "
                "seule la règle de conservation RDV-001 la pose."
            ))
        if self.env.context.get("bf_appointment_refuse_generic_anonymize") and any(
            vals.get("active") is False for vals in vals_list
        ):
            raise UserError(_(
                "Une demande de destruction ne sait pas anonymiser un "
                "rendez-vous : elle l'archiverait sans rien retirer, puis "
                "inscrirait « anonymisation » au registre. L'anonymisation "
                "passe par le cron de la règle RDV-001 ; pour retirer la "
                "réservation tout de suite, choisissez « Supprimer »."
            ))

    @api.model_create_multi
    def create(self, vals_list):
        self._bf_privacy_guard_anonymized_at(vals_list)
        return super().create(vals_list)

    def write(self, vals):
        self._bf_privacy_guard_anonymized_at([vals])
        return super().write(vals)

    # ------------------------------------------------------------------
    # Le cron
    # ------------------------------------------------------------------

    @api.model
    def _bf_privacy_retention_rule(self):
        return self.env.ref(
            "bf_appointment.retention_appointment_booking",
            raise_if_not_found=False,
        )

    @api.model
    def _cron_bf_privacy_anonymize(self, limit=200):
        """Anonymise les réservations échues selon la règle RDV-001.

        ⚠️ La durée n'est pas écrite ici : elle est LUE sur la règle. Changer
        la règle change le comportement, et une règle archivée arrête tout.

        Échéance : la fin de la rencontre (`stop`). Une demande jamais placée
        à l'agenda n'a pas de fin ; son échéance court alors de sa création.

        Une réservation à la fois, chacune dans son point de reprise, et
        validée aussitôt : l'anonymisation, ses poussées vers Nextcloud et son
        entrée de registre réussissent ensemble ou pas du tout, et une limite
        de temps du cron n'annule pas ce qui est déjà fait (les poussées, elles,
        ne s'annulent pas). Une réservation en échec reste intacte, est
        journalisée, et repasse la nuit suivante sans bloquer les autres.
        """
        rule = self._bf_privacy_retention_rule()
        if not rule or not rule.active:
            _logger.warning(
                "bf_appointment: règle RDV-001 introuvable ou archivée, "
                "anonymisation des réservations annulée"
            )
            return 0
        days = rule.total_retention_days
        if days <= 0:
            _logger.warning(
                "bf_appointment: RDV-001 est à %s jour(s), anonymisation "
                "annulée plutôt que d'effacer les rendez-vous du jour", days,
            )
            return 0
        cutoff = fields.Datetime.now() - timedelta(days=days)
        due = self.sudo().with_context(active_test=False).search(
            [
                ("bf_privacy_anonymized_at", "=", False),
                "|",
                ("stop", "<=", cutoff),
                "&", ("stop", "=", False), ("create_date", "<=", cutoff),
            ],
            limit=limit,
            order="stop asc, id asc",
        )
        reason = _("règle de conservation RDV-001 (%s jours)", days)
        done = failed = 0
        for booking in due:
            try:
                with self.env.cr.savepoint():
                    if booking._bf_privacy_anonymize(reason=reason, rule=rule):
                        done += 1
            except Exception:  # noqa: BLE001 — une réservation n'arrête pas les autres
                failed += 1
                _logger.exception(
                    "bf_appointment: anonymisation de la réservation %s en "
                    "échec, laissée intacte et reprise au prochain passage",
                    booking.id,
                )
            if not _in_test():
                self.env.cr.commit()
        if done or failed:
            log = _logger.error if failed else _logger.info
            log(
                "bf_appointment: RDV-001, %s réservation(s) anonymisée(s), "
                "%s en échec", done, failed,
            )
        return done

    # ------------------------------------------------------------------
    # L'anonymisation
    # ------------------------------------------------------------------

    def _bf_privacy_anonymize(self, reason=None, rule=None):
        """Retire de la réservation tout ce qui identifie les personnes.

        Rend True quand quelque chose a été fait, False si la réservation était
        déjà anonymisée (rien à inscrire : une entrée pour une destruction qui
        n'a pas eu lieu serait fausse, et le registre ne se corrige pas).

        Ce qui part :
        - ce que les satellites ont recopié (`_bf_privacy_anonymize_extra`) ;
        - les réponses au questionnaire d'accueil et les invités, avec les
          courriels envoyés aux invités ;
        - les participants, remplacés par le contact « Personne anonymisée »
          (le champ est obligatoire chez l'OCA), qui reprend l'état de réponse
          du demandeur : une réservation confirmée le reste ;
        - le titre, la description, le motif d'annulation, le lien de visio,
          les jetons et la provenance ;
        - le fil de la réservation et de son événement d'agenda (courriels,
          notes, suivi des modifications), leurs pièces jointes, évaluations,
          activités, abonnés et le suivi des courriels ;
        - sur l'événement : participants externes, description, lieux, et la
          mémoire d'avant de bf_calendar_invite ;
        - la copie Nextcloud ou Google de l'événement reçoit la version
          anonymisée ; la salle Talk est renommée.

        Restent : le type, l'organisateur, les ressources, la date, la durée
        et l'état. C'est ce qui sert aux statistiques.

        🔴 Les écritures se font sans suivi ni notification, et sans la
        mémoire d'avant de bf_calendar_invite. Sans cela, le suivi écrirait
        « Ancien nom → Personne anonymisée » dans le fil, l'agenda inviterait
        le contact anonyme, et l'ancien titre resterait en JSON sur
        l'événement.

        🔴 Les appels sortants (Nextcloud, Google, Talk) viennent APRÈS les
        écritures en base et lèvent sur échec : la transaction est alors
        annulée et la réservation repasse la nuit suivante. Ces appels sont
        idempotents, une reprise ne fait pas de dégât.
        """
        self.ensure_one()
        if self.bf_privacy_anonymized_at:
            return False
        rule = rule or self._bf_privacy_retention_rule()
        booking = self.sudo().with_context(
            tracking_disable=True,
            mail_notrack=True,
            mail_create_nolog=True,
            mail_auto_subscribe_no_notify=True,
            no_mail_to_attendees=True,
            dont_notify=True,
            bf_ics_skip_bump=True,
            skip_nc_sync=True,
            skip_google_sync=True,
            bf_privacy_anonymizing=True,
        )
        meeting = booking.meeting_id

        # L'état est figé AVANT d'écrire : après coup, plus rien ne distingue
        # une réservation qu'on vient de vider d'une qui n'a jamais rien porté.
        snapshot = booking._bf_privacy_snapshot()
        talk_token = booking._bf_privacy_talk_token()
        requester_state = booking._bf_privacy_requester_state()
        placeholder = booking._bf_privacy_placeholder_partner()
        new_name = booking._bf_privacy_anonymized_name()

        # Les satellites passent d'abord : ils lisent encore le demandeur et
        # les réponses qu'ils ont recopiés.
        extra = booking._bf_privacy_anonymize_extra()

        guests = booking.guest_ids
        booking._bf_privacy_drop_messages("resource.booking.guest", guests.ids)
        booking.intake_answer_ids.unlink()
        guests.unlink()

        vals = {name: False for name in _BOOKING_FIELDS_CLEARED if name in booking._fields}
        vals.update({
            "name": new_name,
            "partner_ids": [(6, 0, placeholder.ids)],
            "location": booking.type_id.location or False,
            "bf_privacy_anonymized_at": fields.Datetime.now(),
        })
        booking.write(vals)

        if meeting:
            meeting = meeting.with_context(booking.env.context)
            internal = meeting.partner_ids.filtered(_is_internal_partner)
            meeting_vals = {name: False for name in _MEETING_FIELDS_CLEARED}
            meeting_vals.update({
                "partner_ids": [(6, 0, (internal | placeholder).ids)],
                "name": new_name,
            })
            meeting.write({k: v for k, v in meeting_vals.items() if k in meeting._fields})
            if requester_state:
                meeting.attendee_ids.filtered(
                    lambda a: a.partner_id == placeholder
                ).write({"state": requester_state})
            # Le cache de la réservation ne voit pas l'état changer sur
            # l'événement sans qu'on le lui dise.
            booking.invalidate_recordset(["state"])

        booking._bf_privacy_drop_thread(booking)
        if meeting:
            booking._bf_privacy_drop_thread(meeting)

        notes = list(extra)
        if meeting:
            notes.append(booking._bf_privacy_push_remote(meeting))
        if talk_token:
            notes.append(booking._bf_privacy_rename_talk_room(talk_token, new_name))
        booking._bf_privacy_neutralize_classifications(new_name)

        booking._bf_privacy_register(snapshot, rule=rule, reason=reason, notes=notes)
        return True

    def _bf_privacy_anonymize_extra(self):
        """Crochet des satellites : retirer ce qu'ils ont recopié de la
        réservation. Appelé AVANT que le demandeur, les réponses et les invités
        partent, pour qu'ils puissent encore les lire.

        Rend une liste de phrases courtes, reprises telles quelles dans
        l'entrée de registre (« ordre du jour n° 12 : sujet semé retiré »).
        Un satellite relaie toujours à `super()`.
        """
        self.ensure_one()
        return []

    def _bf_privacy_snapshot(self):
        """Ce que l'entrée de registre dira avoir détruit, mesuré avant."""
        self.ensure_one()
        answers = self.intake_answer_ids
        categories = {"identification"}
        categories.update(a for a in answers.mapped("field_id.bf_pi_category") if a)
        unclassified = len(answers.filtered(lambda a: not a.field_id.bf_pi_category))
        # Un invité confirmé est déjà parmi les participants : on compte des
        # personnes, pas des lignes.
        partners = self.partner_ids.filtered(lambda p: not _is_internal_partner(p))
        partners |= self.guest_ids.mapped("partner_id")
        loose_emails = {
            (g.email or "").strip().lower()
            for g in self.guest_ids.filtered(lambda g: not g.partner_id)
        } - {(p.email or "").strip().lower() for p in partners}
        messages = self.env["mail.message"].sudo().search_count([
            ("model", "=", self._name), ("res_id", "=", self.id),
        ])
        return {
            "answers": len(answers),
            "answers_unclassified": unclassified,
            "guests": len(self.guest_ids),
            "subjects": len(partners) + len(loose_emails - {""}),
            "categories": sorted(categories),
            "messages": messages,
            "type": self.type_id.display_name or "",
            "date": self.start and fields.Date.to_string(self.start.date()) or "",
            "company": self.type_id.company_id or self.env.ref("base.main_company"),
        }

    def _bf_privacy_requester_state(self):
        """L'état de réponse du demandeur sur l'événement : c'est lui que
        l'OCA lit pour dire « confirmée »."""
        self.ensure_one()
        for attendee in self.meeting_id.attendee_ids:
            if attendee.partner_id in self.partner_ids:
                return attendee.state
        return False

    def _bf_privacy_anonymized_name(self):
        """Nom sans personne : le type et le numéro, rien d'autre."""
        self.ensure_one()
        return _(
            "%(type)s — rendez-vous anonymisé n° %(id)s",
            type=self.type_id.name or _("Rendez-vous"),
            id=self.id,
        )

    @api.model
    def _bf_privacy_placeholder_partner(self):
        partner = self.env.ref(
            "bf_appointment.partner_appointment_anonymized",
            raise_if_not_found=False,
        )
        if not partner or not partner.active:
            raise UserError(_(
                "Le contact « Personne anonymisée » de bf_appointment est "
                "introuvable ou archivé : l'anonymisation ne peut pas "
                "remplacer les participants."
            ))
        return partner.sudo()

    @api.model
    def _bf_privacy_drop_messages(self, model, res_ids):
        """Supprime les messages d'enregistrements, et leur suivi de courriel
        (mail_tracking garde l'objet, le destinataire et les ouvertures quand
        le message part)."""
        if not res_ids:
            return
        messages = self.env["mail.message"].sudo().search([
            ("model", "=", model), ("res_id", "in", list(res_ids)),
        ])
        if not messages:
            return
        if "mail.tracking.email" in self.env:
            Tracking = self.env["mail.tracking.email"].sudo()
            domain = [("mail_message_id", "in", messages.ids)]
            if "mail_id" in Tracking._fields:
                mails = self.env["mail.mail"].sudo().search(
                    [("mail_message_id", "in", messages.ids)])
                domain = ["|", ("mail_id", "in", mails.ids)] + domain
            Tracking.search(domain).unlink()
        messages.unlink()

    @api.model
    def _bf_privacy_drop_thread(self, record):
        """Supprime le fil d'un enregistrement : messages et leur suivi,
        pièces jointes, évaluations, activités, abonnés. Les courriels en file
        (`mail.mail`) suivent leur message en cascade."""
        env = self.env
        domain = [("res_model", "=", record._name), ("res_id", "=", record.id)]
        self._bf_privacy_drop_messages(record._name, [record.id])
        env["ir.attachment"].sudo().search(domain).unlink()
        if "rating.rating" in env:
            env["rating.rating"].sudo().search(domain).unlink()
        env["mail.activity"].sudo().search(domain).unlink()
        env["mail.followers"].sudo().search(domain).unlink()

    def _bf_privacy_neutralize_classifications(self, new_name):
        """Une classification de la réservation garde le titre d'avant et la
        personne visée : on les retire et on l'archive."""
        self.ensure_one()
        classifications = self.env["privacy.document.classification"].sudo().with_context(
            active_test=False
        ).search([("res_model", "=", self._name), ("res_id", "=", self.id)])
        if not classifications:
            return
        vals = {"res_name": new_name, "active": False}
        if "subject_partner_id" in classifications._fields:
            vals["subject_partner_id"] = False
        classifications.write(vals)

    # ------------------------------------------------------------------
    # Hors d'Odoo : agenda distant et salle Talk
    # ------------------------------------------------------------------

    def _bf_privacy_push_remote(self, meeting):
        """Pousse la version anonymisée vers la copie Nextcloud ou Google.

        La synchro de `calendar_nextcloud_sync` saute volontairement les
        événements venus de Nextcloud ou de Google (anti-boucle) : sur ceux-là,
        une écriture ordinaire ne part jamais. On pousse donc soi-même, en
        levant sur échec. Seules les copies DÉJÀ poussées sont mises à jour :
        un événement jamais envoyé ne doit pas naître ailleurs trois ans après.

        Rend la phrase de l'entrée de registre.
        """
        self.ensure_one()
        if "x_nc_calendar_id" not in meeting._fields or not meeting.x_nc_calendar_id:
            return _("événement d'agenda sans copie Nextcloud ni Google")
        config = meeting.x_nc_calendar_id.sudo()
        if config.backend_type == "google":
            if not meeting.x_nc_uid:
                return _("événement jamais poussé vers Google")
            # 🔴 Le corps standard omet `attendees` quand la liste est vide, et
            # un PATCH sans la clé laisse la liste Google telle quelle : le
            # courriel du demandeur y resterait. On l'envoie explicitement.
            backend = self.env["calendar.google.backend"]
            body = backend._odoo_to_google_vals(meeting, owner=config.calendar_owner_id)
            body.setdefault("attendees", [])
            service = backend._build_service(config.calendar_owner_id)
            resp = service.events().patch(
                calendarId=config.google_calendar_id or "primary",
                eventId=meeting.x_nc_uid,
                body=body,
                sendUpdates="none",
            ).execute()
            meeting.with_context(skip_nc_sync=True, skip_google_sync=True).write({
                "x_caldav_etag": resp.get("etag"),
                "x_last_sync": fields.Datetime.now(),
            })
            return _("copie Google de l'événement mise à jour, participants compris")
        if not meeting.x_caldav_href:
            return _("événement jamais poussé vers Nextcloud")
        if not config.nextcloud_app_password_encrypted:
            raise UserError(_(
                "Agenda Nextcloud « %s » sans identifiants : la copie de "
                "l'événement ne peut pas recevoir l'anonymisation.",
                config.display_name,
            ))
        meeting._push_caldav("update", meeting, config)
        return _("copie Nextcloud de l'événement mise à jour")

    def _bf_privacy_talk_token(self):
        """Le jeton de la salle Talk PROPRE à cette réservation, lu dans le
        lien de visio avant qu'on le vide. Jamais la salle de repli partagée,
        ni le lien fixe du type."""
        self.ensure_one()
        url = (self.videocall_location or "").strip()
        if not url:
            return False
        if "video_fallback_active" in self._fields and self.video_fallback_active:
            return False
        if url == (self.type_id.videocall_location or "").strip():
            return False
        base = (self.env["ir.config_parameter"].sudo()
                .get_param("bf_appointment.nc_talk_base_url") or "").rstrip("/")
        if not base or not url.startswith(base + "/"):
            return False
        match = _TALK_CALL_RE.search(url)
        return match.group(1) if match else False

    def _bf_privacy_rename_talk_room(self, token, new_name):
        """Renomme la salle Talk : son nom reprenait le titre de la rencontre.

        Salle déjà absente (404) = rien à renommer, et ce n'est pas un échec.
        Toute autre erreur lève : la réservation repasse la nuit suivante.
        """
        import requests

        ICP = self.env["ir.config_parameter"].sudo()
        base = (ICP.get_param("bf_appointment.nc_talk_base_url") or "").rstrip("/")
        # ⚠️ Pas de variable locale `user` : `_()` lit les variables du cadre
        # appelant pour trouver l'usager, et prendrait ce texte pour un uid.
        talk_login = ICP.get_param("bf_appointment.nc_talk_user")
        password_enc = ICP.get_param("bf_appointment.nc_talk_password_encrypted")
        password = password_enc and self._decrypt_nc_talk_password(password_enc)
        if not (base and talk_login and password):
            raise UserError(_(
                "Nextcloud Talk n'est plus configuré : la salle %s ne peut pas "
                "être renommée.", token,
            ))
        response = requests.put(
            "%s/ocs/v2.php/apps/spreed/api/v4/room/%s" % (base, token),
            data={"roomName": new_name[:200]},
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
            auth=(talk_login, password),
            timeout=20,
        )
        if response.status_code == 404:
            return _("salle Talk déjà absente")
        response.raise_for_status()
        return _("salle Talk renommée")

    # ------------------------------------------------------------------
    # Le registre
    # ------------------------------------------------------------------

    def _bf_privacy_register(self, snapshot, rule=None, reason=None, notes=None):
        """Écrit au registre immuable ce que l'anonymisation vient de retirer.

        `res_name` ne porte que le nom anonymisé : y recopier l'ancien titre
        reconstituerait, dans un registre qu'on ne peut ni modifier ni
        supprimer, le nom qu'on vient d'effacer.
        """
        self.ensure_one()
        description = _(
            "Réservation n° %(id)s (%(type)s, %(date)s) anonymisée. "
            "Retirés : participants (remplacés par « Personne anonymisée »), "
            "titre, description, lien de visio, jetons, provenance, "
            "%(answers)s réponse(s) au questionnaire d'accueil, %(guests)s "
            "invité(s) et leurs courriels, %(messages)s message(s) du fil ; "
            "sur l'événement d'agenda, participants externes, description, "
            "lieux et fil.\n"
            "Conservés : type, organisateur, ressources, date, durée, état.\n"
            "Non visés : les fiches de contact du demandeur, des invités "
            "confirmés et des participants d'un sondage, et leurs consentements "
            "(régimes propres ; un consentement nomme le numéro de la "
            "réservation) ; le compte rendu et l'ordre du jour de la rencontre "
            "(règle RENC-001), hors ce que les satellites retirent ci-dessous ; "
            "les visites et les touches de prospection liées (modules "
            "satellites) ; la conversation Talk, ses messages et ses invités "
            "(la salle est seulement renommée) ; le lien court de visio ; les "
            "étiquettes de l'événement ; les corbeilles et historiques de "
            "Nextcloud et de Google ; les sauvegardes, jusqu'à leur rotation ; "
            "les copies des courriels dans les boîtes de messagerie ; les "
            "journaux d'envoi de SMS chez le fournisseur.",
            id=self.id,
            type=snapshot["type"] or _("sans type"),
            date=snapshot["date"] or _("jamais placée à l'agenda"),
            answers=snapshot["answers"],
            guests=snapshot["guests"],
            messages=snapshot["messages"],
        )
        if notes:
            description += _("\nFait aussi : %s.", " ; ".join(n for n in notes if n))
        if snapshot["answers_unclassified"]:
            description += _(
                "\n%s réponse(s) venaient de questions sans catégorie de RP.",
                snapshot["answers_unclassified"],
            )
        if reason:
            description += _("\nMotif : %s", reason)
        labels = dict(_pi_category_selection(self))
        operator = self.env.user
        vals = {
            "destruction_date": fields.Datetime.now(),
            "destroyed_by_id": operator.id,
            "approved_by_id": operator.id,
            "res_model": self._name,
            "res_id": self.id,
            "res_name": self.name,
            "document_description": description,
            "pi_categories": ", ".join(labels.get(c, c) for c in snapshot["categories"]),
            "subject_count": snapshot["subjects"],
            "destruction_method": "anonymize",
            "destruction_scope": "partial",
            "res_field": ", ".join(
                ("partner_ids", "name", "location", "intake_answer_ids", "guest_ids")
                + _BOOKING_FIELDS_CLEARED
                + ("message_ids",)
            ),
            "company_id": snapshot["company"].id,
        }
        if rule:
            vals["retention_calendar_id"] = rule.id
            vals["legal_basis"] = rule.legal_basis
        self.env["privacy.destruction.register"].sudo().create(vals)


class PrivacyDestructionRequest(models.Model):
    """La demande de destruction (effacement, document) passe par la primitive
    générique « anonymiser » du socle, qui archive la réservation sans rien
    retirer, puis inscrit « anonymisation ». Sous ce drapeau, l'archivage
    d'une réservation lève : le socle range alors le document parmi les
    « non détruits », avec le motif, et ne certifie rien."""

    _inherit = "privacy.destruction.request"

    def _execute_document_destruction(self, method):
        if method == "anonymize":
            self = self.with_context(bf_appointment_refuse_generic_anonymize=True)
        return super()._execute_document_destruction(method)


class PrivacyDestructionCampaignLine(models.Model):
    """Refuse l'anonymisation d'une réservation par campagne de destruction.

    🔴 La campagne écrit sa propre entrée APRÈS l'exécution : portée
    « entière », `res_name` figé au classement (l'ancien titre, donc le nom de
    la personne), « Document : <ancien titre> ». Le registre est immuable : une
    seule campagne laisserait une certification fausse ET nominative. Le seul
    moyen d'empêcher l'inscription est de lever, comme le fait le pont des
    comptes rendus pour sa règle sur les transcriptions. Le chemin sanctionné écrit sa propre entrée juste : le cron
    de la règle RDV-001.

    Les autres méthodes (suppression, effacement, manuel) et les autres modèles
    passent au socle : il faut toujours relayer à `super()`, cinq ponts
    cohabitent par ce relais.
    """

    _inherit = "privacy.destruction.campaign.line"

    def _execute_destruction(self):
        self.ensure_one()
        if self.res_model != "resource.booking" or self.destruction_method != "anonymize":
            return super()._execute_destruction()
        raise UserError(_(
            "Une campagne ne sait pas consigner l'anonymisation d'un "
            "rendez-vous : elle inscrirait au registre immuable une "
            "destruction « entière » sous l'ancien titre, donc sous le nom de "
            "la personne.\n\n"
            "L'anonymisation passe par le cron « Rendez-vous : anonymisation "
            "des réservations échues (RDV-001) », qui écrit une entrée exacte. "
            "Pour supprimer la réservation, choisissez la méthode "
            "« Supprimer »."
        ))

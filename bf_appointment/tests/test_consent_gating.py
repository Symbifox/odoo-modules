# -*- coding: utf-8 -*-
"""Consentements sur les chemins qui contournent le formulaire d'accueil (2.49.0).

Le défaut couvert ici est un défaut d'ABSENCE : une réservation naissait,
se confirmait et produisait une rencontre enregistrable sans qu'aucun
consentement ait été ni vérifié ni demandé. Les essais portent donc autant
sur ce qui doit se produire que sur ce qui ne doit PAS se produire — un lot
qui ne vérifie que les cas positifs laisserait passer exactement le même
trou une porte plus loin.
"""

from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("bf_appointment", "bf_appointment_consent")
class TestConsentGating(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(
            cls.env.context, tracking_disable=True, tz="UTC"))
        attendances = [
            Command.create({
                "name": "j%s" % d, "dayofweek": str(d), "hour_from": 0.0,
                "hour_to": 24.0, "day_period": "morning",
            })
            for d in range(7)
        ]
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "24/7 consentement", "attendance_ids": attendances, "tz": "UTC"})
        cls.resource = cls.env["resource.resource"].create({
            "name": "consent material", "calendar_id": cls.calendar.id,
            "resource_type": "material", "tz": "UTC"})
        cls.combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([cls.resource.id])]})
        cls.notice = cls.env.ref("privacy_consent.notice_recording")
        cls.purpose = cls.env.ref("privacy_consent.purpose_recording")
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Type consentement", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id, "is_public": True,
            "listed_on_landing": False,
            "requires_recording_consent": True,
            "recording_notice_id": cls.notice.id,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": cls.combination.id})],
        })
        cls.type_sans_consentement = cls.booking_type.copy({
            "name": "Type sans enregistrement",
            "requires_recording_consent": False,
            "recording_notice_id": False,
        })
        cls.destinataire = cls.env["res.partner"].create({
            "name": "Destinataire consentement", "email": "consent@test.invalid"})
        # Les envois automatiques sont FERMÉS par défaut : un courriel à un
        # client ne part pas parce qu'un module vient d'être installé. La suite
        # ouvre l'interrupteur pour éprouver le chemin, et un essai dédié
        # vérifie qu'il ferme bien.
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_appointment.consent_auto_request", "1")

    # -- Outillage ----------------------------------------------------------

    def _lien(self, booking_type=None, partner=None):
        """Une réservation en attente porteuse d'un lien personnel."""
        return (booking_type or self.booking_type)._bf_create_onetime_link(
            partner or self.destinataire)

    def _consentements(self, partner=None, status=None):
        domain = [
            ("subject_partner_id", "=", (partner or self.destinataire).id),
            ("purpose_id", "=", self.purpose.id),
        ]
        if status:
            domain.append(("status", "=", status))
        return self.env["privacy.consent"].sudo().search(domain)

    def _accorde(self, partner=None):
        """Pose un consentement actif au dossier, sans passer par le module."""
        return self.env["privacy.consent"].sudo().create({
            "subject_partner_id": (partner or self.destinataire).id,
            "purpose_id": self.purpose.id,
            "notice_id": self.notice.id,
            "status": "granted",
            "granted_at": fields.Datetime.now(),
        })

    def _creneau(self):
        """Une heure réellement réservable, prise à la source."""
        debut = fields.Datetime.now() + timedelta(days=2)
        import pytz
        debut = pytz.utc.localize(debut.replace(minute=0, second=0, microsecond=0))
        candidats = self.booking_type._bf_candidate_slots(
            debut, debut + timedelta(days=2))
        return candidats[0].astimezone(pytz.utc).replace(tzinfo=None)

    # -- L'état, avant toute correction -------------------------------------

    def test_type_sans_enregistrement_ne_demande_rien(self):
        """Un type qui n'enregistre pas ne doit RIEN réclamer.

        Sans ce contrôle, la Synchro 2FA et les rendez-vous de support
        enverraient une demande de consentement pour un enregistrement qui
        n'aura jamais lieu.
        """
        booking = self._lien(booking_type=self.type_sans_consentement)
        self.assertEqual(booking.bf_consent_state, "none")
        self.assertEqual(booking._bf_required_consents(), [])
        self.assertEqual(booking._bf_missing_consents(), [])

    def test_lien_personnel_nait_a_decouvert(self):
        """Le constat qui motive le lot : rien au dossier, donc manquant."""
        booking = self._lien()
        self.assertEqual(booking.bf_consent_state, "missing")
        self.assertEqual(
            [a["code"] for a in booking._bf_missing_consents()], ["recording"])

    def test_consentement_deja_au_dossier_ne_redemande_rien(self):
        """La règle « on ne redemande pas », vue depuis un autre chemin.

        Le consentement est porté par le CONTACT : accordé une fois, il vaut
        pour la réservation suivante, quel qu'ait été son chemin de création.
        """
        self._accorde()
        booking = self._lien()
        self.assertEqual(booking.bf_consent_state, "granted")
        self.assertEqual(booking._bf_missing_consents(), [])

    def test_consentement_expire_redevient_manquant(self):
        """Un consentement périmé n'est pas un consentement."""
        consent = self._accorde()
        consent.expires_at = fields.Datetime.now() - timedelta(days=1)
        booking = self._lien()
        self.assertEqual(booking.bf_consent_state, "missing")

    def test_consentement_revoque_redevient_manquant(self):
        consent = self._accorde()
        consent.withdrawn_at = fields.Datetime.now()
        booking = self._lien()
        self.assertEqual(booking.bf_consent_state, "missing")

    # -- Collecte en bande --------------------------------------------------

    def test_reponse_sur_place_accordee_est_consignee_avec_sa_preuve(self):
        booking = self._lien()
        booking.with_context(bf_no_consent_request=True)._bf_ensure_consents(
            collected={"recording": True}, evidence={"ip_address": "203.0.113.7"},
            source_note="essai")
        consent = self._consentements(status="granted")
        self.assertEqual(len(consent), 1)
        self.assertEqual(consent.notice_id, self.notice)
        self.assertTrue(consent.granted_at)
        preuve = self.env["privacy.consent.evidence"].sudo().search(
            [("consent_id", "=", consent.id)])
        self.assertEqual(len(preuve), 1)
        self.assertEqual(preuve.consent_action, "grant")
        self.assertEqual(preuve.ip_address, "203.0.113.7")
        self.assertIn("resource.booking=%d" % booking.id, consent.notes)
        self.assertEqual(booking.bf_consent_state, "granted")

    def test_case_vue_et_decochee_est_un_refus_pas_une_absence(self):
        """La distinction que le champ caché du gabarit existe pour porter."""
        booking = self._lien()
        booking.with_context(bf_no_consent_request=True)._bf_ensure_consents(
            collected={"recording": False}, source_note="essai")
        consent = self._consentements(status="refused")
        self.assertEqual(len(consent), 1)
        self.assertTrue(consent.refused_at)
        self.assertFalse(consent.granted_at)
        self.assertEqual(booking.bf_consent_state, "refused")

    def test_question_non_posee_n_ecrit_aucun_refus(self):
        """⚠️ Le cœur du dispositif.

        Une case absente et une case décochée arrivent identiques au serveur.
        Sans le drapeau « la question a été posée », tout POST de confirmation
        fabriquerait un refus que personne n'a exprimé, et l'on documenterait
        des refus imaginaires dans un registre censé faire preuve.
        """
        booking = self._lien()
        booking.with_context(bf_no_consent_request=True)._bf_ensure_consents(
            collected={}, source_note="essai")
        self.assertFalse(self._consentements())
        self.assertEqual(booking.bf_consent_state, "missing")

    def test_reponse_sur_place_ne_double_pas_un_consentement_actif(self):
        self._accorde()
        booking = self._lien()
        booking.with_context(bf_no_consent_request=True)._bf_ensure_consents(
            collected={"recording": True}, source_note="essai")
        self.assertEqual(len(self._consentements(status="granted")), 1)

    def test_reponse_sur_place_repond_a_une_demande_en_attente(self):
        """Une demande partie hier ne doit pas faire ignorer la réponse d'aujourd'hui.

        L'essai lit l'état AVANT et APRÈS, exprès : c'est cette lecture-là qui
        a révélé que le champ calculé restait en cache faute de dépendance
        déclarable, et rendait « demandé » sur un consentement qu'on venait
        d'accorder.
        """
        booking = self._lien()
        booking._bf_request_missing_consents()
        self.assertEqual(booking.bf_consent_state, "requested")
        booking.with_context(bf_no_consent_request=True)._bf_ensure_consents(
            collected={"recording": True}, source_note="essai")
        self.assertEqual(booking.bf_consent_state, "granted")

    # -- Demande hors bande -------------------------------------------------

    def test_demande_hors_bande_cree_un_consentement_en_attente(self):
        booking = self._lien()
        crees = booking._bf_request_missing_consents()
        self.assertEqual(len(crees), 1)
        self.assertEqual(crees.status, "pending")
        self.assertTrue(crees.requested_at)
        self.assertTrue(crees.access_token, "sans jeton, le lien public ne s'ouvre pas")
        self.assertEqual(booking.bf_consent_state, "requested")

    def test_demande_hors_bande_ne_se_repete_pas(self):
        booking = self._lien()
        booking._bf_request_missing_consents()
        self.assertFalse(booking._bf_request_missing_consents())
        self.assertEqual(len(self._consentements()), 1)

    def test_un_refus_n_est_pas_redemande(self):
        """Un refus est une réponse. La redemander est le harcèlement que le
        consentement existe pour empêcher."""
        booking = self._lien()
        booking.with_context(bf_no_consent_request=True)._bf_ensure_consents(
            collected={"recording": False}, source_note="essai")
        self.assertFalse(booking._bf_request_missing_consents())

    def test_sans_adresse_courriel_aucune_demande_ne_part(self):
        muet = self.env["res.partner"].create({"name": "Sans courriel"})
        booking = self._lien(partner=muet)
        self.assertFalse(booking._bf_request_missing_consents())
        self.assertEqual(booking.bf_consent_state, "missing")

    def test_bouton_backend_refuse_de_mentir_quand_il_n_y_a_rien_a_demander(self):
        self._accorde()
        booking = self._lien()
        with self.assertRaises(UserError):
            booking.action_bf_request_consents()

    def test_l_interrupteur_ferme_coupe_les_envois_automatiques(self):
        """⚠️ Un envoi sortant vers un client ne s'active pas tout seul.

        L'essai porte sur ce qui NE doit PAS partir. Sans lui, l'installation
        du module suffirait à écrire à des clients dès la première
        confirmation de rendez-vous, et personne ne le verrait avant que le
        courriel soit reçu.
        """
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_appointment.consent_auto_request", "0")
        booking = self._lien()
        self.assertFalse(booking._bf_request_missing_consents())
        self.assertFalse(self._consentements())
        self.assertEqual(booking.bf_consent_state, "missing")

    def test_le_bouton_du_backend_passe_outre_l_interrupteur(self):
        """Le geste est humain, sur une fiche ouverte : il n'attend rien."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_appointment.consent_auto_request", "0")
        booking = self._lien()
        booking.action_bf_request_consents()
        self.assertEqual(booking.bf_consent_state, "requested")

    # -- L'accroche unique : action_confirm ---------------------------------

    def test_confirmer_declenche_la_demande(self):
        """La porte du back-office et celle des satellites, refermées d'un coup."""
        booking = self.booking_type._bf_create_booking(
            self._creneau(), partners=self.destinataire)
        self.assertEqual(booking.state, "confirmed")
        self.assertEqual(booking.bf_consent_state, "requested")
        self.assertEqual(len(self._consentements(status="pending")), 1)

    def test_confirmer_ne_demande_rien_quand_la_page_a_deja_pose_la_question(self):
        """Sans ce drapeau, la personne recevrait un courriel lui redemandant
        ce qu'elle vient d'accorder à l'écran."""
        booking = self._lien()
        booking.start = self._creneau()
        booking.with_context(bf_consents_handled=True).action_confirm()
        self.assertFalse(self._consentements())
        self.assertEqual(booking.bf_consent_state, "missing")

    def test_confirmer_ne_bloque_jamais_la_reservation(self):
        """La règle de conduite du lot : un consentement manquant empêche
        l'ENREGISTREMENT, pas le rendez-vous."""
        booking = self._lien()
        booking.start = self._creneau()
        booking.action_confirm()
        self.assertEqual(booking.state, "confirmed")

    # -- Filtrable ----------------------------------------------------------

    def test_l_etat_est_cherchable(self):
        """Un état qu'on ne peut pas chercher n'est qu'une décoration."""
        decouvert = self._lien()
        couvert = self._lien(booking_type=self.type_sans_consentement)
        manquants = self.env["resource.booking"].search(
            [("bf_consent_state", "=", "missing")])
        self.assertIn(decouvert, manquants)
        self.assertNotIn(couvert, manquants)

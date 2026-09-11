"""Ce que le module promet, vérifié.

Les tests suivent l'ordre de la vie d'une inscription : on la monte, on la met
en ligne, quelqu'un demande à visiter, le vendeur tranche, la personne arrive.
Chaque piège mesuré au banc du 2026-09-11 a son test : le calendrier par défaut
qui avale les fins de semaine, la combinaison par courtier, la plage retirée
sous une visite confirmée, et le registre qui se ferme.
"""

from datetime import datetime, timedelta

import pytz

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "bf_visite")
class TestVisites(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tz = pytz.timezone("America/Toronto")
        cls.courtier = new_test_user(
            cls.env, login="essai_courtier",
            groups="bf_appointment_visit.group_bf_visit_user",
            tz="America/Toronto", email="courtier@example.com",
        )
        cls.gestionnaire = new_test_user(
            cls.env, login="essai_gestion",
            groups="bf_appointment_visit.group_bf_visit_manager",
            tz="America/Toronto", email="gestion@example.com",
        )
        cls.vendeur = cls.env["res.partner"].create({
            "name": "Essai vendeuse", "email": "vendeuse@example.com",
        })
        cls.locataire = cls.env["res.partner"].create({
            "name": "Essai locataire", "email": "locataire@example.com",
        })
        cls.visiteur = cls.env["res.partner"].create({
            "name": "Essai visiteur", "email": "visiteur@example.com",
        })
        # Le calendrier du courtier : samedi de 9 h à 15 h.
        cls.cal_courtier = cls.env["resource.calendar"].create({
            "name": "Essai dispo courtier",
            "tz": "America/Toronto",
            "attendance_ids": [(5, 0, 0), (0, 0, {
                "name": "sam", "dayofweek": "5", "hour_from": 9.0, "hour_to": 15.0,
            })],
        })
        cls.env["resource.resource"].create({
            "name": "Essai courtier",
            "resource_type": "user",
            "user_id": cls.courtier.id,
            "calendar_id": cls.cal_courtier.id,
            "tz": "America/Toronto",
        })
        cls.samedi = datetime(2026, 9, 12)

    # ------------------------------------------------------------------
    def _inscription(self, **extra):
        valeurs = {
            "name": "Essai 123 rue de la Fiche",
            "street": "123 rue de la Fiche",
            "city": "Sherbrooke",
            "seller_ids": [(6, 0, self.vendeur.ids)],
            "broker_ids": [(6, 0, self.courtier.ids)],
            "visit_duration": 0.5,
            "slot_duration": 0.5,
            "lead_time_hours": 0.0,
            "occupancy": "owner",
            "tz": "America/Toronto",
        }
        valeurs.update(extra)
        return self.env["bf.visit.listing"].create(valeurs)

    def _plage(self, inscription, de=13.0, a=16.0, date=None):
        return self.env["bf.visit.window"].create({
            "listing_id": inscription.id,
            "kind": "once",
            "date": date or self.samedi.date(),
            "hour_from": de,
            "hour_to": a,
        })

    def _creneaux(self, inscription):
        debut = self.tz.localize(datetime(2026, 9, 12, 0, 0))
        fin = self.tz.localize(datetime(2026, 9, 13, 0, 0))
        return [
            s.strftime("%H:%M")
            for s in inscription.booking_type_id._bf_candidate_slots(
                debut, fin, tz="America/Toronto"
            )
        ]

    def _demander_visite(self, inscription, heure_utc=None):
        heure_utc = heure_utc or datetime(2026, 9, 12, 17, 0)
        reservation = inscription.booking_type_id._bf_create_booking(
            heure_utc, partners=self.visiteur
        )
        return reservation.visit_id

    # ------------------------------------------------------------------
    # La publication fabrique ce qu'il faut
    # ------------------------------------------------------------------
    def test_publication_monte_les_objets(self):
        inscription = self._inscription()
        self._plage(inscription)
        inscription.action_publish()
        self.assertTrue(inscription.booking_type_id)
        self.assertTrue(inscription.resource_id)
        self.assertTrue(inscription.calendar_id)
        self.assertTrue(inscription.booking_type_id.is_public)
        self.assertEqual(
            inscription.booking_type_id.visit_listing_id, inscription,
            "Le type doit savoir de quelle inscription il vient.",
        )

    def test_un_courtier_publie_et_republie(self):
        """🔴 Le parcours joué par le RÔLE, pas par l'administrateur.

        Un courtier n'a aucun droit sur `resource.calendar`,
        `resource.resource`, `resource.booking.type` ni sur les combinaisons.
        La première publication les CRÉE (en sudo, donc ça passait), la seconde
        les MET À JOUR : sans sudo là aussi, elle rendait
        « Vous n'êtes pas autorisé à modifier les enregistrements Temps de
        travail de la ressource ». Trouvé sur la production du 2026-09-11, en
        rejouant le parcours comme un courtier. Un test joué en administrateur
        ne pouvait pas le voir.
        """
        inscription = self._inscription().with_user(self.courtier)
        self._plage(inscription)
        inscription.action_publish()
        self.assertEqual(inscription.state, "published")
        # la deuxième passe, celle qui met à jour au lieu de créer
        inscription.write({"visit_duration": 0.75})
        inscription.action_publish()
        self.assertEqual(inscription.booking_type_id.duration, 0.75)

    def test_publication_refuse_sans_courtier_ni_plage(self):
        sans_courtier = self._inscription(broker_ids=[(5, 0, 0)])
        self._plage(sans_courtier)
        with self.assertRaises(UserError):
            sans_courtier.action_publish()
        sans_plage = self._inscription()
        with self.assertRaises(UserError):
            sans_plage.action_publish()

    def test_calendrier_du_type_nest_pas_celui_de_lentreprise(self):
        """🔴 Le piège mesuré : le défaut avale les fins de semaine en silence."""
        inscription = self._inscription()
        self._plage(inscription)
        inscription.action_publish()
        self.assertNotEqual(
            inscription.booking_type_id.resource_calendar_id,
            self.env.company.resource_calendar_id,
            "Un type laissé sur le calendrier de l'entreprise ne rend aucun "
            "créneau de fin de semaine.",
        )
        self.assertTrue(
            self._creneaux(inscription),
            "Une plage du samedi doit produire des créneaux.",
        )

    def test_croisement_vendeur_courtier(self):
        """Vendeur 13 h à 16 h, courtier 9 h à 15 h : de 13 h à 14 h 30."""
        inscription = self._inscription()
        self._plage(inscription, de=13.0, a=16.0)
        inscription.action_publish()
        creneaux = self._creneaux(inscription)
        self.assertEqual(creneaux, ["13:00", "13:30", "14:00", "14:30"])

    def test_une_combinaison_par_courtier(self):
        """🔴 Jamais un K-de-N : il accepterait deux courtiers SANS la propriété."""
        second = new_test_user(
            self.env, login="essai_courtier2",
            groups="bf_appointment_visit.group_bf_visit_user",
            tz="America/Toronto", email="courtier2@example.com",
        )
        inscription = self._inscription(
            broker_ids=[(6, 0, [self.courtier.id, second.id])]
        )
        self._plage(inscription)
        inscription.action_publish()
        combinaisons = inscription.booking_type_id.combination_rel_ids.mapped(
            "combination_id"
        )
        self.assertEqual(len(combinaisons), 2)
        for combinaison in combinaisons:
            self.assertEqual(
                combinaison.min_required, 0,
                "ALL-of-N : toutes les ressources de la combinaison sont exigées.",
            )
            self.assertIn(inscription.resource_id, combinaison.resource_ids)
            self.assertEqual(len(combinaison.resource_ids), 2)

    # ------------------------------------------------------------------
    # Le logement occupé
    # ------------------------------------------------------------------
    def test_logement_occupe_impose_le_preavis(self):
        inscription = self._inscription(
            occupancy="tenant", tenant_id=self.locataire.id, lead_time_hours=2.0,
        )
        self.assertEqual(
            inscription._effective_lead_time(), 24.0,
            "L'article 1931 exige 24 heures, et ça ne se descend pas.",
        )
        self._plage(inscription)
        inscription.action_publish()
        self.assertEqual(
            inscription.booking_type_id.modifications_deadline, 24.0
        )

    def test_logement_occupe_borne_les_heures(self):
        inscription = self._inscription(
            occupancy="tenant", tenant_id=self.locataire.id,
        )
        with self.assertRaises(ValidationError):
            self._plage(inscription, de=8.0, a=10.0)
        with self.assertRaises(ValidationError):
            self._plage(inscription, de=20.0, a=22.0)
        self._plage(inscription, de=9.0, a=12.0)

    def test_avis_au_locataire_laisse_sa_trace(self):
        inscription = self._inscription(
            occupancy="tenant", tenant_id=self.locataire.id,
            approval_mode="auto", lead_time_hours=0.0,
        )
        self._plage(inscription, de=13.0, a=16.0)
        inscription.action_publish()
        visite = self._demander_visite(inscription)
        self.assertEqual(visite.state, "approved")
        self.assertTrue(
            visite.tenant_notice_sent_at,
            "Sans trace d'envoi, il n'y a rien à montrer si le locataire "
            "conteste la visite.",
        )

    # ------------------------------------------------------------------
    # Les plages, une fois des visites posées
    # ------------------------------------------------------------------
    def test_retirer_une_plage_occupee_nomme_la_visite(self):
        """🔴 Odoo refuse déjà, mais sans dire quoi déplacer."""
        inscription = self._inscription(approval_mode="auto")
        plage = self._plage(inscription)
        inscription.action_publish()
        visite = self._demander_visite(inscription)
        self.assertEqual(visite.state, "approved")
        with self.assertRaises(UserError) as capture:
            plage.write({"hour_from": 15.0})
        self.assertIn(visite.display_name, str(capture.exception))

    # ------------------------------------------------------------------
    # Le parcours d'approbation
    # ------------------------------------------------------------------
    def test_mode_approbation_retient_la_confirmation(self):
        inscription = self._inscription(approval_mode="seller")
        self._plage(inscription)
        inscription.action_publish()
        visite = self._demander_visite(inscription)
        self.assertEqual(visite.state, "requested")
        self.assertFalse(
            visite.access_released,
            "Les consignes d'accès ne sortent pas avant la décision.",
        )
        visite.action_approve()
        self.assertEqual(visite.state, "approved")

    def test_refus_rend_le_creneau(self):
        inscription = self._inscription(approval_mode="seller")
        self._plage(inscription)
        inscription.action_publish()
        visite = self._demander_visite(inscription)
        self.assertNotIn("13:00", self._creneaux(inscription))
        visite.action_decline()
        self.assertEqual(visite.state, "declined")
        self.assertEqual(visite.booking_id.state, "canceled")
        self.assertIn(
            "13:00", self._creneaux(inscription),
            "Un refus doit rendre le créneau, sinon l'heure reste morte.",
        )

    def test_acces_transmis_seulement_a_la_confirmation(self):
        inscription = self._inscription(
            approval_mode="seller",
            access_instructions="<p>Boîte à clés : 4417</p>",
        )
        self._plage(inscription)
        inscription.action_publish()
        visite = self._demander_visite(inscription)
        self.assertFalse(visite.access_released)
        visite.action_approve()
        self.assertTrue(visite.access_released)

    def test_garde_du_double_envoi(self):
        inscription = self._inscription(approval_mode="seller")
        self._plage(inscription)
        inscription.action_publish()
        visite = self._demander_visite(inscription)
        self.assertTrue(visite.setup_done)
        avant = self.env["mail.mail"].search_count([])
        visite.booking_id.action_confirm()
        self.assertEqual(
            self.env["mail.mail"].search_count([]), avant,
            "Une deuxième confirmation ne redemande pas au vendeur.",
        )

    # ------------------------------------------------------------------
    # Le registre
    # ------------------------------------------------------------------
    def test_question_de_la_representation_horodatee(self):
        inscription = self._inscription(approval_mode="auto")
        self._plage(inscription)
        inscription.action_publish()
        visite = self._demander_visite(inscription)
        champ = self.env["appointment.intake.field"].search([
            ("type_id", "=", inscription.booking_type_id.id),
            ("bf_visit_role", "=", "representation"),
        ], limit=1)
        self.assertTrue(
            champ,
            "La question de la représentation doit être posée sur le "
            "formulaire public.",
        )
        # 🔴 Le rôle est technique, pas textuel : renommer la question, la
        # traduire ou en ajouter une qui contient le même mot ne doit rien
        # casser.
        champ.name = "Are you working with a REALTOR®?"
        self.env["appointment.intake.answer"].create({
            "booking_id": visite.booking_id.id,
            "field_id": champ.id,
            "value": "Oui",
        })
        visite._read_representation_from_intake()
        self.assertEqual(visite.representation, "broker")
        self.assertTrue(
            visite.representation_asked_at,
            "L'heure de la réponse est la pièce qui compte dans un litige "
            "sur la cause efficiente.",
        )

    def test_les_questions_posees_avant_le_role_sont_reprises(self):
        """⚠️ Une inscription déjà en ligne ne doit pas gagner un doublon.

        Le rôle technique est arrivé après les premières questions. Sans
        reprise, la fabrique en recrée deux à côté des anciennes, et le
        visiteur répond deux fois à la même chose.
        """
        inscription = self._inscription()
        self._plage(inscription)
        inscription.action_publish()
        champs = self.env["appointment.intake.field"].search([
            ("type_id", "=", inscription.booking_type_id.id),
        ])
        self.assertEqual(len(champs), 2)
        champs.bf_visit_role = False  # l'état d'avant le correctif
        inscription._sync_intake_fields()
        apres = self.env["appointment.intake.field"].search([
            ("type_id", "=", inscription.booking_type_id.id),
        ])
        self.assertEqual(len(apres), 2, "les anciennes questions sont reprises")
        self.assertEqual(
            set(apres.mapped("bf_visit_role")), {"representation", "broker_name"}
        )

    def test_registre_ferme_apres_larrivee(self):
        inscription = self._inscription(approval_mode="auto")
        self._plage(inscription)
        inscription.action_publish()
        visite = self._demander_visite(inscription)
        visite.action_mark_arrived()
        with self.assertRaises(AccessError):
            visite.with_user(self.courtier).write({"visitor_name": "Quelqu'un d'autre"})
        # Le gestionnaire garde la main pour corriger une erreur de saisie.
        visite.with_user(self.gestionnaire).write({"visitor_name": "Nom corrigé"})
        self.assertEqual(visite.visitor_name, "Nom corrigé")

    def test_identite_consignee_sans_piece(self):
        inscription = self._inscription(approval_mode="auto")
        self._plage(inscription)
        inscription.action_publish()
        visite = self._demander_visite(inscription)
        with self.assertRaises(UserError):
            visite.action_confirm_identity()
        visite.identity_method = "in_person"
        visite.identity_doc_kind = "licence"
        visite.action_confirm_identity()
        self.assertTrue(visite.identity_checked)
        self.assertTrue(visite.identity_checked_at)
        self.assertFalse(
            [f for f in visite._fields if "number" in f or "numero" in f],
            "Aucun champ ne doit pouvoir accueillir un numéro de pièce.",
        )

    def test_pas_de_visio_ni_de_consentement_denregistrement(self):
        """🔴 Trouvé au parcours réel : la page publique refusait de réserver.

        Un type de rendez-vous naît avec une salle de visioconférence et le
        consentement d'enregistrement exigé. Sur une visite de maison, la page
        répondait « l'enregistrement fait partie du service pour ce type de
        rencontre » et bloquait toute demande. Aucun test Python ne pouvait le
        voir : ils créent la réservation sans passer par le formulaire.
        """
        inscription = self._inscription()
        self._plage(inscription)
        inscription.action_publish()
        self.assertEqual(inscription.booking_type_id.video_provider, "none")
        self.assertFalse(inscription.booking_type_id.requires_recording_consent)
        self.assertTrue(inscription.booking_type_id.is_in_person)

    def test_fuseau_par_defaut_nest_pas_celui_de_la_societe(self):
        """🔴 Le calendrier d'une société neuve est en UTC.

        Sans fuseau propre à l'inscription, « samedi 13 h » se lisait 13 h UTC,
        donc 9 h du matin pour le visiteur de Montréal. Mesuré au banc le
        2026-09-11 : la grille rendait 09:00 et 09:30 au lieu de 13:00 à 14:30.
        """
        inscription = self._inscription()
        self.assertEqual(inscription.tz, "America/Toronto")
        self._plage(inscription)
        inscription.action_publish()
        self.assertEqual(inscription.calendar_id.tz, "America/Toronto")
        self.assertEqual(
            inscription.booking_type_id.resource_calendar_id.tz, "America/Toronto"
        )

    def test_fermeture_de_nuit(self):
        inscription = self._inscription(approval_mode="auto")
        self._plage(inscription)
        inscription.action_publish()
        presente = self._demander_visite(inscription)
        absente = self._demander_visite(
            inscription, heure_utc=datetime(2026, 9, 12, 17, 30)
        )
        presente.action_mark_arrived()
        passe = datetime.now() - timedelta(hours=4)
        (presente | absente).write({"start": passe})
        self.env["bf.visit"]._cron_close_visits()
        self.assertEqual(presente.state, "done")
        self.assertEqual(
            absente.state, "no_show",
            "Une visite sans arrivée constatée n'est pas « faite ».",
        )

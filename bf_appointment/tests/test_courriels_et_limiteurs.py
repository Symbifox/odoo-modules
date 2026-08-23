"""Second lot de ménage (2.51.0) : envois, pièces jointes, limiteurs, modal.

Rien ici n'ajoute de fonctionnalité. Ces tests verrouillent des propriétés qui
n'avaient aucun filet et que la simplification aurait pu casser en silence.
"""

import pathlib
import re
from datetime import timedelta

import pytz

from odoo import Command, fields
from odoo.tests import HttpCase, TransactionCase, tagged


@tagged("bf_appointment", "bf_appointment_menage")
class TestEnvois(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, tz="UTC"))
        attendances = [
            Command.create({
                "name": f"All day {d}", "dayofweek": str(d),
                "hour_from": 0.0, "hour_to": 24.0, "day_period": "morning",
            })
            for d in range(7)
        ]
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "24/7 Ménage", "attendance_ids": attendances, "tz": "UTC",
        })
        cls.resource = cls.env["resource.resource"].create({
            "name": "Ménage material", "calendar_id": cls.calendar.id,
            "resource_type": "material", "tz": "UTC",
        })
        cls.combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([cls.resource.id])],
        })
        cls.partner = cls.env["res.partner"].create({
            "name": "Ménage Booker", "email": "menage@test.invalid", "tz": "America/Toronto",
        })
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Ménage Type", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id, "video_provider": "none",
            "requires_recording_consent": False,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": cls.combination.id}),
            ],
        })

    def _booking(self):
        now = fields.Datetime.context_timestamp(
            self.env["resource.booking"], fields.Datetime.now())
        creneaux = self.booking_type._bf_candidate_slots(
            now + timedelta(hours=1), now + timedelta(days=7), limit=1)
        instant = creneaux[0].astimezone(pytz.utc).replace(tzinfo=None)
        return self.booking_type._bf_create_booking(
            instant, partners=self.partner, confirm=False)

    def _gabarit(self):
        return self.env.ref("bf_appointment.mail_template_appointment_confirmation").sudo()

    def _ics_de(self, booking):
        return self.env["ir.attachment"].search([
            ("res_model", "=", "resource.booking"),
            ("res_id", "=", booking.id),
            ("mimetype", "=", "text/calendar"),
        ])

    def test_la_piece_ics_ne_survit_pas_a_l_envoi(self):
        """Elle est dans le message remis; la garder en accumulait une par envoi."""
        booking = self._booking()
        self.assertFalse(self._ics_de(booking))
        booking._send_appointment_email(self._gabarit(), recipient="booker")
        self.assertFalse(
            self._ics_de(booking),
            "aucune pièce .ics ne doit rester accrochée à la réservation")

    def test_deux_envois_n_accumulent_rien(self):
        booking = self._booking()
        for _i in range(3):
            booking._send_appointment_email(self._gabarit(), recipient="booker")
        self.assertFalse(self._ics_de(booking))

    def test_sans_ics_rien_n_est_cree(self):
        booking = self._booking()
        booking._send_appointment_email(
            self._gabarit(), attach_ics=False, recipient="booker")
        self.assertFalse(self._ics_de(booking))

    def test_le_destinataire_explicite_prime_sur_l_heuristique(self):
        """Un gabarit qui nomme `user_id` tout en s'adressant au demandeur.

        C'est exactement le piège : l'heuristique lit la chaîne « user_id »
        dans `email_to` et bascule le courriel dans le fuseau et la langue de
        l'organisateur, en silence.
        """
        booking = self._booking()
        booking.user_id.tz = "Pacific/Auckland"
        booking.user_id.lang = "en_US"
        piege = self._gabarit().copy({"email_to": "{{ object.user_id.email }}"})

        lang, tz = booking._bf_render_locale(piege, recipient="booker")
        self.assertEqual(tz, booking._get_booker_display_tz())
        self.assertNotEqual(tz, "Pacific/Auckland")

        lang_org, tz_org = booking._bf_render_locale(piege, recipient="organizer")
        self.assertEqual(tz_org, "Pacific/Auckland")

    def test_l_heuristique_reste_le_repli(self):
        """Sans consigne, un gabarit qui nomme `user_id` vise l'organisateur."""
        booking = self._booking()
        booking.user_id.tz = "Pacific/Auckland"
        vers_organisateur = self._gabarit().copy(
            {"email_to": "{{ object.user_id.email }}"})
        vers_demandeur = self._gabarit().copy(
            {"email_to": "{{ object.partner_id.email }}"})
        self.assertEqual(
            booking._bf_render_locale(vers_organisateur)[1], "Pacific/Auckland")
        self.assertEqual(
            booking._bf_render_locale(vers_demandeur)[1],
            booking._get_booker_display_tz())

    def test_le_fuseau_du_demandeur_n_est_jamais_vide(self):
        """Un fuseau vide laisserait le rendu retomber sur l'organisateur."""
        booking = self._booking()
        booking.partner_id.tz = False
        _lang, tz = booking._bf_render_locale(self._gabarit(), recipient="booker")
        self.assertTrue(tz, "le fuseau d'affichage du demandeur doit toujours être posé")


@tagged("bf_appointment", "bf_appointment_menage")
class TestLimiteurs(TransactionCase):
    """Les trois limiteurs partagent désormais le seau nommé.

    Leur sémantique ne doit pas avoir bougé d'un pouce : vérifier un jeton ne
    consomme pas, créer une réservation consomme.
    """

    def setUp(self):
        super().setUp()
        from odoo.addons.bf_appointment.controllers import main as ctrl
        ctrl._bucket_data.clear()
        self.ctrl = ctrl

    def test_verifier_un_jeton_ne_consomme_pas(self):
        with self._ip("198.51.100.7"):
            for _i in range(50):
                self.assertTrue(self.ctrl._check_token_rate_limit())

    def test_les_echecs_de_jeton_finissent_par_fermer(self):
        with self._ip("198.51.100.8"):
            for _i in range(self.ctrl._TOKEN_FAIL_MAX):
                self.assertTrue(self.ctrl._check_token_rate_limit())
                self.ctrl._record_token_failure()
            self.assertFalse(self.ctrl._check_token_rate_limit())

    def test_creer_une_reservation_consomme(self):
        with self._ip("198.51.100.9"):
            for _i in range(self.ctrl._BOOK_MAX):
                self.assertTrue(self.ctrl._check_book_rate_limit())
            self.assertFalse(self.ctrl._check_book_rate_limit())

    def test_les_seaux_sont_independants(self):
        with self._ip("198.51.100.10"):
            for _i in range(self.ctrl._BOOK_MAX):
                self.ctrl._check_book_rate_limit()
            self.assertFalse(self.ctrl._check_book_rate_limit())
            # Le plafond des réservations ne doit pas fermer celui des jetons.
            self.assertTrue(self.ctrl._check_token_rate_limit())

    def _ip(self, adresse):
        """Fige `_client_ip` le temps du bloc : un test n'a pas de requête."""
        import contextlib

        ctrl = self.ctrl
        original = ctrl._client_ip

        @contextlib.contextmanager
        def _fige():
            ctrl._client_ip = lambda: adresse
            try:
                yield
            finally:
                ctrl._client_ip = original

        return _fige()


@tagged("bf_appointment", "bf_appointment_menage")
class TestModalUnique(TransactionCase):
    """Un seul formulaire de confirmation, quel que soit le nombre de créneaux.

    Contrôle sur la SOURCE du gabarit : le rendu dépend d'un mois et d'un
    calendrier, la propriété qu'on veut tenir est structurelle.
    """

    def test_le_gabarit_ne_boucle_plus_sur_les_formulaires(self):
        gabarit = (pathlib.Path(__file__).resolve().parent.parent
                   / "templates" / "appointment_public.xml").read_text(encoding="utf-8")
        self.assertNotIn(
            't-foreach="slots[day]" t-as="slot"\n', gabarit.replace("<form ", "<form\n"),
            "aucun <form> ne doit être répété par créneau")
        formulaires_par_creneau = re.findall(
            r"<form[^>]*t-foreach=\"slots\[day\]\"", gabarit)
        self.assertFalse(
            formulaires_par_creneau,
            "un formulaire par créneau produit des centaines de modals par page")
        self.assertEqual(
            gabarit.count('id="bf-modal-confirm"'), 1,
            "il doit y avoir exactement un modal de confirmation")
        # La bulle doit porter son créneau, sinon le modal unique ne sait plus
        # lequel confirmer.
        for attribut in ("data-bf-when", "data-bf-date", "data-bf-time"):
            self.assertIn(attribut, gabarit)

    def test_le_js_recopie_le_creneau_choisi(self):
        js = (pathlib.Path(__file__).resolve().parent.parent
              / "static" / "src" / "js" / "timezone_detect.js").read_text(encoding="utf-8")
        self.assertIn("bf-modal-confirm", js)
        self.assertIn("show.bs.modal", js)
        self.assertIn("data-bf-when-field", js)

    def test_la_protection_double_envoi_n_est_ecrite_qu_une_fois(self):
        racine = pathlib.Path(__file__).resolve().parent.parent / "static" / "src" / "js"
        porteurs = [
            f.name for f in racine.glob("*.js")
            if "bfProcessed" in f.read_text(encoding="utf-8")
            or "bf-btn-confirm" in f.read_text(encoding="utf-8")
        ]
        self.assertEqual(
            porteurs, ["processing_buttons.js"],
            "la protection contre le double envoi ne vit que dans un fichier")


@tagged("-at_install", "post_install", "bf_appointment", "bf_appointment_menage")
class TestPageDeChoixDeCreneau(HttpCase):
    """La page publique doit RENDRE, pas seulement compiler.

    Un défaut QWeb (un `t-attf` bancal, un `t-set` déplacé) ne se voit ni à la
    lecture ni au `py_compile` : il ne se voit qu'au rendu. Le passage à un
    modal unique touche précisément cette page, et c'est la surface la plus
    frequentee du module.
    """

    def setUp(self):
        super().setUp()
        # ⚠️ Les routes publiques sont déclarées `website=True` : sans le module
        # `website`, elles ne sont pas routées du tout et tout rend 404. Le
        # module ne DÉPEND pas de `website` (c'est voulu — un locataire peut
        # l'installer sans site web), donc ce test ne peut pas s'exécuter
        # partout. On le saute bruyamment plutôt que de le faire mentir.
        if not self.env["ir.module.module"].sudo().search_count(
                [("name", "=", "website"), ("state", "=", "installed")]):
            self.skipTest(
                "module `website` absent : les routes `website=True` ne sont "
                "pas routées, ce test n'a rien à mesurer")
        attendances = [
            Command.create({
                "name": f"All day {d}", "dayofweek": str(d),
                "hour_from": 0.0, "hour_to": 24.0, "day_period": "morning",
            })
            for d in range(7)
        ]
        calendrier = self.env["resource.calendar"].create({
            "name": "24/7 HTTP", "attendance_ids": attendances, "tz": "UTC",
        })
        ressource = self.env["resource.resource"].create({
            "name": "HTTP material", "calendar_id": calendrier.id,
            "resource_type": "material", "tz": "UTC",
        })
        combinaison = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([ressource.id])],
        })
        partenaire = self.env["res.partner"].create({
            "name": "HTTP Booker", "email": "http@test.invalid",
        })
        self.type_rdv = self.env["resource.booking.type"].create({
            "name": "HTTP Type", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": calendrier.id, "video_provider": "none",
            "requires_recording_consent": False, "is_public": True,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": combinaison.id}),
            ],
        })
        self.reservation = self.type_rdv._bf_create_onetime_link(partenaire)
        self.env.cr.flush()

    def test_la_page_rend_un_seul_modal_et_des_creneaux(self):
        url = "/appointment/b/%d/%s/schedule" % (
            self.reservation.id, self.reservation.access_token)
        reponse = self.url_open(url)
        self.assertEqual(reponse.status_code, 200, "la page doit rendre")
        page = reponse.text
        self.assertEqual(
            page.count('id="bf-modal-confirm"'), 1,
            "exactement un formulaire de confirmation, quel que soit le nombre "
            "de créneaux")
        self.assertGreater(
            page.count("data-bf-when="), 3,
            "les bulles doivent porter leur créneau")
        self.assertIn("data-bf-when-field", page,
                      "le modal doit avoir son champ caché à remplir")
        # Les anciens identifiants étaient `modal-confirm-<horodatage>`. On
        # cible ce motif précis : `bf-modal-confirm-title`, l'en-tête du modal
        # unique, contient légitimement la même sous-chaîne.
        self.assertFalse(
            re.search(r"modal-confirm-\d", page),
            "plus aucun modal indexé par créneau")

    def test_la_page_d_accueil_publique_rend(self):
        reponse = self.url_open("/appointment")
        self.assertEqual(reponse.status_code, 200)

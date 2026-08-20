# -*- coding: utf-8 -*-
"""Liens de réservation personnels (2.42.0)."""

from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("bf_appointment", "bf_appointment_onetime")
class TestOnetimeLink(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, tz="UTC"))
        attendances = [
            Command.create({
                "name": "j%s" % d, "dayofweek": str(d), "hour_from": 0.0,
                "hour_to": 24.0, "day_period": "morning",
            })
            for d in range(7)
        ]
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "24/7 lien", "attendance_ids": attendances, "tz": "UTC"})
        cls.resource = cls.env["resource.resource"].create({
            "name": "lien material", "calendar_id": cls.calendar.id,
            "resource_type": "material", "tz": "UTC"})
        cls.combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([cls.resource.id])]})
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Type lien", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id, "is_public": True,
            "listed_on_landing": False,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": cls.combination.id})],
        })
        cls.destinataire = cls.env["res.partner"].create({
            "name": "Destinataire", "email": "dest@test.invalid"})
        cls.invite = cls.env["res.partner"].create({
            "name": "Invité", "email": "invite@test.invalid"})

    def _wizard(self, **vals):
        base = {
            "type_id": self.booking_type.id,
            "partner_id": self.destinataire.id,
        }
        base.update(vals)
        return self.env["bf.appointment.onetime.wizard"].create(base)

    # -- Fabrication --------------------------------------------------------

    def test_wizard_creates_a_pending_booking_with_a_link(self):
        """Le lien n'a de sens que sur une réservation SANS créneau : c'est la
        personne qui choisira."""
        w = self._wizard()
        w.action_create_link()
        self.assertEqual(w.state, "done")
        booking = w.booking_id
        self.assertTrue(booking.exists())
        self.assertFalse(booking.start, "un lien de réservation ne fixe pas l'heure")
        self.assertNotEqual(booking.state, "confirmed")
        self.assertTrue(booking.access_token)
        self.assertEqual(booking.bf_source, "onetime")
        self.assertIn("/appointment/b/%d/%s/schedule" % (booking.id, booking.access_token),
                      w.url)

    def test_guests_are_attached_without_choosing_the_slot(self):
        w = self._wizard(guest_partner_ids=[Command.set([self.invite.id])])
        w.action_create_link()
        self.assertIn(self.destinataire, w.booking_id.partner_ids)
        self.assertIn(self.invite, w.booking_id.partner_ids)

    def test_overrides_are_applied(self):
        w = self._wizard(duration=2.5, location="Salle du fond")
        w.action_create_link()
        self.assertEqual(w.booking_id.duration, 2.5)
        self.assertEqual(w.booking_id.location, "Salle du fond")

    def test_expiry_is_set_from_days(self):
        w = self._wizard(expires_in_days=7)
        w.action_create_link()
        delta = w.booking_id.link_expires_at - fields.Datetime.now()
        self.assertGreater(delta, timedelta(days=6, hours=23))
        self.assertLess(delta, timedelta(days=7, minutes=1))

    def test_no_expiry_when_zero(self):
        w = self._wizard(expires_in_days=0)
        w.action_create_link()
        self.assertFalse(w.booking_id.link_expires_at)

    def test_non_public_type_is_refused_with_a_reason(self):
        """Un type non public rendrait un lien qui ne s'ouvre pas. Mieux vaut
        refuser à la fabrication qu'après l'envoi."""
        self.booking_type.is_public = False
        w = self._wizard()
        with self.assertRaises(UserError):
            w.action_create_link()

    # -- État du lien -------------------------------------------------------

    def test_fresh_link_is_active_and_usable(self):
        w = self._wizard()
        w.action_create_link()
        self.assertEqual(w.booking_id.link_state, "active")
        self.assertTrue(w.booking_id._link_is_usable())

    def test_expired_link_is_refused(self):
        w = self._wizard()
        w.action_create_link()
        w.booking_id.link_expires_at = fields.Datetime.now() - timedelta(minutes=1)
        self.assertEqual(w.booking_id.link_state, "expired")
        self.assertFalse(w.booking_id._link_is_usable())

    def test_single_use_link_closes_after_use(self):
        w = self._wizard(single_use=True)
        w.action_create_link()
        booking = w.booking_id
        self.assertTrue(booking._link_is_usable())
        booking._mark_link_used()
        self.assertEqual(booking.link_state, "used")
        self.assertFalse(booking._link_is_usable())

    def test_reusable_link_stays_open_after_use(self):
        w = self._wizard(single_use=False)
        w.action_create_link()
        booking = w.booking_id
        booking._mark_link_used()
        self.assertEqual(booking.link_state, "active",
                         "sans usage unique, le lien doit rester ouvert")
        self.assertTrue(booking._link_is_usable())

    def test_marking_twice_keeps_the_first_timestamp(self):
        w = self._wizard()
        w.action_create_link()
        booking = w.booking_id
        booking._mark_link_used()
        premier = booking.link_used_at
        booking._mark_link_used()
        self.assertEqual(booking.link_used_at, premier)

    def test_ordinary_bookings_are_not_link_governed(self):
        """🔴 L'invariant qui protège l'existant : une réservation prise sur le
        formulaire public n'a pas de lien à expirer, et les gardes ne doivent
        jamais s'appliquer à elle."""
        ordinaire = self.env["resource.booking"].create({
            "type_id": self.booking_type.id,
            "partner_ids": [Command.set([self.destinataire.id])],
        })
        self.assertEqual(ordinaire.link_state, "none")
        self.assertTrue(ordinaire._link_is_usable())
        ordinaire.link_expires_at = fields.Datetime.now() - timedelta(days=1)
        self.assertEqual(
            ordinaire.link_state, "none",
            "une expiration posée par erreur ne doit pas fermer une réservation "
            "ordinaire",
        )
        self.assertTrue(ordinaire._link_is_usable())

    def test_closed_link_template_exists(self):
        """La page qui explique doit exister : sans elle, le contrôleur lève."""
        self.assertTrue(self.env.ref(
            "bf_appointment.appointment_link_closed", raise_if_not_found=False))


@tagged("bf_appointment", "bf_appointment_cancel_link")
class TestCancelLinkIsReachable(TransactionCase):
    """Le lien « Annuler » de l'ICS se clique depuis l'agenda, donc en GET."""

    def test_cancel_url_in_ics_points_at_a_reachable_route(self):
        """🔴 L'ICS écrit « Annuler : <url> » : cette URL doit aboutir.

        Elle rendait un 405 brut de Werkzeug — la route n'acceptait que POST.
        Signalé le 2026-08-20. Le contrôle vérifie ici que la route déclare
        bien GET; le comportement de la page est éprouvé par sonde HTTP.
        """
        from odoo.addons.bf_appointment.controllers.main import AppointmentController

        route = AppointmentController.appointment_cancel.original_routing
        self.assertIn("GET", route["methods"],
                      "le lien d'annulation d'un courriel se clique en GET")
        self.assertIn("POST", route["methods"],
                      "la mutation doit rester possible en POST")

    def test_cancel_confirm_template_exists(self):
        self.assertTrue(self.env.ref(
            "bf_appointment.appointment_cancel_confirm", raise_if_not_found=False))


@tagged("bf_appointment", "bf_appointment_quicklink")
class TestQuickBookingLink(TransactionCase):
    """Raccourcis : depuis le compositeur de courriel et depuis un contact."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, tz="UTC"))
        att = [Command.create({"name": "j%s" % d, "dayofweek": str(d),
                               "hour_from": 0.0, "hour_to": 24.0,
                               "day_period": "morning"}) for d in range(7)]
        cal = cls.env["resource.calendar"].create({
            "name": "24/7 quick", "attendance_ids": att, "tz": "UTC"})
        res = cls.env["resource.resource"].create({
            "name": "quick mat", "calendar_id": cal.id,
            "resource_type": "material", "tz": "UTC"})
        combo = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([res.id])]})
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Type rapide", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": cal.id, "is_public": True,
            "listed_on_landing": True,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": combo.id})]})
        cls.env.company.appointment_quick_link_type_id = cls.booking_type
        cls.client = cls.env["res.partner"].create({
            "name": "Client rapide", "email": "rapide@test.invalid"})

    def _composer(self, **vals):
        base = {"partner_ids": [Command.set([self.client.id])],
                "body": "<p>Bonjour,</p>"}
        base.update(vals)
        return self.env["mail.compose.message"].create(base)

    def test_composer_appends_a_personal_link(self):
        c = self._composer()
        c.action_bf_insert_booking_link()
        self.assertIn("Bonjour", c.body, "le texte déjà écrit doit survivre")
        booking = self.env["resource.booking"].search(
            [("bf_source", "=", "onetime")], order="id desc", limit=1)
        self.assertIn(booking.access_token, c.body, "le lien n'est pas dans le corps")
        self.assertIn(self.client, booking.partner_ids)
        self.assertFalse(booking.start, "un lien ne fixe pas l'heure")

    def test_link_is_not_escaped(self):
        """🔴 Le défaut le plus visible : les balises en clair dans le courriel.

        `body` est un `Markup` ; y concaténer une `str` échappe la `str`, et le
        rédacteur voit « &lt;p&gt;&lt;a href… » au lieu d'un lien. L'écriture
        réussit, aucune exception : seul un contrôle sur le contenu l'attrape.
        """
        c = self._composer()
        c.action_bf_insert_booking_link()
        corps = str(c.body)
        self.assertNotIn("&lt;", corps, "des balises ont été échappées")
        self.assertNotIn("&gt;", corps, "des balises ont été échappées")
        # Le banc sert en http, la prod en https : on vérifie la BALISE, pas
        # le protocole, sinon le test échoue là où le code est bon.
        self.assertIn('<a href="http', corps, "le lien n'est pas du HTML")
        self.assertRegex(corps, r'<a href="[^"]+/appointment/b/\d+/[^"]+/schedule"')

    def test_link_lands_before_the_signature(self):
        """Sous la signature, le lien se lit comme une note de bas de page."""
        signature = '<div><table style="border:0"><tr><td>Jane Doe</td></tr></table></div>'
        self.env.user.signature = signature
        c = self._composer(body="<p>Bonjour,</p><br>" + signature)
        c.action_bf_insert_booking_link()
        corps = str(c.body)
        i_lien = corps.find("/appointment/b/")
        i_sig = corps.find("Jane Doe")
        self.assertGreater(i_lien, 0, "lien absent")
        self.assertGreater(i_sig, 0, "signature absente")
        self.assertLess(i_lien, i_sig, "le lien est passé SOUS la signature")
        self.assertTrue(corps.startswith("<p>Bonjour,</p>"),
                        "le texte déjà écrit doit rester en tête")

    def test_short_signature_is_still_found(self):
        """🔴 Une signature courte doit être trouvée comme une longue.

        Les tailles de préfixe essayées étaient 400, 200, 100 et 60, chacune
        sautée si la signature est plus courte : une signature compactée sous
        60 caractères ne déclenchait aucun essai et le lien retombait à la fin,
        en silence. Invisible en production, où les signatures font des
        milliers de caractères.
        """
        courte = "<div><b>Jane Doe</b></div>"
        self.env.user.signature = courte
        c = self._composer(body="<p>Salut,</p><br>" + courte)
        c.action_bf_insert_booking_link()
        corps = str(c.body)
        self.assertLess(corps.find("/appointment/b/"), corps.find("Jane Doe"),
                        "une signature courte n'a pas été repérée")

    def test_link_lands_before_a_quoted_reply(self):
        c = self._composer(
            body='<p>Ma réponse</p><blockquote>Le message d\'origine</blockquote>')
        c.action_bf_insert_booking_link()
        corps = str(c.body)
        self.assertLess(corps.find("/appointment/b/"), corps.find("<blockquote"),
                        "le lien doit précéder l'historique cité")

    def test_link_appended_when_there_is_no_signature(self):
        self.env.user.signature = False
        c = self._composer(body="<p>Court message</p>")
        c.action_bf_insert_booking_link()
        corps = str(c.body)
        self.assertTrue(corps.startswith("<p>Court message</p>"))
        self.assertIn("/appointment/b/", corps)

    def test_composer_never_overwrites_the_body(self):
        c = self._composer(body="<p>Texte important</p>")
        c.action_bf_insert_booking_link()
        self.assertTrue(c.body.startswith("<p>Texte important</p>"),
                        "le lien doit s'AJOUTER, jamais remplacer")

    def test_composer_without_recipient_says_so(self):
        c = self.env["mail.compose.message"].create({"body": "<p>x</p>"})
        with self.assertRaises(UserError):
            c.action_bf_insert_booking_link()

    def test_composer_recipient_without_email_is_refused(self):
        muet = self.env["res.partner"].create({"name": "Sans courriel"})
        c = self._composer(partner_ids=[Command.set([muet.id])])
        with self.assertRaises(UserError):
            c.action_bf_insert_booking_link()

    def test_configured_type_wins_over_the_first_listed(self):
        autre = self.booking_type.copy({
            "name": "Autre type", "slug": "autre-type-quick", "sequence": 1})
        self.env.company.appointment_quick_link_type_id = autre
        c = self._composer()
        c.action_bf_insert_booking_link()
        booking = self.env["resource.booking"].search(
            [("bf_source", "=", "onetime")], order="id desc", limit=1)
        self.assertEqual(booking.type_id, autre,
                         "le réglage de société doit primer")

    def test_falls_back_to_first_public_type(self):
        self.env.company.appointment_quick_link_type_id = False
        c = self._composer()
        c.action_bf_insert_booking_link()
        booking = self.env["resource.booking"].search(
            [("bf_source", "=", "onetime")], order="id desc", limit=1)
        self.assertTrue(booking.type_id.is_public)

    def test_partner_button_opens_the_copy_dialog(self):
        action = self.client.action_bf_booking_link()
        self.assertEqual(action["res_model"], "bf.appointment.onetime.wizard")
        assistant = self.env["bf.appointment.onetime.wizard"].browse(action["res_id"])
        self.assertEqual(assistant.state, "done")
        booking = self.env["resource.booking"].search(
            [("bf_source", "=", "onetime")], order="id desc", limit=1)
        self.assertIn(booking.access_token, assistant.url)

    def test_copy_button_leaves_the_body_untouched(self):
        """Copier n'écrit rien dans le message : c'est tout son intérêt."""
        c = self._composer(body="<p>Mon texte</p>")
        avant = str(c.body)
        action = c.action_bf_copy_booking_link()
        self.assertEqual(str(c.body), avant, "le corps a été modifié")
        self.assertEqual(action["res_model"], "bf.appointment.onetime.wizard")
        assistant = self.env["bf.appointment.onetime.wizard"].browse(action["res_id"])
        self.assertIn("/appointment/b/", assistant.url)
        self.assertEqual(assistant.state, "done")

    def test_both_buttons_share_the_same_sequence(self):
        """🔴 Insérer et copier doivent produire le MÊME lien.

        Deux copies de la séquence divergeraient au premier ajustement.
        """
        c1 = self._composer()
        c1.action_bf_insert_booking_link()
        par_insertion = self.env["resource.booking"].search(
            [("bf_source", "=", "onetime")], order="id desc", limit=1)
        c2 = self._composer()
        c2.action_bf_copy_booking_link()
        par_copie = self.env["resource.booking"].search(
            [("bf_source", "=", "onetime")], order="id desc", limit=1)
        self.assertNotEqual(par_insertion, par_copie, "deux liens distincts attendus")
        for champ in ("type_id", "bf_source", "link_single_use"):
            self.assertEqual(par_insertion[champ], par_copie[champ], champ)
        self.assertEqual(par_insertion.partner_ids, par_copie.partner_ids)

    def test_copy_dialog_shows_the_expiry(self):
        c = self._composer()
        action = c.action_bf_copy_booking_link()
        assistant = self.env["bf.appointment.onetime.wizard"].browse(action["res_id"])
        self.assertTrue(assistant.expires_display)
        self.assertNotEqual(assistant.expires_display, "",
                            "la date d'expiration doit être annoncée")

    def test_shared_factory_is_the_single_source(self):
        """🔴 L'assistant, le compositeur et le contact doivent produire la
        MÊME chose. Trois copies de la séquence divergeraient au premier
        ajustement."""
        w = self.env["bf.appointment.onetime.wizard"].create({
            "type_id": self.booking_type.id, "partner_id": self.client.id})
        w.action_create_link()
        par_assistant = w.booking_id
        c = self._composer()
        c.action_bf_insert_booking_link()
        par_courriel = self.env["resource.booking"].search(
            [("bf_source", "=", "onetime")], order="id desc", limit=1)
        for champ in ("bf_source", "link_single_use", "type_id"):
            self.assertEqual(par_assistant[champ], par_courriel[champ], champ)
        self.assertFalse(par_assistant.start)
        self.assertFalse(par_courriel.start)

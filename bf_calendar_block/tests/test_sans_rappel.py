# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Plages sans rappel."""

from datetime import datetime, timedelta

from odoo import Command, fields
from odoo.tests import Form, TransactionCase, tagged

from odoo.addons.bf_calendar_block.hooks import aligner_plages_a_venir, types_du_module


@tagged("post_install", "-at_install", "bf_calendar_block")
class TestSansRappel(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(
            cls.env.context, tracking_disable=True, no_mail_to_attendees=True, skip_nc_sync=True))
        cls.Event = cls.env["calendar.event"]
        cls.prod = cls.env.ref("bf_calendar_block.block_production")
        cls.focus = cls.env.ref("bf_calendar_block.block_focus")
        cls.client = cls.env["calendar.event.type"].create({"name": "Client essai"})
        cls.rappel = cls.env["calendar.alarm"].create({
            "name": "Rappel essai", "alarm_type": "notification", "duration": 7, "interval": "minutes"})
        # Un défaut de rappel connu, quel que soit le réglage du locataire.
        cls.env["ir.config_parameter"].sudo().set_param("bf_email_management.default_alarm_minutes", "1,15")
        cls.debut = fields.Datetime.now().replace(microsecond=0) + timedelta(days=3)

    def _creer(self, **vals):
        base = {"name": "Plage essai", "start": self.debut, "stop": self.debut + timedelta(hours=2)}
        base.update(vals)
        return self.Event.create(base)

    def test_quatre_types_sans_rappel(self):
        for t in types_du_module(self.env):
            self.assertTrue(t.bf_block_no_alarm, t.name)

    def test_plage_par_son_type(self):
        self.assertFalse(self._creer(bf_block_type_id=self.prod.id).alarm_ids)

    def test_plage_par_sa_seule_etiquette(self):
        self.assertFalse(self._creer(categ_ids=[Command.set([self.client.id, self.prod.id])]).alarm_ids)
        self.assertFalse(self._creer(categ_ids=[Command.link(self.focus.id)]).alarm_ids)

    def test_plage_par_le_contexte(self):
        # L'appel ne dit rien des étiquettes ; le type vient des défauts du contexte
        # (vue filtrée sur une étiquette, création rapide).
        Event = self.Event
        plage = Event.with_context(default_categ_ids=[Command.set(self.prod.ids)]).create({
            "name": "Plage contexte", "start": self.debut, "stop": self.debut + timedelta(hours=1)})
        self.assertEqual(plage.bf_block_type_id, self.prod)
        self.assertFalse(plage.alarm_ids)
        plage = Event.with_context(default_bf_block_type_id=self.focus.id).create({
            "name": "Plage contexte 2", "start": self.debut, "stop": self.debut + timedelta(hours=1)})
        self.assertEqual(plage.bf_block_type_id, self.focus)
        self.assertFalse(plage.alarm_ids)
        plage = Event.with_context(bf_creation_rapide_type_plage=self.prod.id).create({
            "name": "Plage contexte 3", "start": self.debut, "stop": self.debut + timedelta(hours=1)})
        self.assertFalse(plage.alarm_ids)
        if hasattr(self.Event, "_bf_default_alarm_ids"):
            ordinaire = Event.with_context(default_categ_ids=[Command.set(self.client.ids)]).create({
                "name": "Ordinaire contexte", "start": self.debut, "stop": self.debut + timedelta(hours=1)})
            self.assertTrue(ordinaire.alarm_ids)

    def test_rappel_explicite_garde(self):
        plage = self._creer(bf_block_type_id=self.prod.id, alarm_ids=[Command.set(self.rappel.ids)])
        self.assertEqual(plage.alarm_ids, self.rappel)

    def test_evenement_ordinaire_garde_le_defaut(self):
        if not hasattr(self.Event, "_bf_default_alarm_ids"):
            self.skipTest("pas de rappel par défaut sans bf_email_management")
        self.assertTrue(self._creer().alarm_ids)
        self.assertTrue(self._creer(categ_ids=[Command.set(self.client.ids)]).alarm_ids)

    def test_type_sans_la_case_garde_le_defaut(self):
        if not hasattr(self.Event, "_bf_default_alarm_ids"):
            self.skipTest("pas de rappel par défaut sans bf_email_management")
        self.prod.bf_block_no_alarm = False
        self.assertTrue(self._creer(bf_block_type_id=self.prod.id).alarm_ids)

    def test_etiquette_retiree_dans_la_meme_ecriture(self):
        if not hasattr(self.Event, "_bf_default_alarm_ids"):
            self.skipTest("pas de rappel par défaut sans bf_email_management")
        plage = self._creer(categ_ids=[Command.link(self.prod.id), Command.unlink(self.prod.id)])
        self.assertTrue(plage.alarm_ids)

    def test_formulaire(self):
        with Form(self.Event) as f:
            f.name = "Plage formulaire"
            f.start = self.debut
            f.stop = self.debut + timedelta(hours=1)
            f.bf_block_type_id = self.prod
            self.assertFalse(f.alarm_ids)
        self.assertFalse(f.record.alarm_ids)

    def test_aligner_les_plages_a_venir(self):
        avec = [Command.set(self.rappel.ids)]
        futur = self._creer(bf_block_type_id=self.prod.id, alarm_ids=avec)
        passe = self._creer(bf_block_type_id=self.prod.id, alarm_ids=avec,
                            start=datetime(2026, 1, 5, 14), stop=datetime(2026, 1, 5, 15))
        ordinaire = self._creer(alarm_ids=avec)
        serie = self._creer(bf_block_type_id=self.prod.id, alarm_ids=avec, recurrency=True,
                            rrule_type="weekly", end_type="count", count=3,
                            event_tz="UTC",
                            **{["mon", "tue", "wed", "thu", "fri", "sat", "sun"][self.debut.weekday()]: True})
        occurrences = serie.recurrence_id.calendar_event_ids
        self.assertEqual(len(occurrences), 3)
        occurrences[0].with_context(skip_nc_sync=True).write({"start": datetime(2026, 1, 12, 14),
                                                              "stop": datetime(2026, 1, 12, 15)})
        aligner_plages_a_venir(self.env, types_du_module(self.env))
        self.assertFalse(futur.alarm_ids)
        self.assertEqual(passe.alarm_ids, self.rappel, "le passé reste tel quel")
        self.assertEqual(ordinaire.alarm_ids, self.rappel)
        self.assertEqual(occurrences[0].alarm_ids, self.rappel, "occurrence passée de la série")
        self.assertFalse(occurrences[1:].mapped("alarm_ids"))
        self.assertEqual(serie.recurrence_id.calendar_event_ids, occurrences, "la série n'est pas recréée")

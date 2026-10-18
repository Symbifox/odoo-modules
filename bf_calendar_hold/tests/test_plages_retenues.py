# -*- coding: utf-8 -*-
"""Plages retenues pour déplacer une rencontre."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

REQUESTS = "odoo.addons.calendar_nextcloud_sync.models.caldav_backend.requests"


def _reponse(code=201):
    return SimpleNamespace(status_code=code, headers={"ETag": '"e1"'},
                           raise_for_status=lambda: None)


@tagged("post_install", "-at_install", "bf_calendar_hold")
class TestPlagesRetenues(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(
            cls.env.context, tracking_disable=True, no_mail_to_attendees=True,
            skip_nc_sync=True))
        cls.Event = cls.env["calendar.event"]
        cls.alex = cls.env["res.partner"].create({
            "name": "Alex essai", "email": "alex.essai@example.invalid"})
        cls.lundi = datetime(2026, 10, 12, 20, 0)
        cls.rencontre = cls.Event.create({
            "name": "Rencontre statutaire",
            "start": cls.lundi,
            "stop": cls.lundi + timedelta(hours=1),
            "partner_ids": [Command.set([cls.env.user.partner_id.id, cls.alex.id])],
        })

    def _plage(self, debut, rencontre=None, **autres):
        vals = {"bf_hold_for_id": (rencontre or self.rencontre).id,
                "start": debut, "stop": debut + timedelta(minutes=30)}
        vals.update(autres)
        return self.Event.create(vals)

    # -- ce qui fait une plage ---------------------------------------------

    def test_valeurs_proposees_en_mode_retenir(self):
        valeurs = self.Event.with_context(default_bf_hold_for_id=self.rencontre.id).default_get(
            ["name", "bf_hold_for_id", "bf_event_status", "show_as", "partner_ids", "user_id"])
        self.assertEqual(valeurs["name"], "↪ Rencontre statutaire")
        self.assertEqual(valeurs["bf_hold_for_id"], self.rencontre.id)
        self.assertEqual(valeurs["bf_event_status"], "tentative")
        self.assertEqual(valeurs["show_as"], "busy")

    def test_les_defauts_ne_fuient_pas_vers_les_autres_modeles(self):
        partenaire = self.env["res.partner"].with_context(
            default_bf_hold_for_id=self.rencontre.id).create({"name": "Contact neuf"})
        self.assertEqual(partenaire.name, "Contact neuf")

    def test_une_plage_n_invite_personne(self):
        plage = self._plage(
            datetime(2026, 10, 13, 19, 30),
            partner_ids=[Command.set([self.alex.id])],
            name="")
        # Sans active_test : le partenaire du superutilisateur des essais est archivé.
        self.assertEqual(plage.with_context(active_test=False).partner_ids,
                         self.env.user.partner_id)
        self.assertFalse(plage.alarm_ids)
        self.assertEqual(plage.bf_event_status, "tentative")
        self.assertEqual(plage.show_as, "busy")
        self.assertTrue(plage.bf_is_hold)
        self.assertEqual(plage.name, "↪ Rencontre statutaire")
        # La durée de la rencontre, pas celle de la case cliquée.
        self.assertEqual(plage.stop - plage.start, timedelta(hours=1))
        self.assertEqual(self.rencontre.bf_hold_count, 1)
        if "bf_skip_agenda" in plage._fields:
            self.assertTrue(plage.bf_skip_agenda)

    def test_pas_de_plage_pour_une_plage(self):
        plage = self._plage(datetime(2026, 10, 13, 19, 30))
        with self.assertRaises(ValidationError):
            self._plage(datetime(2026, 10, 14, 19, 30), rencontre=plage)

    # -- choisir, libérer ------------------------------------------------

    def test_choisir_deplace_la_rencontre_et_libere_tout(self):
        mardi = self._plage(datetime(2026, 10, 13, 19, 30))
        mercredi = self._plage(datetime(2026, 10, 14, 19, 30))
        mardi.action_bf_hold_choose()
        self.assertEqual(self.rencontre.start, datetime(2026, 10, 13, 19, 30))
        self.assertEqual(self.rencontre.stop, datetime(2026, 10, 13, 20, 30))
        self.assertIn(self.alex, self.rencontre.partner_ids)
        self.assertFalse(mardi.exists())
        self.assertFalse(mercredi.exists())

    def test_choisir_une_occurrence_ne_deplace_qu_elle(self):
        serie = self.Event.create({
            "name": "Statutaire hebdomadaire",
            "start": datetime(2026, 10, 5, 20, 0),
            "stop": datetime(2026, 10, 5, 21, 0),
            "recurrency": True, "rrule_type": "weekly", "interval": 1,
            "end_type": "count", "count": 4, "mon": True,
        })
        occurrences = serie.recurrence_id.calendar_event_ids.sorted("start")
        lundi = occurrences.filtered(lambda e: e.start == self.lundi)
        plage = self._plage(datetime(2026, 10, 13, 19, 30), rencontre=lundi)
        plage.action_bf_hold_choose()
        self.assertEqual(lundi.start, datetime(2026, 10, 13, 19, 30))
        self.assertFalse(lundi.follow_recurrence)
        autres = occurrences - lundi
        self.assertTrue(all(e.start.weekday() == 0 for e in autres))
        self.assertEqual(len(serie.recurrence_id.calendar_event_ids), 4)

    def test_rencontre_annulee_libere_ses_plages(self):
        plage = self._plage(datetime(2026, 10, 13, 19, 30))
        self.rencontre.write({"bf_event_status": "cancelled"})
        self.assertFalse(plage.exists())

    def test_rencontre_supprimee_libere_ses_plages(self):
        rencontre = self.rencontre.copy({"name": "Jetable"})
        plage = self._plage(datetime(2026, 10, 13, 19, 30), rencontre=rencontre)
        rencontre.unlink()
        self.assertFalse(plage.exists())

    def test_glissee_sur_une_plage_libere_les_plages(self):
        """Le cas du 2026-10-10 : la rencontre déplacée à la main sur sa plage."""
        mardi = self._plage(datetime(2026, 10, 13, 19, 30))
        mercredi = self._plage(datetime(2026, 10, 14, 19, 30))
        self.rencontre.write({"start": datetime(2026, 10, 13, 19, 30),
                              "stop": datetime(2026, 10, 13, 20, 30)})
        self.assertFalse(mardi.exists())
        self.assertFalse(mercredi.exists())

    def test_deplacee_ailleurs_garde_ses_plages(self):
        mardi = self._plage(datetime(2026, 10, 13, 19, 30))
        self.rencontre.write({"start": datetime(2026, 10, 12, 21, 0),
                              "stop": datetime(2026, 10, 12, 22, 0)})
        self.assertTrue(mardi.exists())

    def test_le_menage_ne_libere_que_les_plages_passees(self):
        passee = self._plage(fields.Datetime.now() - timedelta(days=2))
        future = self._plage(fields.Datetime.now() + timedelta(days=2))
        self.Event._cron_bf_hold_cleanup()
        self.assertFalse(passee.exists())
        self.assertTrue(future.exists())

    # -- couleur, grille -------------------------------------------------

    def test_couleur_dediee(self):
        plage = self._plage(datetime(2026, 10, 13, 19, 30))
        regle = self.env.ref("bf_calendar_hold.bf_color_rule_hold_line")
        self.assertEqual(plage.color_resolved, regle.color_hex)
        self.assertNotEqual(self.rencontre.color_resolved, regle.color_hex)

    def test_retenir_ouvre_la_grille_sur_la_semaine(self):
        action = self.rencontre.action_bf_hold_slots()
        self.assertEqual(action["context"]["default_bf_hold_for_id"], self.rencontre.id)
        self.assertEqual(action["context"]["initial_date"], "2026-10-12")
        self.assertNotIn("default_name", action["context"])

    # -- Nextcloud -------------------------------------------------------

    def test_la_plage_part_vers_nextcloud_et_en_disparait(self):
        config = self.env["nextcloud.calendar.sync.config"].create({
            "name": "Agenda essai plages",
            "backend_type": "nextcloud",
            "nextcloud_base_url": "https://nc.example.test",
            "caldav_path": "/remote.php/dav/calendars/alice/plages/",
            "nextcloud_user": "alice",
            "nextcloud_app_password": "mdp-essai",
            "sync_direction": "both",
        })
        self.env["ir.config_parameter"].sudo().set_param(
            "calendar_nextcloud_sync.push_via_caldav", "1")
        self.rencontre.with_context(skip_nc_sync=True).x_nc_calendar_id = config
        Event = self.Event.with_context(skip_nc_sync=False)
        with patch(REQUESTS + ".put", return_value=_reponse()) as put:
            plage = Event.create({"bf_hold_for_id": self.rencontre.id,
                                  "start": datetime(2026, 10, 13, 19, 30),
                                  "stop": datetime(2026, 10, 13, 20, 30)})
        self.assertEqual(plage.x_nc_calendar_id, config)
        corps = put.call_args.kwargs["data"].decode("utf-8")
        self.assertIn("STATUS:TENTATIVE", corps)
        self.assertIn("TRANSP:OPAQUE", corps)
        self.assertNotIn("alex.essai", corps)
        with patch(REQUESTS + ".delete", return_value=_reponse(204)) as delete:
            plage.with_context(skip_nc_sync=False).action_bf_hold_release()
        self.assertTrue(delete.called)
        self.assertFalse(plage.exists())

    # -- cas limites ------------------------------------------------------

    def test_h1_une_copie_en_mode_retenir_n_est_pas_une_plage(self):
        autre = self.Event.create({"name": "Autre rencontre",
                                   "start": datetime(2026, 10, 14, 15, 0),
                                   "stop": datetime(2026, 10, 14, 16, 0),
                                   "partner_ids": [Command.set([self.alex.id])]})
        copie = autre.with_context(default_bf_hold_for_id=self.rencontre.id).copy()
        self.assertFalse(copie.bf_hold_for_id)
        neuve = self.Event.with_context(default_bf_hold_for_id=self.rencontre.id).create(
            {"name": "Créée côté serveur", "start": datetime(2026, 10, 15, 15, 0),
             "stop": datetime(2026, 10, 15, 16, 0)})
        self.assertFalse(neuve.bf_hold_for_id)

    def test_h2_les_plages_quittent_odoo_avant_le_deplacement(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "calendar_nextcloud_sync.push_via_caldav", "1")
        plage = self._plage(datetime(2026, 10, 13, 19, 30))
        vues = []
        ecrire = type(self.Event).write

        def espion(records, vals):
            if records.env.context.get("bf_hold_choosing"):
                vues.append(records.bf_hold_ids.ids)
            return ecrire(records, vals)

        with patch.object(type(self.Event), "write", espion):
            plage.action_bf_hold_choose()
        self.assertTrue(vues and all(v == [] for v in vues), vues)

    def test_h3_les_plages_suivent_une_serie_recreee(self):
        config = self.env["nextcloud.calendar.sync.config"].create({
            "name": "Agenda essai h3", "backend_type": "nextcloud",
            "nextcloud_base_url": "https://nc.example.test",
            "caldav_path": "/remote.php/dav/calendars/alice/h3/",
            "nextcloud_user": "alice", "nextcloud_app_password": "x",
            "sync_direction": "nc_to_odoo",
        })
        data = {"uid": "serie-h3", "summary": "Statutaire h3",
                "start": "2026-10-05 20:00:00", "end": "2026-10-05 21:00:00",
                "allday": False, "location": "", "description": "", "attendees": [],
                "rrule": "FREQ=WEEKLY;BYDAY=MO;COUNT=4", "exdates": [],
                "event_tz": "America/Toronto", "alarms": None, "status": None,
                "etag": "v0", "href": "/x/serie-h3.ics"}
        res = self.Event.create_from_nextcloud(dict(data), config.id)
        serie = self.Event.browse(res["id"]).recurrence_id.calendar_event_ids
        lundi = serie.filtered(lambda e: e.start == self.lundi)
        plage = self._plage(datetime(2026, 10, 13, 19, 30), rencontre=lundi)
        # La série change dans Nextcloud (titre) : elle est recréée.
        self.Event.create_from_nextcloud(dict(data, summary="Statutaire renommée", etag="v1"), config.id)
        self.assertFalse(lundi.exists())
        self.assertTrue(plage.exists())
        self.assertEqual(plage.bf_hold_for_id.x_nc_recurrence_id, self.lundi)
        self.assertEqual(plage.bf_hold_for_id.name, "Statutaire renommée")

    def test_h4_la_plage_prend_la_confidentialite(self):
        privee = self.Event.create({"name": "Entrevue confidentielle",
                                    "start": datetime(2026, 10, 16, 15, 0),
                                    "stop": datetime(2026, 10, 16, 16, 0),
                                    "privacy": "private"})
        plage = self._plage(datetime(2026, 10, 17, 15, 0), rencontre=privee)
        self.assertEqual(plage.privacy, "private")

    # -- cas limites, suite ------------------------------------------------

    def _serie_nextcloud(self, nom):
        config = self.env["nextcloud.calendar.sync.config"].create({
            "name": "Agenda essai %s" % nom, "backend_type": "nextcloud",
            "nextcloud_base_url": "https://nc.example.test",
            "caldav_path": "/remote.php/dav/calendars/alice/%s/" % nom,
            "nextcloud_user": "alice", "nextcloud_app_password": "x",
            "sync_direction": "nc_to_odoo",
        })
        data = {"uid": "serie-%s" % nom, "summary": "Statutaire %s" % nom,
                "start": "2026-10-05 20:00:00", "end": "2026-10-05 21:00:00",
                "allday": False, "location": "", "description": "", "attendees": [],
                "rrule": "FREQ=WEEKLY;BYDAY=MO;COUNT=4", "exdates": [],
                "event_tz": "America/Toronto", "alarms": None, "status": None,
                "etag": "v0",
                "href": "/remote.php/dav/calendars/alice/%s/serie-%s.ics" % (nom, nom)}
        res = self.Event.create_from_nextcloud(dict(data), config.id)
        serie = self.Event.browse(res["id"]).recurrence_id.calendar_event_ids
        return config, data, serie.filtered(lambda e: e.start == self.lundi)

    def _deplacee_au_mardi(self, data, **autres):
        exception = {"uid": data["uid"], "summary": data["summary"],
                     "start": "2026-10-13 19:30:00", "end": "2026-10-13 20:30:00",
                     "allday": False, "recurrence_id": self.lundi,
                     "recurrence_id_allday": False}
        return dict(data, overrides=[exception], **autres)

    def test_hn1_occurrence_deplacee_dans_nextcloud_sur_sa_plage(self):
        config, data, lundi = self._serie_nextcloud("hn1")
        plage = self._plage(datetime(2026, 10, 13, 19, 30), rencontre=lundi)
        autre = self._plage(datetime(2026, 10, 14, 19, 30), rencontre=lundi)
        self.Event.create_from_nextcloud(self._deplacee_au_mardi(data, etag="v1"), config.id)
        self.assertEqual(lundi.start, datetime(2026, 10, 13, 19, 30))
        self.assertFalse(plage.exists())
        self.assertFalse(autre.exists())

    def test_hn1_meme_geste_quand_la_serie_est_recreee(self):
        config, data, lundi = self._serie_nextcloud("hn1b")
        plage = self._plage(datetime(2026, 10, 13, 19, 30), rencontre=lundi)
        self.Event.create_from_nextcloud(
            self._deplacee_au_mardi(data, etag="v1", summary="Statutaire renommée"), config.id)
        self.assertFalse(plage.exists())

    def test_hn1_deplacee_ailleurs_garde_ses_plages(self):
        config, data, lundi = self._serie_nextcloud("hn1c")
        plage = self._plage(datetime(2026, 10, 15, 19, 30), rencontre=lundi)
        self.Event.create_from_nextcloud(self._deplacee_au_mardi(data, etag="v1"), config.id)
        self.assertTrue(plage.exists())
        self.assertEqual(plage.bf_hold_for_id, lundi)

    def _plage_google(self):
        google = self.env["nextcloud.calendar.sync.config"].create({
            "name": "Google essai", "backend_type": "google",
            "google_calendar_id": "primary", "calendar_owner_id": self.env.user.id,
            "sync_direction": "both",
        })
        self.env["ir.config_parameter"].sudo().set_param(
            "calendar_nextcloud_sync.push_via_caldav", "1")
        self.rencontre.with_context(skip_nc_sync=True).x_nc_calendar_id = google
        plage = self._plage(datetime(2026, 10, 13, 19, 30))
        self.assertEqual(plage.x_nc_calendar_id, google)
        plage.with_context(skip_nc_sync=True, skip_google_sync=True).x_nc_uid = "g-essai"
        return google, plage

    def test_hn2_choisir_retire_les_plages_d_un_agenda_google(self):
        google, plage = self._plage_google()
        Google = type(self.env["calendar.google.backend"])
        with patch.object(Google, "push_delete", return_value=True) as suppression:
            plage.action_bf_hold_choose()
        self.assertFalse(plage.exists())
        self.assertEqual(self.rencontre.start, datetime(2026, 10, 13, 19, 30))
        suppression.assert_called_once_with(google, "g-essai")

    def test_hm1_google_intact_si_le_deplacement_echoue(self):
        _google, plage = self._plage_google()
        Google = type(self.env["calendar.google.backend"])
        ecrire = type(self.Event).write

        def refus(records, vals):
            if records.env.context.get("bf_hold_choosing"):
                raise ValidationError("conflit de réservation")
            return ecrire(records, vals)

        with patch.object(Google, "push_delete", return_value=True) as suppression, \
                patch.object(type(self.Event), "write", refus):
            with self.assertRaises(ValidationError):
                plage.action_bf_hold_choose()
        self.assertFalse(suppression.called)

    def test_rattacher_par_ecriture_exige_le_droit_sur_la_rencontre(self):
        from odoo.exceptions import AccessError
        from odoo.tests import new_test_user
        autrui = new_test_user(self.env, login="autrui_essai", groups="base.group_user")
        privee = self.Event.create({"name": "Entrevue essai",
                                    "start": datetime(2026, 10, 16, 15, 0),
                                    "stop": datetime(2026, 10, 16, 16, 0),
                                    "privacy": "private"})
        sienne = self.Event.with_user(autrui).create({
            "name": "La sienne", "start": datetime(2026, 10, 17, 15, 0),
            "stop": datetime(2026, 10, 17, 16, 0),
            "partner_ids": [Command.set(autrui.partner_id.ids)]})
        with self.assertRaises(AccessError):
            sienne.with_user(autrui).write({"bf_hold_for_id": privee.id})

    def test_le_mode_retenir_tient_dans_une_cle_default(self):
        action = self.rencontre.action_bf_hold_slots()
        self.assertEqual(set(action["context"]), {"default_bf_hold_for_id", "initial_date"})

    # -- 18.0.1.0.2 ---------------------------------------------------------

    def test_la_plage_suit_la_confidentialite_changee_apres_coup(self):
        plage = self._plage(datetime(2026, 10, 13, 19, 30))
        self.assertNotEqual(plage.privacy, "private")
        self.rencontre.write({"privacy": "private"})
        self.assertEqual(plage.privacy, "private")
        self.rencontre.write({"privacy": "public"})
        self.assertEqual(plage.privacy, "public")

    def test_la_confidentialite_suit_meme_ecrite_par_une_assistante(self):
        from odoo.tests import new_test_user
        plage = self._plage(datetime(2026, 10, 13, 19, 30))
        assistante = new_test_user(self.env, login="assistante_essai", groups="base.group_user")
        self.rencontre.with_user(assistante).write({"privacy": "private"})
        self.assertEqual(plage.privacy, "private")

    def test_rattachee_par_ecriture_elle_prend_la_confidentialite(self):
        self.rencontre.write({"privacy": "private"})
        evenement = self.Event.create({
            "name": "Créneau libre", "start": datetime(2026, 10, 14, 15, 0),
            "stop": datetime(2026, 10, 14, 16, 0), "privacy": "public"})
        evenement.write({"bf_hold_for_id": self.rencontre.id})
        self.assertEqual(evenement.privacy, "private")

    def test_create_from_nextcloud_garde_la_forme_modele(self):
        # Le relevé n8n l'appelle en JSON-RPC sans ids : `[données, config]`.
        methode = type(self.env["calendar.event"]).create_from_nextcloud
        self.assertEqual(getattr(methode, "_api", None), "model")

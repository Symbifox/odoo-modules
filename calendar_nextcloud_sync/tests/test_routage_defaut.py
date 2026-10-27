# Rattachement d'office d'un événement neuf à une config de synchro, et poussée vers
# une config archivée. Mesuré en production : la config par défaut archivée, le repli
# « première config active de l'organisateur » rattachait les événements personnels
# d'un usager à une config qui reflète l'agenda d'un autre organisme, et ils y partaient.
from unittest.mock import patch

from odoo.tests import TransactionCase, new_test_user, tagged

PARAM = "calendar_nextcloud_sync.default_calendar_id"


@tagged("post_install", "-at_install", "calendar_nextcloud_sync")
class TestRoutageDefaut(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env["nextcloud.calendar.sync.config"].sudo()
        cls.ICP = cls.env["ir.config_parameter"].sudo()
        # A possède deux configs (son agenda et ses non-dispos), B une seule, C aucune.
        cls.usager_a = new_test_user(cls.env, login="routage_a", groups="base.group_user")
        cls.usager_b = new_test_user(cls.env, login="routage_b", groups="base.group_user")
        cls.usager_c = new_test_user(cls.env, login="routage_c", groups="base.group_user")
        cls.agenda_a = cls._config("routage-agenda-a", cls.usager_a)
        cls.non_dispos_a = cls._config("routage-non-dispos-a", cls.usager_a)
        cls.agenda_b = cls._config("routage-agenda-b", cls.usager_b)

    @classmethod
    def _config(cls, nom, owner, **vals):
        return cls.Config.create({
            "name": nom,
            "calendar_owner_id": owner.id,
            "nextcloud_base_url": "https://nc.invalid",
            "caldav_path": "/remote.php/dav/calendars/%s/personal/" % nom,
            "nextcloud_user": nom,
            "nextcloud_app_password": "mdp-routage",
            **vals,
        })

    def setUp(self):
        super().setUp()
        self.ICP.set_param(PARAM, str(self.agenda_a.id))

    def _creer(self, user, ctx=None, **vals):
        return self.env["calendar.event"].with_context(**({"skip_nc_sync": True} if ctx is None else ctx)).create({
            "name": "routage", "user_id": user.id,
            "start": "2026-11-02 14:00:00", "stop": "2026-11-02 15:00:00", **vals,
        })

    # Comportement gardé

    def test_defaut_actif_possede_par_l_organisateur(self):
        self.assertEqual(self._creer(self.usager_a).x_nc_calendar_id, self.agenda_a)

    def test_organisateur_a_une_seule_config(self):
        self.assertEqual(self._creer(self.usager_b).x_nc_calendar_id, self.agenda_b)

    def test_organisateur_sans_config_prend_le_defaut(self):
        self.assertEqual(self._creer(self.usager_c).x_nc_calendar_id, self.agenda_a)

    def test_config_donnee_est_gardee(self):
        self.agenda_a.active = False
        ev = self._creer(self.usager_a, x_nc_calendar_id=self.non_dispos_a.id)
        self.assertEqual(ev.x_nc_calendar_id, self.non_dispos_a)
        self.assertTrue(ev.x_nc_uid)

    def test_defaut_a_zero_ne_route_rien(self):
        self.ICP.set_param(PARAM, "0")
        self.assertFalse(self._creer(self.usager_b).x_nc_calendar_id)

    def test_lot_de_plusieurs_organisateurs(self):
        evs = self.env["calendar.event"].with_context(skip_nc_sync=True).create([
            {"name": "lot", "user_id": u.id, "start": "2026-11-02 14:00:00", "stop": "2026-11-02 15:00:00"}
            for u in (self.usager_a, self.usager_b, self.usager_c)
        ])
        self.assertEqual(evs.mapped("x_nc_calendar_id"), self.agenda_a | self.agenda_b)
        self.assertEqual([e.x_nc_calendar_id for e in evs], [self.agenda_a, self.agenda_b, self.agenda_a])

    # Règle 1 : un défaut archivé, disparu ou illisible ne route plus rien

    def test_defaut_archive_ne_route_pas_vers_l_autre_config(self):
        # Un paramètre resté sur une config archivée (avant ce correctif, ou récrit à la main).
        self.agenda_a.active = False
        self.ICP.set_param(PARAM, str(self.agenda_a.id))
        for user in (self.usager_a, self.usager_b, self.usager_c):
            ev = self._creer(user)
            self.assertFalse(ev.x_nc_calendar_id, user.login)
            self.assertFalse(ev.x_nc_uid, user.login)
            self.assertFalse(ev.x_sync_source, user.login)

    def test_defaut_disparu_ne_route_rien(self):
        mort = self._config("routage-mort", self.usager_b)
        mort_id = mort.id
        mort.unlink()
        self.ICP.set_param(PARAM, str(mort_id))
        self.assertFalse(self._creer(self.usager_b).x_nc_calendar_id)

    def test_parametre_illisible(self):
        self.ICP.set_param(PARAM, "abc")
        self.assertFalse(self._creer(self.usager_b).x_nc_calendar_id)
        self.assertFalse(self.env["res.config.settings"].get_values().get("nc_sync_default_calendar_id"))

    # Règle 2 : repli sur l'organisateur seulement s'il n'a qu'UNE config active

    def test_plusieurs_configs_sans_le_defaut_ne_route_rien(self):
        self.ICP.set_param(PARAM, str(self.agenda_b.id))
        ev = self._creer(self.usager_a)
        self.assertFalse(ev.x_nc_calendar_id)
        self.assertFalse(ev.x_nc_uid)
        self.assertFalse(ev.x_sync_source)

    def test_une_seule_config_active_parmi_plusieurs(self):
        self.ICP.set_param(PARAM, str(self.agenda_b.id))
        self.non_dispos_a.active = False
        self.assertEqual(self._creer(self.usager_a).x_nc_calendar_id, self.agenda_a)

    def test_cle_a_faux_ne_marque_rien(self):
        self.ICP.set_param(PARAM, str(self.agenda_b.id))
        ev = self._creer(self.usager_a, x_nc_calendar_id=False)
        self.assertFalse(ev.x_nc_calendar_id)
        self.assertFalse(ev.x_nc_uid)
        self.assertFalse(ev.x_sync_source)

    # Règle 3 : archiver ou supprimer la config par défaut remet le défaut à 0

    def _archiver_remet_a_zero(self, archiver):
        archiver(self.agenda_a)
        self.assertEqual(self.ICP.get_param(PARAM), "0")
        self.assertIn("default", self.agenda_a.last_sync_message or "")
        self.assertFalse(self._creer(self.usager_c).x_nc_calendar_id)

    def test_archiver_le_defaut(self):
        self._archiver_remet_a_zero(lambda c: c.action_archive())

    def test_ecrire_active_faux_sur_le_defaut(self):
        self._archiver_remet_a_zero(lambda c: c.write({"active": False}))

    def test_archiver_en_lot_avec_le_defaut(self):
        self._archiver_remet_a_zero(lambda c: (c | self.agenda_b).action_archive())
        self.assertFalse(self.agenda_b.last_sync_message)

    def test_supprimer_le_defaut(self):
        self.agenda_a.unlink()
        self.assertEqual(self.ICP.get_param(PARAM), "0")

    def test_archiver_une_autre_config_garde_le_defaut(self):
        self.non_dispos_a.action_archive()
        self.agenda_b.unlink()
        self.assertEqual(self.ICP.get_param(PARAM), str(self.agenda_a.id))
        self.assertEqual(self._creer(self.usager_a).x_nc_calendar_id, self.agenda_a)

    def test_reactiver_le_defaut_le_garde(self):
        self.agenda_a.active = False
        self.ICP.set_param(PARAM, str(self.agenda_a.id))
        self.agenda_a.action_unarchive()
        self.assertEqual(self.ICP.get_param(PARAM), str(self.agenda_a.id))
        self.assertEqual(self._creer(self.usager_a).x_nc_calendar_id, self.agenda_a)

    def test_reglages_n_affichent_pas_un_defaut_archive(self):
        Settings = self.env["res.config.settings"]
        self.assertEqual(Settings.get_values().get("nc_sync_default_calendar_id"), self.agenda_a.id)
        self.agenda_a.active = False
        self.ICP.set_param(PARAM, str(self.agenda_a.id))
        self.assertFalse(Settings.get_values().get("nc_sync_default_calendar_id"))

    # Une config archivée ne reçoit plus aucune poussée

    def test_config_archivee_ne_recoit_plus_rien(self):
        self.ICP.set_param("calendar_nextcloud_sync.push_via_caldav", "1")
        Event = type(self.env["calendar.event"])
        pousses = []

        def faux(event_self, action, event, config):
            pousses.append((action, config.name))

        with patch.object(Event, "_push_caldav", faux):
            ev = self._creer(self.usager_b, ctx={})
            self.assertEqual(pousses, [("create", "routage-agenda-b")])
            self.agenda_b.action_archive()
            ev.write({"name": "routage modifié"})
            self._creer(self.usager_b, ctx={}, x_nc_calendar_id=self.agenda_b.id)
            ev.unlink()
        self.assertEqual(pousses, [("create", "routage-agenda-b")])

    def test_config_google_archivee_ne_recoit_plus_rien(self):
        google = self._config("routage-google", self.usager_b, backend_type="google",
                              google_calendar_id="primary")
        Backend = type(self.env["calendar.google.backend"])
        appels = []
        with patch.object(Backend, "push_create", lambda s, c, e: appels.append("create") or ("g1", "e1")), \
             patch.object(Backend, "push_update", lambda s, c, e: appels.append("update") or "e2"), \
             patch.object(Backend, "push_delete", lambda s, c, g: appels.append("delete")):
            ev = self._creer(self.usager_b, ctx={}, x_nc_calendar_id=google.id)
            self.assertEqual(appels, ["create"])
            google.action_archive()
            ev.write({"name": "routage modifié"})
            ev.unlink()
        self.assertEqual(appels, ["create"])

    def test_retouches_ne_visent_pas_une_config_archivee(self):
        # Les retouches (CLASS, CATEGORIES, exclusions de séries) passent par
        # _bf_nc_config_pousse et non par _trigger_sync_webhook.
        ev = self._creer(self.usager_b)
        self.assertEqual(ev._bf_nc_config_pousse(), self.agenda_b)
        self.agenda_b.action_archive()
        self.assertFalse(ev._bf_nc_config_pousse())

    def test_defaut_du_contexte_garde_quand_rien_n_est_route(self):
        # L'écran « événements de cette config » pose default_x_nc_calendar_id : un
        # organisateur à plusieurs configs n'est routé nulle part, le contexte décide.
        self.ICP.set_param(PARAM, str(self.agenda_b.id))
        ev = self._creer(self.usager_a, ctx={"skip_nc_sync": True,
                                             "default_x_nc_calendar_id": self.non_dispos_a.id})
        self.assertEqual(ev.x_nc_calendar_id, self.non_dispos_a)
        # Marqué comme un rattachement explicite : sans UID, la poussée échoue.
        self.assertTrue(ev.x_nc_uid)
        self.assertEqual(ev.x_sync_source, "odoo")

    def test_defaut_du_contexte_l_emporte_sur_le_routage(self):
        ev = self._creer(self.usager_b, ctx={"skip_nc_sync": True,
                                             "default_x_nc_calendar_id": self.non_dispos_a.id})
        self.assertEqual(ev.x_nc_calendar_id, self.non_dispos_a)
        self.assertTrue(ev.x_nc_uid)

    def test_poussee_manuelle_refusee_sur_une_config_archivee(self):
        ev = self._creer(self.usager_b)
        self.agenda_b.webhook_url = "https://n8n.invalid/x"
        self.agenda_b.action_archive()
        Event = type(self.env["calendar.event"])
        with patch.object(Event, "_trigger_sync_webhook", lambda s, a: self.fail("poussée")):
            res = self.agenda_b.action_push_to_nextcloud()
        self.assertIn("archived", str(res))
        self.assertFalse(ev.x_caldav_href)

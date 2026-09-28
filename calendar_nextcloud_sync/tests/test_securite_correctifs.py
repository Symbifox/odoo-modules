# -*- coding: utf-8 -*-
"""Correctifs de sécurité du lot « calsync ».

1. Le mot de passe d'application Nextcloud et le secret du webhook se
   lisaient EN CLAIR par n'importe quel usager interne (`search_read`) : le
   calcul déchiffre avec la clé lue en sudo. Ces champs sont désormais
   réservés à `base.group_system`, et la poussée d'un usager ordinaire passe
   par sudo pour continuer de fonctionner.
2. Le retour OAuth Google choisissait le compte cible d'après `state` seul :
   un collègue lançait le flux, envoyait l'URL de consentement à sa victime,
   et les jetons Google de la victime atterrissaient sur SON compte. Le state
   expire maintenant après 10 minutes et, si une session est ouverte, elle
   doit appartenir au propriétaire du state.
3. N'importe quel usager pouvait déconnecter l'agenda Google d'un autre.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

CTRL = "odoo.addons.calendar_nextcloud_sync.controllers.google_oauth"


@tagged("post_install", "-at_install", "calendar_nextcloud_sync")
class TestSecretsConfig(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.config = cls.env["nextcloud.calendar.sync.config"].create({
            "name": "Agenda ",
            "backend_type": "nextcloud",
            "nextcloud_base_url": "https://nc.example.test",
            "caldav_path": "/remote.php/dav/calendars/alice/perso/",
            "nextcloud_user": "alice",
            "nextcloud_app_password": "mdp-en-clair-",
            "webhook_secret": "secret-webhook-en-clair--0123456789",
            "sync_direction": "both",
        })
        cls.employe = new_test_user(
            cls.env, login="employe_essai", groups="base.group_user")

    def test_usager_ordinaire_ne_lit_pas_les_secrets(self):
        Config = self.env["nextcloud.calendar.sync.config"].with_user(
            self.employe)
        champs = [
            "nextcloud_app_password", "webhook_secret",
            "nextcloud_app_password_encrypted", "webhook_secret_encrypted",
        ]
        for champ in champs:
            try:
                rows = Config.search_read(
                    [("id", "=", self.config.id)], ["name", champ])
            except AccessError:
                continue
            valeur = rows and rows[0].get(champ)
            self.assertFalse(
                valeur, "un usager interne lit %s : %r" % (champ, valeur))

    def test_poussee_par_usager_ordinaire_fonctionne(self):
        """La poussée CalDAV déclenchée par un usager ordinaire obtient
        toujours le mot de passe, sans erreur de droits."""
        vus = []

        def faux_push(backend, config, event, payload=None):
            vus.append(backend._auth(config))
            return ("/remote.php/dav/calendars/alice/perso/x.ics", '"e1"')

        Backend = type(self.env["calendar.caldav.backend"])
        now = fields.Datetime.now()
        with patch.object(Backend, "push", faux_push):
            self.env["calendar.event"].with_user(self.employe).create({
                "name": "Rencontre ",
                "start": now,
                "stop": now + timedelta(hours=1),
                "x_nc_calendar_id": self.config.id,
            })
        self.assertEqual(vus, [("alice", "mdp-en-clair-")])


@tagged("post_install", "-at_install", "calendar_nextcloud_sync")
class TestDeconnexionGoogle(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(
            cls.env, login="alice_essai", groups="base.group_user")
        cls.bob = new_test_user(
            cls.env, login="bob_essai", groups="base.group_user")
        cls.admin = new_test_user(
            cls.env, login="admin_essai",
            groups="base.group_user,base.group_system")
        cls.alice.sudo().write({
            "x_google_oauth_refresh_token_encrypted": "jeton",
            "x_google_email": "alice@example.test",
        })

    def test_collegue_ne_deconnecte_pas_autrui(self):
        with self.assertRaises(AccessError):
            self.alice.with_user(self.bob).action_disconnect_google_calendar()
        self.assertEqual(self.alice.x_google_email, "alice@example.test")

    def test_soi_meme_et_admin_peuvent(self):
        self.alice.with_user(self.alice).action_disconnect_google_calendar()
        self.assertFalse(self.alice.x_google_email)
        self.bob.sudo().x_google_email = "bob@example.test"
        self.bob.with_user(self.admin).action_disconnect_google_calendar()
        self.assertFalse(self.bob.x_google_email)


class _FauxFlow:
    code_verifier = None

    def fetch_token(self, code=None):
        self.credentials = SimpleNamespace(
            token="acces-", refresh_token="rafraichir-",
            expiry=None, scopes=[])


@tagged("post_install", "-at_install", "calendar_nextcloud_sync")
class TestRetourOAuth(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.attaquant = new_test_user(
            cls.env, login="attaquant_essai", password="attaquant_essai",
            groups="base.group_user")
        cls.victime = new_test_user(
            cls.env, login="victime_essai", password="victime_essai",
            groups="base.group_user")

    def _retour(self, state):
        with patch(CTRL + "._build_flow", return_value=_FauxFlow()):
            return self.url_open(
                "/bf_calendar/google/oauth/callback?code=c&state=%s" % state,
                allow_redirects=False)

    def _connecte(self, user):
        user.invalidate_recordset()
        return bool(user.sudo().x_google_oauth_refresh_token_encrypted)

    def test_session_d_un_autre_refusee(self):
        state = self.attaquant._generate_oauth_state()
        self.authenticate("victime_essai", "victime_essai")
        self._retour(state)
        self.assertFalse(self._connecte(self.attaquant),
                         "les jetons de la victime ont atterri chez l'attaquant")
        self.assertFalse(self._connecte(self.victime))

    def test_state_expire_refuse(self):
        state = self.attaquant._generate_oauth_state()
        self.attaquant.sudo().x_google_oauth_state_date = (
            fields.Datetime.now() - timedelta(minutes=30))
        self.authenticate("attaquant_essai", "attaquant_essai")
        self._retour(state)
        self.assertFalse(self._connecte(self.attaquant))

    def test_state_a_usage_unique(self):
        state = self.victime._generate_oauth_state()
        self.authenticate("victime_essai", "victime_essai")
        self._retour(state)
        self.assertTrue(self._connecte(self.victime))
        self.victime.sudo().x_google_oauth_refresh_token_encrypted = False
        self._retour(state)
        self.assertFalse(self._connecte(self.victime))

    def test_flux_normal_sans_session(self):
        """Le retour sans cookie de session reste accepté (state frais)."""
        state = self.victime._generate_oauth_state()
        self._retour(state)
        self.assertTrue(self._connecte(self.victime))


@tagged("post_install", "-at_install", "calendar_nextcloud_sync")
class TestPousseeGoogleUsagerOrdinaire(TransactionCase):
    """La poussée Google écrivait l'état de synchronisation sur la
    configuration avec les droits de l'usager ordinaire (lecture seule) :
    AccessError avalée, état jamais mis à jour."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.employe = new_test_user(
            cls.env, login="employe_g_essai", groups="base.group_user")
        cls.config = cls.env["nextcloud.calendar.sync.config"].create({
            "name": "Agenda Google ",
            "backend_type": "google",
            "google_calendar_id": "primary",
            "calendar_owner_id": cls.employe.id,
            "sync_direction": "both",
        })

    def test_etat_ecrit_apres_poussee(self):
        Backend = type(self.env["calendar.google.backend"])
        now = fields.Datetime.now()
        with patch.object(Backend, "push_create",
                          lambda b, config, event: ("gid-", '"e1"')):
            self.env["calendar.event"].with_user(self.employe).create({
                "name": "Rencontre Google ",
                "start": now,
                "stop": now + timedelta(hours=1),
                "x_nc_calendar_id": self.config.id,
            })
        self.config.invalidate_recordset()
        self.assertEqual(self.config.last_sync_status, "success")
        self.assertIn("Google create", self.config.last_sync_message)

"""Tests for the "+ Nextcloud Talk" button.

The bug these exist for: the button was declared `invisible="... or not id"`,
so it never appeared where people actually create meetings — the calendar's
quick-create popover, where the event has no id yet. Nothing failed; the button
was simply absent, which is why it went unnoticed until someone asked.

The click itself is handled in JS and needs a browser, so what is pinned here
is everything underneath it: that the view no longer hides the button on an
unsaved record, that the asset the JS lives in is actually shipped, and that
the method the JS calls works without a record and refuses anonymous callers.
"""

from unittest.mock import patch

from lxml import etree

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged
from odoo.modules.module import get_manifest
from odoo.tools.misc import file_path


class _FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@tagged("post_install", "-at_install")
class TestNcTalkButton(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        params = cls.env["ir.config_parameter"].sudo()
        params.set_param("bf_calendar_nc_talk.url", "https://nc.example.com")
        params.set_param("bf_calendar_nc_talk.user", "fox-bot")
        params.set_param("bf_calendar_nc_talk.password", "aaaaa-bbbbb-ccccc-ddddd-eeeee")

    # --- the button is reachable ---------------------------------------

    def _button_in(self, view_xmlid):
        view = self.env.ref(view_xmlid)
        arch = self.env["calendar.event"].get_view(view.id, "form")["arch"]
        buttons = etree.fromstring(arch).xpath(
            "//button[@name='action_set_nc_talk_videocall_location']")
        self.assertTrue(buttons, "the button is missing from %s" % view_xmlid)
        return buttons[0]

    def test_button_does_not_depend_on_a_saved_record_in_quick_create(self):
        """Booking straight on the calendar is where this button is wanted."""
        button = self._button_in("calendar.view_calendar_event_form_quick_create")
        modifier = button.get("invisible", "")
        self.assertNotIn(
            "id", modifier.split(),
            "'not id' hides the button in the quick-create popover, where the "
            "event is never saved yet. Modifier was: %s" % modifier)

    def test_button_does_not_depend_on_a_saved_record_in_the_form(self):
        button = self._button_in("calendar.view_calendar_event_form")
        modifier = button.get("invisible", "")
        self.assertNotIn("id", modifier.split(),
                         "same problem when creating from the form. Modifier: %s" % modifier)

    def test_button_matches_cores_own_visibility(self):
        """Ours should appear exactly where '+ Odoo meeting' appears."""
        view = self.env.ref("calendar.view_calendar_event_form_quick_create")
        arch = etree.fromstring(
            self.env["calendar.event"].get_view(view.id, "form")["arch"])
        core = arch.xpath("//button[@name='set_discuss_videocall_location']")[0]
        ours = arch.xpath("//button[@name='action_set_nc_talk_videocall_location']")[0]
        self.assertEqual(ours.get("invisible", ""), core.get("invisible", ""))

    def test_the_javascript_is_shipped(self):
        """A wrong asset path is silent: the bundle just leaves the file out."""
        declared = "bf_calendar_nc_talk/static/src/js/nc_talk_form_controller.js"
        self.assertIn(
            declared,
            get_manifest("bf_calendar_nc_talk")["assets"]["web.assets_backend"])
        file_path(declared)  # raises FileNotFoundError if it is not there

    # --- the method the JS calls ---------------------------------------

    def test_room_is_created_without_any_record(self):
        """This is what lets the quick-create popover work at all."""
        response = _FakeResponse(201, {"ocs": {"data": {"token": "abc12345"}}})
        with patch("odoo.addons.bf_calendar_nc_talk.models.calendar_event."
                   "requests.post", return_value=response):
            url = self.env["calendar.event"].create_nc_talk_room("Kickoff")
        self.assertEqual(url, "https://nc.example.com/call/abc12345")

    def test_room_name_is_passed_to_nextcloud(self):
        response = _FakeResponse(201, {"ocs": {"data": {"token": "abc12345"}}})
        with patch("odoo.addons.bf_calendar_nc_talk.models.calendar_event."
                   "requests.post", return_value=response) as post:
            self.env["calendar.event"].create_nc_talk_room("Suivi trimestriel")
        self.assertEqual(post.call_args.kwargs["data"]["roomName"], "Suivi trimestriel")

    def test_portal_users_cannot_create_rooms(self):
        """No underscore means any client can call it over RPC."""
        portal = self.env["res.users"].create({
            "name": "Portal person",
            "login": "bf_nc_talk_portal",
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])],
        })
        with self.assertRaises(AccessError):
            self.env["calendar.event"].with_user(portal).create_nc_talk_room("Nope")

    # --- failures the user has to be able to act on ---------------------

    def test_a_refused_password_says_what_to_do(self):
        """The raw OCS 401 payload told the user nothing actionable.

        🔴 The language is pinned since 18.0.1.2.0: the module now ships a
        fr_CA catalogue, and OdooBot may read fr_CA depending on the database. A test that
        checks the English SOURCE has to say which language it reads.

        ⚠️ Measured: this message comes out of `_http_error_message`, a MODULE-level
        function. `_()` there sees no `self.env`, so outside an HTTP request it resolves
        to no language and always returns English. Its French is only reachable through
        `request.env.lang` — the browser pass is the only place that measures it.
        """
        self.env["res.lang"]._activate_lang("en_US")
        response = _FakeResponse(
            401, text='{"ocs":{"meta":{"status":"failure","statuscode":401}}}')
        with patch("odoo.addons.bf_calendar_nc_talk.models.calendar_event."
                   "requests.post", return_value=response):
            with self.assertRaises(UserError) as caught:
                self.env["calendar.event"].with_context(
                    lang="en_US").create_nc_talk_room("Kickoff")
        message = str(caught.exception)
        self.assertIn("app password", message)
        self.assertIn("Nextcloud Talk", message)

    def test_the_401_message_has_a_french_twin_in_the_catalogue(self):
        """Le français de ce message existe, même si aucun essai ne peut le faire
        rendre par `_()` (voir l'avertissement ci-dessus) : on le lit au catalogue, qui
        est ce que la page publique servira."""
        import polib
        from pathlib import Path as _P
        catalogue = polib.pofile(str(
            _P(__file__).resolve().parent.parent / "i18n" / "fr_CA.po"))
        entree = next(
            (e for e in catalogue
             if e.msgid.startswith("Nextcloud refused the service account")), None)
        self.assertIsNotNone(entree, "le refus 401 doit être au catalogue")
        self.assertIn("mot de passe d'application", entree.msgstr)

    def test_settings_test_button_reports_empty_fields(self):
        """Same `user`-named-local trap as above, on the settings page."""
        settings = self.env["res.config.settings"].create({
            "bf_nc_talk_url": "https://nc.example.com",
            "bf_nc_talk_user": "fox-bot",
            "bf_nc_talk_password": "",
        })
        with self.assertRaises(UserError):
            settings.action_test_nc_talk_connection()

    def test_missing_configuration_is_reported_before_any_call(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_calendar_nc_talk.password", "")
        # 🔴 La langue est épinglée depuis la 18.0.1.2.0 : le module porte
        # maintenant un catalogue fr_CA, et OdooBot peut lire en fr_CA selon la base.
        # Un essai qui vérifie la SOURCE anglaise doit dire dans quelle langue il lit,
        # sinon il mesure la traduction et tombe sans qu'aucun défaut existe.
        self.env["res.lang"]._activate_lang("en_US")
        evenements = self.env["calendar.event"].with_context(lang="en_US")
        with patch("odoo.addons.bf_calendar_nc_talk.models.calendar_event."
                   "requests.post") as post:
            with self.assertRaises(UserError) as caught:
                evenements.create_nc_talk_room("Kickoff")
        post.assert_not_called()
        # Not a ValueError: `_()` reads frame locals and used to choke on a
        # local named `user` holding the Nextcloud account name.
        self.assertIn("not configured", str(caught.exception))

    def test_missing_configuration_is_reported_in_french_too(self):
        """Le même refus, lu en français : c'est ce que le catalogue a ajouté."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_calendar_nc_talk.password", "")
        self.env["ir.module.module"]._load_module_terms(
            ["bf_calendar_nc_talk"], ["fr_CA"], overwrite=True)
        evenements = self.env["calendar.event"].with_context(lang="fr_CA")
        with patch("odoo.addons.bf_calendar_nc_talk.models.calendar_event."
                   "requests.post") as post:
            with self.assertRaises(UserError) as caught:
                evenements.create_nc_talk_room("Kickoff")
        post.assert_not_called()
        self.assertIn("n'est pas configuré", str(caught.exception))

"""Horloges et photo servies par la politique.

L'agent d'accueil savait deja poser le format de l'heure, la seconde horloge
et la photo ; il ne recevait rien, parce que la politique ne les portait pas.
Ces essais tiennent le contrat qu'il lit : `session.clock` et `user.avatar`."""

import base64
import json
import pathlib

from odoo.tests import TransactionCase, tagged

# PNG 1x1 valide, et un SVG : le second ne doit jamais sortir.
PNG = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA"
    "60e6kgAAAABJRU5ErkJggg=="))
SVG = base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg"/>')


@tagged("post_install", "-at_install")
class TestClockAvatar(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Horloge Inc."})
        cls.org = cls.env["bf.policy.org"].create({
            "company_id": cls.company.id, "domain": "horloge.example",
            "timezone": "America/Montreal",
        })
        cls.user = cls.env["res.users"].create({
            "name": "Kai", "login": "kai@horloge.example", "tz": "America/Montreal",
            "company_id": cls.company.id, "company_ids": [(4, cls.company.id)],
        })

    def _policy(self):
        return self.org.get_policy_json(self.user)

    # --- horloges ------------------------------------------------------------
    def test_defaults_to_24h_and_a_single_clock(self):
        self.assertEqual(self._policy()["session"]["clock"],
                         {"format": "24h", "second_timezone": None})

    def test_org_format_is_served(self):
        self.org.clock_format = "12h"
        self.assertEqual(self._policy()["session"]["clock"]["format"], "12h")

    def test_person_overrides_the_org_format(self):
        self.org.clock_format = "12h"
        override = self.env["bf.policy.user"].create({
            "user_id": self.user.id, "company_id": self.company.id})
        self.assertEqual(override.clock_format, "inherit")
        self.assertEqual(self._policy()["session"]["clock"]["format"], "12h")
        override.clock_format = "24h"
        self.assertEqual(self._policy()["session"]["clock"]["format"], "24h")

    def test_second_clock_shows_the_org_timezone_when_the_person_is_elsewhere(self):
        self.user.tz = "Pacific/Auckland"
        self.assertEqual(self._policy()["session"]["clock"]["second_timezone"],
                         "America/Montreal")

    def test_pinned_machine_timezone_also_decides_the_second_clock(self):
        # Une machine epinglee sur le fuseau de l'org n'a pas de seconde horloge,
        # meme si la personne vit ailleurs : c'est le fuseau EFFECTIF qui compte.
        self.user.tz = "Pacific/Auckland"
        self.env["bf.policy.user"].create({
            "user_id": self.user.id, "company_id": self.company.id,
            "timezone": "America/Montreal"})
        self.assertIsNone(self._policy()["session"]["clock"]["second_timezone"])

    # --- photo ---------------------------------------------------------------
    def test_no_photo_no_avatar_key(self):
        self.assertNotIn("avatar", self._policy()["user"])

    def test_user_photo_is_served(self):
        self.user.image_1920 = PNG
        avatar = self._policy()["user"]["avatar"]
        self.assertTrue(base64.b64decode(avatar).startswith(b"\x89PNG"))

    def test_employee_photo_wins_over_the_user_photo(self):
        if "hr.employee" not in self.env:
            self.skipTest("hr absent")
        self.user.image_1920 = SVG  # ne doit pas etre choisi
        self.env["hr.employee"].create({
            "name": "Kai", "user_id": self.user.id,
            "company_id": self.company.id, "image_1920": PNG})
        self.assertTrue(base64.b64decode(
            self._policy()["user"]["avatar"]).startswith(b"\x89PNG"))

    def test_a_non_raster_photo_is_not_served(self):
        self.user.image_1920 = SVG
        self.assertNotIn("avatar", self._policy()["user"])

    def test_payload_still_validates_against_the_schema(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        self.user.write({"tz": "Pacific/Auckland", "image_1920": PNG})
        schema = json.loads((pathlib.Path(__file__).resolve().parent.parent
                             / "static/schema/policy.v2.json").read_text())
        jsonschema.validate(self._policy(), schema)

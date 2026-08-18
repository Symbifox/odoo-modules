"""Ouverture d'un fil : marquage lu, blocage des images, pièces jointes."""
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestMobileConversation(MobileApiCase):

    def test_opening_a_thread_marks_it_read(self):
        """Le dernier message arrive déjà déplié — le lecteur le voit.

        Sans ce marquage la pastille de non-lus ne se viderait jamais par la
        lecture normale, seulement par une action explicite.
        """
        self.assertEqual(self.inbound.status, "new")
        self.as_owner().get_mobile_conversation("<racine-1@test.invalid>")
        self.assertEqual(self.inbound.status, "read")

    def test_the_whole_thread_is_marked_not_just_the_last_message(self):
        """La liste compte les non-lus PAR FIL ; n'en marquer qu'un laisserait
        la pastille allumée sur un fil qu'il n'y a plus rien à lire."""
        self.as_owner().get_mobile_conversation("<racine-1@test.invalid>")
        unread = self.as_owner().get_mobile_threads(filter_name="unread")
        self.assertNotIn("<racine-1@test.invalid>",
                         [t["thread_key"] for t in unread["threads"]])

    def test_last_message_arrives_full_others_as_previews(self):
        data = self.as_owner().get_mobile_conversation("<racine-1@test.invalid>")
        self.assertEqual(len(data["messages"]), 2)
        self.assertIn("body_html", data["messages"][-1])
        self.assertNotIn("body_html", data["messages"][0])

    def test_messages_come_back_oldest_first(self):
        data = self.as_owner().get_mobile_conversation("<racine-1@test.invalid>")
        self.assertEqual(data["messages"][0]["id"], self.inbound.id)
        self.assertEqual(data["messages"][-1]["id"], self.outbound.id)

    def test_remote_images_are_parked_until_asked_for(self):
        data = self.as_owner().get_mobile_message(self.with_attachment.id)
        import re
        body = data["body_html"]
        self.assertIn('data-blocked-src="https://pisteur.test', body)
        # Naive substring matching would pass on `data-blocked-src="http…"`,
        # so anchor on the attribute boundary: no LIVE src pointing outward.
        self.assertIsNone(re.search(r'[\s"\']src\s*=\s*["\']https?://', body),
                          "une source distante est restée active")
        self.assertEqual(data["blocked_images"], 1)
        # Les images en ligne (cid:) sont locales : on n'y touche pas.
        self.assertIn("cid:", body)

    def test_asking_for_images_restores_them(self):
        data = self.as_owner().get_mobile_message(
            self.with_attachment.id, load_images=True)
        self.assertNotIn("data-blocked-src", data["body_html"])
        self.assertEqual(data["blocked_images"], 0)

    def test_a_full_message_reports_its_attachments(self):
        data = self.as_owner().get_mobile_message(self.with_attachment.id)
        self.assertEqual(len(data["attachments"]), 1)
        attachment = data["attachments"][0]
        self.assertEqual(attachment["name"], "rapport.csv")
        self.assertEqual(attachment["idx"], 0)
        self.assertGreater(attachment["size"], 0)

    def test_attachment_bytes_are_addressed_by_position(self):
        """L'appareil ne nomme jamais un ``ir.attachment`` : il donne un rang."""
        record = self.as_owner().browse(self.with_attachment.id)
        found = record._mobile_attachment_bytes(0)
        self.assertIsNotNone(found)
        name, mimetype, payload = found
        self.assertEqual(name, "rapport.csv")
        self.assertIn(b"colonne A", payload)
        # Hors bornes : rien, pas une exception ni un autre fichier.
        self.assertIsNone(record._mobile_attachment_bytes(9))
        self.assertIsNone(record._mobile_attachment_bytes(-1))

    def test_oversized_attachment_is_refused_before_being_decoded(self):
        record = self.as_owner().browse(self.with_attachment.id)
        from odoo.addons.bf_email_management.models.bf_email_mobile import TOO_LARGE
        self.assertIs(record._mobile_attachment_bytes(0, max_bytes=1), TOO_LARGE)

    def test_unknown_thread_raises(self):
        with self.assertRaises(UserError):
            self.as_owner().get_mobile_conversation("id:99999999")
        with self.assertRaises(UserError):
            self.as_owner().get_mobile_conversation("")

    def test_config_exposes_no_mailbox_credentials(self):
        """``bf.email.account`` porte le mot de passe IMAP en clair ; la charge
        utile mobile ne doit contenir que l'adressage."""
        config = self.as_owner().get_mobile_config()
        serialized = str(config)
        self.assertNotIn("password", serialized)
        self.assertNotIn("imap.test.invalid", serialized)
        self.assertTrue(config["accounts"])
        self.assertEqual(config["accounts"][0]["login"], "owner@test.invalid")

    def test_snooze_presets_land_in_the_users_timezone(self):
        """« Ce soir (18 h) » doit être 18 h à Montréal, pas 18 h UTC."""
        import pytz
        from datetime import datetime
        config = self.as_owner().get_mobile_config()
        tz = pytz.timezone("America/Montreal")
        presets = {p["key"]: p for p in config["snooze_presets"]}

        for key, expected_hour in (("tonight", 18), ("tomorrow", 8), ("nextweek", 8)):
            local = datetime.fromtimestamp(presets[key]["until_ms"] / 1000, tz)
            self.assertEqual(local.hour, expected_hour,
                             f"{key} tombe à {local.hour} h locale")

    def test_odoo_stock_colours_count_as_no_brand(self):
        """Une société encore en violet Odoo n'a rien choisi.

        Le rendre comme « la marque du locataire » peindrait l'app en violet
        Odoo d'usine — exactement ce que ce produit existe pour éviter. L'app
        doit recevoir « rien » et appliquer ses propres couleurs.
        """
        company = self.env.company.sudo()
        if "report_brand_primary" in company._fields:
            company.report_brand_primary = False
        company.primary_color = "#714B67"
        branding = self.as_owner()._mobile_branding()
        self.assertIsNone(branding["primary"])

    def test_a_chosen_colour_is_served(self):
        company = self.env.company.sudo()
        if "report_brand_primary" in company._fields:
            company.report_brand_primary = "#123abc"
        else:
            company.primary_color = "#123abc"
        self.assertEqual(self.as_owner()._mobile_branding()["primary"], "#123ABC")

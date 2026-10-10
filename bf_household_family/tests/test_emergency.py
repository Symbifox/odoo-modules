"""Les fiches d'urgence : vues du foyer, tenues par la personne ou les parents."""
from datetime import timedelta

from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import FamilyCase


@tagged("post_install", "-at_install")
class TestEmergency(FamilyCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Card = cls.env["bf.household.emergency.card"]
        cls.carte_sam = Card.with_user(cls.sam).create({
            "blood_type": "o_neg", "allergies": "Arachides",
            "contact_ids": [(0, 0, {"name": "Robin", "relation": "Sœur", "phone": "555-0101"})],
        })
        cls.carte_lea = Card.with_user(cls.camille).create({"child_id": cls.lea.id, "allergies": "Pénicilline"})

    def test_household_reads_every_card(self):
        for lecteur in (self.camille, self.alex, self.teo):
            lu = self.as_user(lecteur, "bf.household.emergency.card").browse(self.carte_sam.id)
            self.assertEqual(lu.allergies, "Arachides")
            self.assertEqual(lu.contact_ids.phone, "555-0101")

    def test_only_the_person_or_the_parents_write(self):
        with self.assertRaises(AccessError):
            self.as_user(self.camille, "bf.household.emergency.card").browse(self.carte_sam.id).write(
                {"allergies": "Aucune"})
        with self.assertRaises(AccessError):
            self.as_user(self.camille, "bf.household.emergency.contact").browse(
                self.carte_sam.contact_ids.id).write({"phone": "000"})
        with self.assertRaises(AccessError):
            self.as_user(self.camille, "bf.household.emergency.contact").create(
                {"card_id": self.carte_sam.id, "name": "Intrus", "phone": "000"})
        self.as_user(self.alex, "bf.household.emergency.card").browse(self.carte_lea.id).write({"notes": "Doudou"})
        with self.assertRaises(AccessError):
            self.as_user(self.sam, "bf.household.emergency.card").browse(self.carte_lea.id).write({"notes": "X"})

    def test_no_sin_on_a_card(self):
        with self.assertRaises(ValidationError):
            self.as_user(self.sam, "bf.household.emergency.card").browse(self.carte_sam.id).write(
                {"notes": "NAS de Sam : 046 454 286"})

    def test_card_is_copied_only_by_whoever_keeps_it(self):
        with self.assertRaises(AccessError):
            self.as_user(self.camille, "bf.household.emergency.card").browse(self.carte_sam.id).copy(
                {"child_id": self.lea.id})

    def test_cannot_keep_a_card_for_someone_else(self):
        with self.assertRaises(AccessError):
            self.as_user(self.sam, "bf.household.emergency.card").create({"holder_user_id": self.alex.id})

    def test_one_card_per_person(self):
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"), self.env.cr.savepoint():
            self.as_user(self.sam, "bf.household.emergency.card").create({})

    def test_links_are_private_to_whoever_keeps_the_card(self):
        Link = self.env["bf.household.emergency.link"]
        lien, jeton = Link.with_user(self.sam)._bf_create_for(self.carte_sam, "Gardienne", 24)
        self.assertTrue(jeton)
        self.assertNotEqual(lien.sudo().token_hash, jeton, "Seule l'empreinte est gardée.")
        for intrus in (self.camille, self.alex, self.admin):
            try:
                trouves = self.as_user(intrus, "bf.household.emergency.link").search([("id", "=", lien.id)])
            except AccessError:
                trouves = None
            self.assertFalse(trouves)
        with self.assertRaises(AccessError):
            Link.with_user(self.camille)._bf_create_for(self.carte_sam, "Pirate", 24)

    def test_link_lasts_seven_days_at_most(self):
        Link = self.env["bf.household.emergency.link"]
        lien, _jeton = Link.with_user(self.sam)._bf_create_for(self.carte_sam, "Long", 10000)
        self.assertLessEqual(lien.sudo().expires_at, fields.Datetime.now() + timedelta(hours=168, minutes=1))
        with self.assertRaises(ValidationError):
            lien.sudo().write({"expires_at": fields.Datetime.now() + timedelta(days=30)})

    def test_link_is_not_edited_only_withdrawn(self):
        lien, _jeton = self.env["bf.household.emergency.link"].with_user(self.sam)._bf_create_for(
            self.carte_sam, "G", 24)
        with self.assertRaises(AccessError):
            lien.with_user(self.sam).write({"expires_at": fields.Datetime.now() + timedelta(days=6)})
        with self.assertRaises(AccessError):
            lien.with_user(self.sam).write({"revoked": False, "open_count": 0})
        lien.with_user(self.sam).action_revoke()
        self.assertTrue(lien.sudo().revoked)

    def test_find_valid_refuses_expired_and_revoked(self):
        Link = self.env["bf.household.emergency.link"]
        lien, jeton = Link.with_user(self.sam)._bf_create_for(self.carte_sam, "G", 24)
        self.assertEqual(Link._bf_find_valid(jeton), lien.sudo())
        self.assertFalse(Link._bf_find_valid(jeton + "x"))
        self.assertFalse(Link._bf_find_valid(""))
        lien.sudo().expires_at = fields.Datetime.now() - timedelta(minutes=1)
        self.assertFalse(Link._bf_find_valid(jeton))
        lien2, jeton2 = Link.with_user(self.sam)._bf_create_for(self.carte_sam, "G2", 24)
        lien2.with_user(self.sam).action_revoke()
        self.assertFalse(Link._bf_find_valid(jeton2))

    def test_public_values_carry_no_paper_number(self):
        self.env["bf.household.document"].with_user(self.sam).create(
            {"doc_type": "health_card", "number": "SAMS12345678"})
        valeurs = self.carte_sam._bf_public_values()
        texte = " ".join(str(v) for k, v in valeurs.items() if k not in ("card", "contacts"))
        self.assertNotIn("SAMS12345678", texte)

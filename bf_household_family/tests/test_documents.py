"""Les papiers : classe privée, partage exprès, aucun NAS."""
import base64
from datetime import date, timedelta

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from .common import FamilyCase


@tagged("post_install", "-at_install")
class TestDocuments(FamilyCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Doc = cls.env["bf.household.document"]
        cls.passeport_sam = Doc.with_user(cls.sam).create({
            "doc_type": "passport", "number": "AB123456", "issuing_authority": "Canada",
            "expiry_date": date.today() + timedelta(days=100),
        })
        cls.passeport_lea = Doc.with_user(cls.camille).create({
            "doc_type": "passport", "child_id": cls.lea.id, "number": "LE654321",
            "expiry_date": date.today() + timedelta(days=900),
        })

    def test_form_sends_holder_with_child(self):
        """Comme le formulaire : le titulaire invisible arrive à sa valeur par défaut."""
        papier = self.as_user(self.camille, "bf.household.document").create(
            {"doc_type": "passport", "child_id": self.lea.id, "holder_user_id": self.camille.id})
        self.assertFalse(papier.holder_user_id)
        self.assertEqual(papier.child_id, self.lea)
        carte = self.as_user(self.camille, "bf.household.emergency.card").create(
            {"child_id": self.lea.id, "holder_user_id": self.camille.id})
        self.assertFalse(carte.holder_user_id)

    def test_form_shape_by_a_non_parent_is_refused(self):
        with self.assertRaises(AccessError):
            self.as_user(self.sam, "bf.household.document").create(
                {"doc_type": "passport", "child_id": self.lea.id, "holder_user_id": self.sam.id})
        with self.assertRaises(AccessError):
            self.as_user(self.sam, "bf.household.emergency.card").create(
                {"child_id": self.lea.id, "holder_user_id": self.sam.id})

    def test_card_moved_to_a_child_by_a_non_parent_is_refused(self):
        carte = self.as_user(self.sam, "bf.household.emergency.card").create({"allergies": "Pollen"})
        with self.assertRaises(AccessError):
            carte.with_user(self.sam).write({"child_id": self.lea.id})

    def test_shared_reader_cannot_copy_a_paper(self):
        self.passeport_sam.with_user(self.sam).share_user_ids = [(4, self.alex.id)]
        with self.assertRaises(AccessError):
            self.as_user(self.alex, "bf.household.document").browse(self.passeport_sam.id).copy(
                {"holder_user_id": self.alex.id})
        copie = self.as_user(self.sam, "bf.household.document").browse(self.passeport_sam.id).copy()
        self.assertEqual(copie.holder_user_id, self.sam)

    def test_holder_only(self):
        for intrus in (self.camille, self.alex, self.teo, self.admin):
            with self.subTest(intrus=intrus.login):
                try:
                    trouves = self.as_user(intrus, "bf.household.document").search(
                        [("id", "=", self.passeport_sam.id)])
                except AccessError:
                    # Blue Fox n'a même pas le droit de chercher : plus strict encore.
                    trouves = None
                self.assertFalse(trouves)
                with self.assertRaises(AccessError):
                    self.as_user(intrus, "bf.household.document").browse(self.passeport_sam.id).read(["number"])

    def test_both_parents_hold_the_childs_papers(self):
        for parent in (self.camille, self.alex):
            lu = self.as_user(parent, "bf.household.document").browse(self.passeport_lea.id)
            self.assertEqual(lu.number, "LE654321")
        self.as_user(self.alex, "bf.household.document").browse(self.passeport_lea.id).write({"notes": "Tiroir"})
        with self.assertRaises(AccessError):
            self.as_user(self.sam, "bf.household.document").browse(self.passeport_lea.id).read(["number"])

    def test_share_is_read_only(self):
        self.passeport_sam.with_user(self.sam).share_user_ids = [(4, self.alex.id)]
        lu = self.as_user(self.alex, "bf.household.document").browse(self.passeport_sam.id)
        self.assertEqual(lu.number, "AB123456")
        with self.assertRaises(AccessError):
            lu.write({"number": "XX000000"})
        with self.assertRaises(AccessError):
            lu.unlink()
        with self.assertRaises(AccessError):
            self.as_user(self.camille, "bf.household.document").browse(self.passeport_sam.id).read(["number"])

    def test_share_only_with_household_members(self):
        with self.assertRaises(ValidationError):
            self.passeport_sam.with_user(self.sam).share_user_ids = [(4, self.admin.id)]

    def test_cannot_hold_a_paper_for_someone_else(self):
        with self.assertRaises(AccessError):
            self.as_user(self.sam, "bf.household.document").create(
                {"doc_type": "passport", "holder_user_id": self.camille.id})
        with self.assertRaises(AccessError):
            self.as_user(self.sam, "bf.household.document").create(
                {"doc_type": "passport", "child_id": self.lea.id})
        with self.assertRaises(AccessError):
            self.as_user(self.sam, "bf.household.document").browse(self.passeport_sam.id).write(
                {"child_id": self.lea.id})

    def test_private_name_in_display_name(self):
        nom = self.env["bf.household.document"].with_user(self.camille).browse(self.passeport_sam.id).sudo(
        ).with_user(self.camille).display_name
        self.assertEqual(nom, "Private paper")
        self.assertIn("Sam", self.passeport_sam.with_user(self.sam).display_name)

    def test_no_social_insurance_number(self):
        with self.assertRaises(ValidationError):
            self.as_user(self.sam, "bf.household.document").create(
                {"doc_type": "other", "number": "046 454 286"})
        self.as_user(self.sam, "bf.household.document").create({"doc_type": "other", "number": "046 454 287"})
        with self.assertRaises(ValidationError):
            self.as_user(self.sam, "bf.household.document").create(
                {"doc_type": "other", "notes": "Mon NAS : 046-454-286, au cas où"})
        with self.assertRaises(ValidationError):
            self.as_user(self.sam, "bf.household.document").browse(self.passeport_sam.id).write(
                {"issuing_authority": "046454286"})
        self.as_user(self.sam, "bf.household.document").browse(self.passeport_sam.id).write(
            {"notes": "Renouvelé au comptoir 12, dossier 2026-10-09"})
        for forme in ("NAS 046 454 286", "046 - 454 - 286", "046–454–286", "046/454/286"):
            with self.subTest(forme=forme), self.assertRaises(ValidationError):
                self.as_user(self.sam, "bf.household.document").create({"doc_type": "other", "number": forme})

    def test_passport_reminder_is_six_months(self):
        self.assertEqual(self.passeport_sam.reminder_days, 183)
        self.assertEqual(self.passeport_sam.reminder_date, self.passeport_sam.expiry_date - timedelta(days=183))

    def test_reminder_is_a_private_todo_for_the_holders(self):
        self.env["bf.household.document"]._cron_send_reminders()
        tache = self.passeport_sam.sudo().reminder_task_id
        self.assertTrue(tache, "Échéance dans 100 jours, rappel à 183 : dû.")
        self.assertEqual(tache.user_ids, self.sam)
        self.assertFalse(tache.project_id)
        self.assertNotIn("AB123456", tache.name)
        for intrus in (self.camille, self.alex):
            self.assertFalse(self.as_user(intrus, "project.task").search([("id", "=", tache.id)]))
        self.assertFalse(self.passeport_lea.sudo().reminder_task_id, "900 jours : pas encore.")
        self.env["bf.household.document"]._cron_send_reminders()
        self.assertEqual(self.env["project.task"].sudo().search_count([("id", "=", tache.id)]), 1)

    def test_child_reminder_goes_to_both_parents(self):
        self.passeport_lea.with_user(self.camille).expiry_date = date.today() + timedelta(days=30)
        self.env["bf.household.document"]._cron_send_reminders()
        self.assertEqual(self.passeport_lea.sudo().reminder_task_id.user_ids, self.camille | self.alex)

    def test_photo_follows_the_paper(self):
        Attachment = self.env["ir.attachment"]
        photo = Attachment.with_user(self.sam).create({
            "name": "passeport.jpg", "datas": base64.b64encode(b"jpeg"),
            "res_model": "bf.household.document", "res_id": 0,
        })
        self.passeport_sam.with_user(self.sam).attachment_ids = [(4, photo.id)]
        self.assertEqual(photo.res_id, self.passeport_sam.id)
        with self.assertRaises(AccessError):
            Attachment.with_user(self.camille).browse(photo.id).read(["datas"])

    def test_cannot_attach_someone_elses_photo(self):
        photo_lea = self.env["ir.attachment"].with_user(self.camille).create({
            "name": "lea.jpg", "datas": base64.b64encode(b"jpeg"),
            "res_model": "bf.household.document", "res_id": self.passeport_lea.id,
        })
        self.assertIn(photo_lea, self.passeport_lea.sudo().attachment_ids, "Rattachée d'elle-même.")
        with self.assertRaises(ValidationError):
            self.passeport_sam.with_user(self.sam).attachment_ids = [(4, photo_lea.id)]

    def test_cannot_add_a_photo_to_someone_elses_paper(self):
        with self.assertRaises(AccessError):
            self.env["ir.attachment"].with_user(self.camille).create({
                "name": "x.jpg", "datas": base64.b64encode(b"jpeg"),
                "res_model": "bf.household.document", "res_id": self.passeport_sam.id,
            })

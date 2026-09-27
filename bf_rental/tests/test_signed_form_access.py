"""Le formulaire signé se lit par tout gestionnaire, pas seulement par qui l'a déposé.

🔴 Joué en rôle sur la démonstration : un gestionnaire ouvrait un bail portant
un formulaire signé et recevait un refus d'accès. Le fichier, déposé sur un bail
neuf, était né sans `res_id` : Odoo le réserve alors à la personne qui l'a
déposé. Les essais tournaient en administrateur, qui traverse ce contrôle.
"""
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSignedFormAccess(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = "base.group_user,bf_property_core.group_bf_property_manager"
        cls.depositor = new_test_user(cls.env, login="qa-bail-depot", groups=groups)
        cls.colleague = new_test_user(cls.env, login="qa-bail-collegue", groups=groups)
        cls.stranger = new_test_user(cls.env, login="qa-bail-tiers", groups="base.group_user")
        cls.landlord = cls.env["bf.property.organisation"].create({
            "name": "Immeubles Beauport", "kind": "landlord",
        })
        cls.building = cls.env["bf.property.building"].create({
            "name": "1200 Cartier", "organisation_id": cls.landlord.id,
        })
        cls.tenant = cls.env["res.partner"].create({"name": "Locataire Test"})

    def _upload(self, user):
        # Ce que fait le widget sur un bail neuf : un fichier sans res_id.
        return self.env["ir.attachment"].with_user(user).create({
            "name": "formulaire-signe.pdf", "raw": b"%PDF-1.4 essai",
            "res_model": "bf.rental.lease", "res_id": 0,
        })

    def _lease(self, user, attachment):
        return self.env["bf.rental.lease"].with_user(user).create({
            "organisation_id": self.landlord.id,
            "building_id": self.building.id,
            "tenant_ids": [(6, 0, [self.tenant.id])],
            "duration_kind": "fixed",
            "date_start": "2026-07-01",
            "date_end": "2027-06-30",
            "rent": 1200.0,
            "lease_attachment_ids": [(6, 0, attachment.ids)],
        })

    def test_a_colleague_reads_the_signed_form(self):
        attachment = self._upload(self.depositor)
        lease = self._lease(self.depositor, attachment)
        self.assertEqual((attachment.res_model, attachment.res_id), (lease._name, lease.id))
        seen = lease.with_user(self.colleague).lease_attachment_ids
        self.assertEqual(seen.read(["name"])[0]["name"], "formulaire-signe.pdf")

    def test_a_form_added_later_is_attached_too(self):
        lease = self._lease(self.depositor, self.env["ir.attachment"])
        attachment = self._upload(self.depositor)
        lease.with_user(self.depositor).write(
            {"lease_attachment_ids": [(4, attachment.id)]})
        self.assertEqual(attachment.res_id, lease.id)

    def test_someone_elses_file_is_not_pulled_into_the_lease(self):
        # Un identifiant glissé par RPC ne rend pas lisible le fichier d'un autre.
        foreign = self._upload(self.stranger)
        lease = self._lease(self.depositor, self.env["ir.attachment"])
        lease.with_user(self.depositor).write(
            {"lease_attachment_ids": [(4, foreign.id)]})
        self.assertFalse(foreign.res_id)
        with self.assertRaises(AccessError):
            foreign.with_user(self.colleague).read(["name"])

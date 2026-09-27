"""Le pont ne fait qu'une chose : reprendre ce que le parc sait déjà."""

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_visite")
class TestPontImmeubles(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.organisation = cls.env["bf.property.organisation"].create({
            "name": "Essai Parc locatif",
            "kind": "landlord",
        })
        cls.immeuble = cls.env["bf.property.building"].create({
            "name": "Essai Immeuble du Parc",
            "organisation_id": cls.organisation.id,
            "street": "500, rue du Parc",
            "city": "Sherbrooke",
            "zip": "J1H 1A1",
        })
        cls.occupant = cls.env["res.partner"].create({
            "name": "Essai Occupante", "email": "occupante@example.com",
        })
        cls.logement = cls.env["bf.property.unit"].create({
            "name": "302",
            "building_id": cls.immeuble.id,
            "organisation_id": cls.organisation.id,
            "is_rented": True,
            "occupant_id": cls.occupant.id,
        })

    def test_le_logement_descend_dans_linscription(self):
        inscription = self.env["bf.visit.listing"].new({
            "name": False,
            "unit_id": self.logement.id,
            "lead_time_hours": 2.0,
        })
        inscription._onchange_unit_id()
        self.assertEqual(inscription.street, "500, rue du Parc")
        self.assertEqual(inscription.city, "Sherbrooke")
        self.assertEqual(
            inscription.occupancy, "tenant",
            "Un logement loué se visite sous le régime de l'article 1931.",
        )
        self.assertEqual(inscription.tenant_id, self.occupant)
        self.assertEqual(
            inscription.lead_time_hours, 24.0,
            "Le plancher légal se pose à la reprise, pas au refus de la "
            "première plage saisie.",
        )

    def test_le_bouton_rouvre_linscription_existante(self):
        premiere = self.env["bf.visit.listing"].create({
            "name": "Essai 302",
            "unit_id": self.logement.id,
            "tz": "America/Toronto",
        })
        action = self.logement.action_create_visit_listing()
        self.assertEqual(
            action.get("res_id"), premiere.id,
            "Un deuxième clic ne doit pas créer une deuxième inscription.",
        )

    def test_le_bouton_ne_pose_pas_de_second_entete(self):
        # Le portail des occupants pose déjà un en-tête sur la fiche du
        # logement : un second, ajouté ici, s'affichait sous le premier.
        vue = self.env.ref(
            "bf_appointment_visit_property.view_bf_property_unit_form_visit")
        self.assertNotIn("<header", vue.arch)
        self.assertIn("action_create_visit_listing", vue.arch)

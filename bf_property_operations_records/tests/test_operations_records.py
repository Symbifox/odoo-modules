"""La citation entre les deux lectures d'un même bien, et la dérive.

⚠️ Ce qui est éprouvé ici n'est pas que le lien existe, mais qu'il signale.
Un pont muet entre deux registres ne coûte rien à écrire et ne rend rien : le
défaut qu'il doit attraper est le carnet qui décrit encore une chaudière que
l'exploitation a mise au rebut.
"""
from datetime import date

from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestOperationsRecords(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.syndicat = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat du pont", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble Marquette", "organisation_id": cls.syndicat.id}
        )
        cls.other_building = cls.env["bf.property.building"].create(
            {"name": "Immeuble Fabre", "organisation_id": cls.syndicat.id}
        )
        cls.machine_room = cls.env["bf.property.common.area"].create(
            {"name": "Salle mécanique", "building_id": cls.building.id}
        )
        cls.log = cls.env["bf.property.maintenance.log"].create(
            {
                "name": "Carnet 2026",
                "organisation_id": cls.syndicat.id,
            }
        )
        cls.boiler = cls.env["maintenance.equipment"].create(
            {
                "name": "Chaudière principale",
                "bf_common_area_id": cls.machine_room.id,
            }
        )
        cls.item = cls.env["bf.property.maintenance.item"].create(
            {
                "name": "Chaudière",
                "log_id": cls.log.id,
                "common_area_id": cls.machine_room.id,
                "equipment_id": cls.boiler.id,
            }
        )

    # ── La citation ──

    def test_the_log_cites_the_equipment_and_the_equipment_knows_it(self):
        self.assertEqual(self.item.equipment_id, self.boiler)
        self.assertIn(self.item, self.boiler.bf_maintenance_item_ids)

    def test_the_citation_copies_no_field(self):
        """⚠️ Citer n'est pas fusionner : 23 champs propres d'un côté, 27 de
        l'autre, et le module n'en recopie aucun."""
        self.boiler.write({"model": "Viessmann Vitocrossal"})
        self.item.write({"condition": "fair"})
        self.assertEqual(self.item.name, "Chaudière")
        self.assertEqual(self.boiler.name, "Chaudière principale")
        self.assertFalse(self.boiler.scrap_date)

    def test_an_equipment_of_another_building_cannot_be_cited(self):
        stranger = self.env["maintenance.equipment"].create(
            {
                "name": "Chaudière voisine",
                "bf_building_id": self.other_building.id,
            }
        )
        with self.assertRaises(ValidationError):
            self.item.write({"equipment_id": stranger.id})

    # ── La dérive ──

    def test_a_scrapped_equipment_raises_the_drift(self):
        """Le défaut nommé : le carnet décrit une chaudière que
        l'exploitation a déjà remplacée."""
        self.assertFalse(self.item.equipment_drift)
        self.boiler.write({"scrap_date": date(2026, 7, 1)})
        self.assertEqual(self.item.equipment_drift, "scrapped")

    def test_an_archived_equipment_raises_the_drift(self):
        self.boiler.write({"active": False})
        self.assertEqual(self.item.equipment_drift, "archived")

    def test_recorded_work_clears_the_drift(self):
        """L'écart s'éteint quand le conseil a fait sa mise à jour : l'art. 3
        al. 2 fait noter les travaux effectués, leur date et leur coût."""
        self.boiler.write({"scrap_date": date(2026, 7, 1)})
        self.assertEqual(self.item.equipment_drift, "scrapped")
        self.item.write({"done_date": date(2026, 7, 15), "done_cost": 18500.0})
        self.assertFalse(self.item.equipment_drift)

    def test_the_drift_never_blocks_the_save(self):
        """⚠️ Un signal qui refuserait l'enregistrement empêcherait le carnet
        de dire la vérité, alors que l'art. 4 lui demande justement de porter
        ce qui n'a pas été fait et pourquoi."""
        self.boiler.write({"scrap_date": date(2026, 7, 1)})
        self.item.write({"not_done_reason": "Remplacée d'urgence en juillet"})
        self.assertEqual(self.item.equipment_drift, "scrapped")
        self.assertEqual(
            self.item.not_done_reason, "Remplacée d'urgence en juillet"
        )

    def test_an_item_without_a_citation_never_drifts(self):
        orphan = self.env["bf.property.maintenance.item"].create(
            {"name": "Toiture", "log_id": self.log.id}
        )
        self.assertFalse(orphan.equipment_drift)

    # ── Ce que la citation empêche, et ce qu'elle traverse ──

    def test_a_cited_equipment_cannot_be_deleted(self):
        """⚠️ Le chemin normal du retrait est la mise au rebut, que le module
        signale. La suppression, elle, effacerait la citation d'un document
        réglementaire sans que personne l'apprenne."""
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            with self.cr.savepoint():
                self.boiler.unlink()

    def test_the_drift_is_computed_across_the_roles(self):
        """🔴 L'écart se calcule sur un modèle du carnet quand quelqu'un touche
        un modèle de l'exploitation. Le gestionnaire d'équipements qui met une
        chaudière au rebut n'a aucun droit sur le carnet : si le recalcul
        passait par ses droits à lui, la mise au rebut échouerait — et elle
        échouerait en production, jamais dans un test joué en administrateur.
        """
        technician = self.env["res.users"].create(
            {
                "name": "Gestionnaire d'équipements",
                "login": "equipements.p9@example.org",
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref(
                                "maintenance.group_equipment_manager"
                            ).id,
                        ],
                    )
                ],
            }
        )
        self.boiler.with_user(technician).write({"scrap_date": date(2026, 7, 1)})
        self.assertEqual(self.item.equipment_drift, "scrapped")

    # ── Ce que le carnet montre ──

    def test_the_log_counts_what_drifted(self):
        """⚠️ Une colonne facultative dans deux cents lignes ne se voit pas :
        le carnet doit dire combien de biens sont en écart, à son entrée."""
        self.assertEqual(self.log.equipment_drift_count, 0)
        self.boiler.write({"scrap_date": date(2026, 7, 1)})
        self.log.invalidate_recordset(["equipment_drift_count"])
        self.assertEqual(self.log.equipment_drift_count, 1)

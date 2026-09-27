"""La nature d'une réparation : ce qu'elle dit, et surtout quand elle se tait.

`maintenance_type` d'Odoo n'a que deux
valeurs, et « correctif » en recouvre deux du règlement : la réparation
*courante* (r. 8.01, art. 2 al. 2, par. 3°) et la réparation *majeure* ou le
remplacement (art. 3 al. 2, qui veut aussi le coût). Le carnet a deux jeux de
champs pour ça. Le module ne devine pas lequel.

Ce qui s'éprouve ici est donc l'accroche, pas son effet : c'est
`bf_property_operations_records` qui écrit au carnet, et l'exploitation vaut
sans lui.

⚠️ **Le silence est la valeur par défaut, et c'est le point.** Un champ vide est
une question sans réponse ; une valeur devinée serait une réponse fausse dans un
document qu'un professionnel signe.
"""
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRepairScope(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["bf.property.building"])
        cls.organisation = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat de la nature", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble Fullum", "organisation_id": cls.organisation.id}
        )
        cls.boiler = cls.env["maintenance.equipment"].create(
            {"name": "Chaudière Fullum", "bf_building_id": cls.building.id}
        )
        cls.done_stage = cls.env["maintenance.stage"].search(
            [("done", "=", True)], limit=1
        )

    def _work(self, **kw):
        vals = {
            "name": "Fuite au raccord",
            "equipment_id": self.boiler.id,
            "maintenance_type": "corrective",
        }
        vals.update(kw)
        return self.env["maintenance.request"].create(vals)

    # ── Le champ ──

    def test_a_corrective_starts_unqualified(self):
        """Rien n'est porté tant que rien n'est retenu."""
        self.assertFalse(self._work().bf_repair_scope)

    def test_the_two_values_are_those_of_the_regulation(self):
        """Deux paragraphes, deux valeurs. Le vide n'en est pas une troisième :
        c'est l'absence de réponse."""
        scopes = dict(
            self.env["maintenance.request"]._fields["bf_repair_scope"].selection
        )
        self.assertEqual(set(scopes), {"routine", "major"})

    def test_a_preventive_cannot_carry_a_qualification(self):
        """Le par. 2° écarte lui-même les travaux visés à l'article 3 : un
        travail d'entretien requis n'a pas de nature de réparation à porter.

        🔴 Le calcul ne tient pas cette règle tout seul : un calculé stocké
        `readonly=False` range une valeur explicite sans se rejouer. L'écriture
        par RPC est donc la porte qu'il faut fermer, la vue cachant déjà le
        champ."""
        work = self._work(maintenance_type="preventive")
        work.write({"bf_repair_scope": "routine"})
        self.assertFalse(work.bf_repair_scope)

    def test_a_preventive_born_with_a_qualification_drops_it(self):
        """La même porte, à la création. Posée dans le même `create` que le
        type, la valeur explicite bat le calcul de la même façon."""
        work = self._work(maintenance_type="preventive", bf_repair_scope="routine")
        self.assertFalse(work.bf_repair_scope)

    def test_switching_to_preventive_clears_the_qualification(self):
        """⚠️ Sinon un cédule ferait remonter une réparation courante."""
        work = self._work(bf_repair_scope="routine")
        work.maintenance_type = "preventive"
        self.assertFalse(work.bf_repair_scope)

    def test_the_qualification_survives_the_closing(self):
        work = self._work(bf_repair_scope="routine")
        work.stage_id = self.done_stage
        self.assertEqual(work.bf_repair_scope, "routine")

    # ── L'accroche ──

    def _closes(self, **kw):
        """Ferme un travail et dit si l'accroche a joué."""
        work = self._work(**kw)
        target = type(self.env["maintenance.request"])
        with patch.object(target, "_bf_corrective_done", autospec=True) as hook:
            work.stage_id = self.done_stage
            return hook.called

    def test_closing_a_routine_repair_reaches_the_hook(self):
        self.assertTrue(self._closes(bf_repair_scope="routine"))

    def test_closing_an_unqualified_repair_reaches_nothing(self):
        """🔴 Le cas par défaut, et le plus fréquent."""
        self.assertFalse(self._closes())

    def test_closing_a_major_repair_reaches_nothing(self):
        """L'art. 3 al. 2 veut aussi le coût : ce n'est pas la même écriture,
        et elle n'existe pas encore."""
        self.assertFalse(self._closes(bf_repair_scope="major"))

    def test_a_repair_that_is_not_closed_reaches_nothing(self):
        work = self._work(bf_repair_scope="routine")
        target = type(self.env["maintenance.request"])
        with patch.object(target, "_bf_corrective_done", autospec=True) as hook:
            work.write({"name": "Fuite au raccord, reprise"})
            self.assertFalse(hook.called)

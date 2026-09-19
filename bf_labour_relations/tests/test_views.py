from odoo.tests.common import tagged

from .common import LabourCase

MODELS = [
    "bf.labour.union",
    "bf.labour.unit",
    "bf.labour.agreement",
    "bf.labour.agreement.article",
    "bf.labour.membership",
    "bf.labour.grievance",
    "bf.labour.grievance.step",
    "bf.labour.dues.rule",
    "bf.labour.dues.remittance",
    "bf.labour.dues.remittance.line",
]


@tagged("post_install", "-at_install")
class TestViews(LabourCase):
    """Les vues se montent vraiment.

    ⚠️ Les essais Python passent très bien sur un module dont les vues sont
    cassées : une erreur d'architecture ne se voit qu'au moment où le client
    web la demande. `get_views` est ce qui la fait sortir sans navigateur.
    """

    def test_every_model_renders_its_views(self):
        for model in MODELS:
            with self.subTest(model=model):
                self.env[model].get_views([
                    (False, "list"), (False, "form"),
                ])

    def test_the_employee_form_still_builds(self):
        """Le greffon sur hr.employee est une vue héritée : elle peut casser
        la fiche employé d'Odoo sans que rien d'autre bronche."""
        self.env["hr.employee"].get_views([(False, "form")])

    def test_search_views_build(self):
        for model in ["bf.labour.unit", "bf.labour.agreement",
                      "bf.labour.agreement.article", "bf.labour.membership",
                      "bf.labour.grievance"]:
            with self.subTest(model=model):
                self.env[model].get_views([(False, "search")])

    def test_actions_point_at_existing_models(self):
        """Les actions DE CE MODULE, pas toutes celles qui commencent par bf.labour.

        ⚠️ Un filtre sur `res_model like 'bf.labour.%'` ramasse aussi les
        actions des greffons dès qu'ils sont installés, et l'essai du socle se
        met à échouer sur du code parfaitement sain. La portée d'un essai se
        prend par le module qui a posé l'enregistrement.
        """
        data = self.env["ir.model.data"].search([
            ("module", "=", "bf_labour_relations"),
            ("model", "=", "ir.actions.act_window"),
        ])
        actions = self.env["ir.actions.act_window"].browse(data.mapped("res_id"))
        self.assertTrue(actions)
        for action in actions:
            with self.subTest(action=action.name):
                self.assertIn(action.res_model, MODELS)

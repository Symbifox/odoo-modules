from odoo.tests.common import tagged

from .common import UnionCase

MODELS = [
    "bf.labour.card",
    "bf.labour.assembly",
    "bf.labour.assembly.vote",
    "bf.labour.delegate",
    "bf.labour.delegate.release",
    "bf.labour.dues.receipt",
]


@tagged("post_install", "-at_install")
class TestViews(UnionCase):

    def test_every_model_renders_its_views(self):
        for model in MODELS:
            with self.subTest(model=model):
                self.env[model].get_views([(False, "list"), (False, "form")])

    def test_the_inherited_grievance_views_still_build(self):
        """Le greffon ajoute une page et une colonne aux vues du socle par xpath.

        Un xpath qui ne résout plus casse le grief du socle sans qu'aucun essai
        Python ne bronche, et le grief est l'écran que les deux côtés partagent.
        """
        self.env["bf.labour.grievance"].get_views([(False, "form"), (False, "list")])

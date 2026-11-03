from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestActionRunGuard(TransactionCase):
    """`action_run` est publique, donc atteignable par /web/dataset/call_kw.

    L'ACL du détecteur ne donne l'écriture qu'à `base.group_system`, et les
    détecteurs écrivent en `sudo()` : la garde doit donc être dans la méthode,
    pas seulement sur le menu.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.detector = cls.env.ref("bf_irregularity.detector_activity_overdue")
        cls.simple = cls.env["res.users"].create({
            "name": "Usager simple", "login": "irr_simple",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.admin = cls.env["res.users"].create({
            "name": "Usager système", "login": "irr_systeme",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id,
                                  cls.env.ref("base.group_system").id])],
        })

    def test_usager_simple_refuse(self):
        # Le détecteur est un enregistrement RÉEL et l'ensemble n'est pas vide :
        # un essai sur un ensemble vide ne prouverait aucun refus.
        detector = self.detector.with_user(self.simple)
        self.assertTrue(detector.id, "l'essai doit porter sur un détecteur existant")
        with self.assertRaises(AccessError):
            detector.action_run()

    def test_usager_systeme_passe(self):
        self.detector.with_user(self.admin).action_run()
        self.assertFalse(self.detector.last_error)

    def test_la_lecture_reste_permise(self):
        """La garde porte sur l'exécution, pas sur la lecture du détecteur."""
        self.assertTrue(self.detector.with_user(self.simple).read(["name"]))

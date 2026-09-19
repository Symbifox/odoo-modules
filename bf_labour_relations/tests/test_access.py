from odoo.exceptions import AccessError
from odoo.tests.common import tagged

from .common import LabourCase


@tagged("post_install", "-at_install")
class TestAccess(LabourCase):
    """La portée se joue DANS le rôle visé, jamais en administrateur.

    ⚠️ Un droit de lecture ouvre le modèle entier : c'est la règle
    d'enregistrement qui borne, et elle ne se vérifie qu'en lisant avec les
    yeux de la personne. Le cache de transaction masque un refus si le même
    enregistrement a déjà été lu en administrateur, d'où les
    `invalidate_all()` avant chaque lecture.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plain_user = cls.env["res.users"].create({
            "name": "Salariée ordinaire",
            "login": "labour_plain_user",
            "company_id": cls.company_union.id,
            "company_ids": [(6, 0, [cls.company_union.id])],
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.other_user = cls.env["res.users"].create({
            "name": "Collègue",
            "login": "labour_other_user",
            "company_id": cls.company_union.id,
            "company_ids": [(6, 0, [cls.company_union.id])],
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.hr_user = cls.env["res.users"].create({
            "name": "Relations de travail",
            "login": "labour_hr_user",
            "company_id": cls.company_union.id,
            "company_ids": [(6, 0, [cls.company_union.id])],
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("hr.group_hr_user").id,
            ])],
        })
        cls.mine = cls._employee("Salariée ordinaire")
        cls.mine.user_id = cls.plain_user
        cls.theirs = cls._employee("Collègue")
        cls.theirs.user_id = cls.other_user

        cls.my_membership = cls._membership(cls.mine)
        cls.their_membership = cls._membership(cls.theirs)

    def test_i_read_my_own_membership(self):
        self.env.invalidate_all()
        line = self.my_membership.with_user(self.plain_user)
        self.assertEqual(line.employee_id, self.mine)

    def test_i_do_not_read_a_colleague_membership(self):
        """Le rang d'ancienneté de quelqu'un d'autre n'est pas à moi."""
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            self.their_membership.with_user(self.plain_user).read(["seniority_date"])

    def test_a_colleague_membership_is_absent_from_my_search(self):
        """Une règle qui ne bloque qu'à la lecture directe ne borne rien.

        Ce qui compte est que l'enregistrement ne sorte pas de la recherche.
        """
        self.env.invalidate_all()
        found = self.env["bf.labour.membership"].with_user(self.plain_user).search([])
        self.assertIn(self.my_membership, found)
        self.assertNotIn(self.their_membership, found)

    def test_relations_de_travail_reads_everyone(self):
        self.env.invalidate_all()
        found = self.env["bf.labour.membership"].with_user(self.hr_user).search([])
        self.assertIn(self.my_membership, found)
        self.assertIn(self.their_membership, found)

    def test_i_do_not_read_a_grievance_that_does_not_target_me(self):
        """⚠️ La moitié sensible du module.

        Un grief porte de la discipline, parfois de la santé. Il se lit par les
        personnes visées et par l'administration, personne d'autre.
        """
        grievance = self._grievance(subject="Suspension de trois jours")
        grievance.employee_ids = self.theirs
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            grievance.with_user(self.plain_user).read(["subject"])

    def test_i_read_the_grievance_that_targets_me(self):
        grievance = self._grievance(subject="Mon grief")
        grievance.employee_ids = self.mine
        self.env.invalidate_all()
        self.assertEqual(
            grievance.with_user(self.plain_user).subject, "Mon grief",
        )

    def test_a_grievance_that_targets_a_colleague_is_absent_from_my_search(self):
        grievance = self._grievance(subject="Pas le mien")
        grievance.employee_ids = self.theirs
        self.env.invalidate_all()
        found = self.env["bf.labour.grievance"].with_user(self.plain_user).search([])
        self.assertNotIn(grievance, found)

    def test_grievance_steps_follow_the_grievance(self):
        """Border le grief sans border ses étapes ne borne rien.

        Le calendrier d'un dossier disciplinaire en dit déjà long.
        """
        grievance = self._grievance(subject="Pas le mien non plus")
        grievance.employee_ids = self.theirs
        step = self.env["bf.labour.grievance.step"].create({
            "grievance_id": grievance.id,
            "name": "Première étape",
            "date_start": self.today,
            "delay_days": 10,
        })
        self.env.invalidate_all()
        found = self.env["bf.labour.grievance.step"].with_user(self.plain_user).search([])
        self.assertNotIn(step, found)

    def test_a_plain_user_cannot_write_a_grievance(self):
        grievance = self._grievance(subject="Le mien, en lecture seule")
        grievance.employee_ids = self.mine
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            grievance.with_user(self.plain_user).write({"subject": "Récrit"})

    def test_another_company_is_invisible(self):
        """La règle de société est globale : elle s'applique à tout le monde."""
        outsider_unit = self.env["bf.labour.unit"].create({
            "name": "Unité d'une autre société",
            "company_id": self.company_free.id,
            "union_id": self.union.id,
        })
        self.env.invalidate_all()
        scoped = self.env["bf.labour.unit"].with_user(self.hr_user).search([])
        self.assertIn(self.unit, scoped)
        self.assertNotIn(outsider_unit, scoped)

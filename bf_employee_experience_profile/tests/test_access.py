from odoo.exceptions import AccessError
from odoo.tests.common import tagged

from .common import ProfileCase


@tagged("post_install", "-at_install")
class TestAccess(ProfileCase):
    """⚠️ LA MOITIE SENSIBLE DU MODULE.

    La conception : la personne, son gestionnaire
    direct et l'administration LISENT ; la personne et l'administration
    ECRIVENT ; personne d'autre n'entre.

    Chaque règle qui masque porte ici une MUTATION : on remplace son domaine
    par `[(1, '=', 1)]` et on vérifie que la fuite apparaît. Sans cette
    contre-épreuve, un test vert ne dit pas si c'est bien la règle qui masque,
    ou si la donnée était simplement absente.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_alice = cls._user("Alice", "alice_profile_acc")
        cls.user_bob = cls._user("Bob", "bob_profile_acc")
        cls.user_boss = cls._user("Gestionnaire", "boss_profile_acc")
        cls.user_hr = cls._user("Administration", "hr_profile_acc",
                                ("base.group_user", "hr.group_hr_user"))

        cls.boss = cls._employee("Gestionnaire", user=cls.user_boss)
        cls.alice = cls._employee("Alice Accès", user=cls.user_alice, manager=cls.boss)
        cls.bob = cls._employee("Bob Accès", user=cls.user_bob, manager=cls.boss)

    def setUp(self):
        super().setUp()
        self.profile = self._profile(self.alice)
        items = {item.code: item for item in self.behaviour4.item_ids}
        self.scored = self._scored(self.bob, self.behaviour4, {
            items["d"]: 10.0, items["s"]: 80.0,
        })

    def _mutate(self, xmlid, domain="[(1, '=', 1)]"):
        """Ouvrir une règle en grand, pour voir si c'est bien elle qui masque."""
        rule = self.env.ref("bf_employee_experience_profile.%s" % xmlid)
        rule.sudo().write({"domain_force": domain})
        self.env.invalidate_all()

    # ------------------------------------------------------------- lecture

    def test_the_person_reads_their_own(self):
        found = self.env["bf.ex.profile"].with_user(self.user_alice).search([])
        self.assertEqual(found, self.profile)

    def test_the_direct_manager_reads_their_team(self):
        """L'écart assumé avec les allergies : un style de communication ne
        sert à rien si la personne qui doit communiquer ne le voit pas."""
        found = self.env["bf.ex.profile"].with_user(self.user_boss).search([])
        self.assertIn(self.profile, found)
        self.assertIn(self.scored, found)

    def test_a_colleague_reads_nothing(self):
        found = self.env["bf.ex.profile"].with_user(self.user_bob).search([])
        self.assertNotIn(self.profile, found, "Bob n'est pas le gestionnaire d'Alice")

    def test_the_read_rule_is_what_masks_a_colleague(self):
        self._mutate("rule_profile_read")
        found = self.env["bf.ex.profile"].with_user(self.user_bob).search([])
        self.assertIn(self.profile, found,
                      "règle ouverte : la fuite doit apparaître, sinon le test "
                      "précédent ne prouvait rien")

    def test_administration_reads_everything(self):
        found = self.env["bf.ex.profile"].with_user(self.user_hr).search([])
        self.assertIn(self.profile, found)
        self.assertIn(self.scored, found)

    # ------------------------------------------------------------- écriture

    def test_the_person_writes_their_own(self):
        self.profile.with_user(self.user_alice).write({"note": "Ajout de ma main."})
        self.assertEqual(self.profile.note, "Ajout de ma main.")

    def test_the_manager_reads_but_does_not_write(self):
        """🔴 Le point précis où deux règles pour le même groupe se combinent
        par OU : si la règle d'écriture portait aussi la lecture, ou si la
        règle de lecture portait l'écriture, le gestionnaire récrirait le
        profil de son équipe."""
        with self.assertRaises(AccessError):
            self.profile.with_user(self.user_boss).write({"note": "Corrigé par le chef."})

    def test_the_manager_does_not_create_for_their_team(self):
        with self.assertRaises(AccessError):
            self.env["bf.ex.profile"].with_user(self.user_boss).create({
                "employee_id": self.alice.id,
                "framework_id": self.behaviour4.id,
            })

    def test_the_manager_does_not_delete_their_team_s_profile(self):
        with self.assertRaises(AccessError):
            self.profile.with_user(self.user_boss).unlink()

    def test_administration_writes(self):
        self.profile.with_user(self.user_hr).write({"note": "Saisi par les RH."})
        self.assertEqual(self.profile.note, "Saisi par les RH.")

    # -------------------------------------------------------------- scores

    def test_a_colleague_reads_no_score(self):
        """Les scores portent le même renseignement que la déclaration. Une
        règle plus généreuse ici rendrait l'autre décorative."""
        found = self.env["bf.ex.profile.score"].with_user(self.user_alice).search([])
        self.assertFalse(found & self.scored.score_ids)

    def test_the_score_rule_is_what_masks(self):
        self._mutate("rule_score_read")
        found = self.env["bf.ex.profile.score"].with_user(self.user_alice).search([])
        self.assertTrue(found & self.scored.score_ids,
                        "règle ouverte : la fuite doit apparaître")

    def test_the_manager_reads_the_scores_of_their_team(self):
        found = self.env["bf.ex.profile.score"].with_user(self.user_boss).search([])
        self.assertTrue(found & self.scored.score_ids)

    def test_the_manager_does_not_write_a_score(self):
        with self.assertRaises(AccessError):
            self.scored.score_ids[0].with_user(self.user_boss).write({"value": 99.0})

    # ------------------------------------------------------------ catalogue

    def test_everybody_reads_the_catalogue(self):
        """Sans ça, l'écran de déclaration ne s'ouvre pas : il lit le
        référentiel et ses types."""
        self.assertTrue(
            self.env["bf.ex.profile.framework"].with_user(self.user_bob).search([]))
        self.assertTrue(
            self.env["bf.ex.profile.type"].with_user(self.user_bob).search([]))

    def test_nobody_but_administration_touches_the_catalogue(self):
        with self.assertRaises(AccessError):
            self.behaviour4.with_user(self.user_bob).write({"name": "Renommé"})

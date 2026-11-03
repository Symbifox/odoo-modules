from dateutil.relativedelta import relativedelta

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from .common import ProfileCase


@tagged("post_install", "-at_install")
class TestCatalogue(ProfileCase):
    """Les trois référentiels livrés, et ce qu'ils promettent."""

    def test_three_frameworks_are_shipped(self):
        self.assertEqual(len(self.behaviour4.item_ids), 4)
        self.assertEqual(len(self.types16.item_ids), 16)
        self.assertEqual(len(self.bigfive.item_ids), 5)
        self.assertEqual(self.behaviour4.result_mode, "scored")
        self.assertEqual(self.types16.result_mode, "single")
        self.assertEqual(self.bigfive.result_mode, "scored")

    def test_no_vendor_trademark_in_the_catalogue(self):
        """⚠️ La règle exacte, parce que la précédente était taillée autour de
        ce que le module livre.

        Ce qui est interdit : les noms de MARQUE des éditeurs. Ce qui est
        permis : « DISC » en majuscules, acronyme générique du modèle de
        Marston, et seulement entre parenthèses derrière un nom qui dit ce que
        le référentiel mesure. L'essai vérifie les deux, dans les deux langues,
        en lisant ce que l'ORM rend.
        """
        data = self.env["ir.model.data"].search([
            ("module", "=", "bf_employee_experience_profile"),
            ("model", "in", ("bf.ex.profile.framework", "bf.ex.profile.type")),
        ])
        for lang, generique in (("en_US", "(disc family)"), ("fr_CA", "(famille disc)")):
            textes = []
            for ligne in data:
                record = self.env[ligne.model].with_context(lang=lang).browse(ligne.res_id)
                if not record.exists():
                    continue
                textes += [record.name or "", record.description or ""]
                if ligne.model == "bf.ex.profile.type":
                    textes.append(record.working_with or "")
            blob = " ".join(textes).lower()
            for marque in ("myers", "briggs", "mbti", "disc\u00ae", "everything disc",
                           "16personalities", "insights discovery", "wiley", "neris"):
                with self.subTest(lang=lang, marque=marque):
                    self.assertNotIn(marque, blob,
                                     "nom de marque d'éditeur dans les données livrées")
            with self.subTest(lang=lang, regle="disc generique"):
                self.assertEqual(
                    blob.count("disc"), blob.count(generique),
                    "« DISC » ne doit apparaître que comme acronyme générique, "
                    "entre parenthèses",
                )

    def test_the_two_c_factors_are_not_the_same_label(self):
        """🔴 Le C du DISC et le C du Big Five portaient le MÊME libellé
        anglais, donc une seule entrée de catalogue couvrait les deux : en
        français, le facteur Big Five s'affichait « Conformité » au-dessus de
        sa propre description. Aucune traduction ne peut séparer deux libellés
        identiques ; c'est la source qui devait changer.
        """
        disc_c = self.env.ref("bf_employee_experience_profile.type_behaviour4_c")
        big_c = self.env.ref("bf_employee_experience_profile.type_bigfive_conscientiousness")
        for lang in ("en_US", "fr_CA"):
            with self.subTest(lang=lang):
                self.assertNotEqual(
                    disc_c.with_context(lang=lang).name,
                    big_c.with_context(lang=lang).name,
                )

    def test_a_scored_framework_needs_a_real_scale(self):
        with self.assertRaises(ValidationError):
            self.env["bf.ex.profile.framework"].create({
                "name": "Échelle vide", "code": "vide",
                "result_mode": "scored", "scale_min": 10.0, "scale_max": 10.0,
            })

    def test_display_name_of_an_item(self):
        self.assertEqual(self.style_d.display_name, "Dominance (D)")
        # Pour les seize types, le code EST le nom : pas de « ISTJ (ISTJ) ».
        self.assertEqual(self.type_istj.display_name, "ISTJ")


@tagged("post_install", "-at_install")
class TestDeclaration(ProfileCase):
    """La déclaration : ce qu'elle accepte, ce qu'elle refuse, ce qu'elle calcule."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = cls._employee("Alice Profil")

    def test_single_framework_requires_a_type(self):
        with self.assertRaises(ValidationError):
            self.env["bf.ex.profile"].create({
                "employee_id": self.alice.id, "framework_id": self.types16.id,
            })

    def test_single_framework_refuses_scores(self):
        with self.assertRaises(ValidationError):
            self.env["bf.ex.profile"].create({
                "employee_id": self.alice.id,
                "framework_id": self.types16.id,
                "type_id": self.type_istj.id,
                "score_ids": [(0, 0, {"type_id": self.type_istj.id, "value": 1.0})],
            })

    def test_scored_framework_refuses_a_single_type(self):
        """Le dominant se calcule. Saisi ET calculé, personne ne sait lequel compte."""
        with self.assertRaises(ValidationError):
            self.env["bf.ex.profile"].create({
                "employee_id": self.alice.id,
                "framework_id": self.behaviour4.id,
                "type_id": self.style_d.id,
            })

    def test_type_must_belong_to_the_framework(self):
        with self.assertRaises(ValidationError):
            self.env["bf.ex.profile"].create({
                "employee_id": self.alice.id,
                "framework_id": self.types16.id,
                "type_id": self.style_d.id,
            })

    def test_one_declaration_per_person_and_framework(self):
        self._profile(self.alice)
        with self.assertRaises(Exception):
            with mute_logger("odoo.sql_db"):
                self._profile(self.alice)

    def test_a_result_cannot_be_dated_in_the_future(self):
        with self.assertRaises(ValidationError):
            self._profile(self.alice, assessed_on=self.today + relativedelta(days=1))

    def test_dominant_dimension_is_computed_from_the_scores(self):
        items = {item.code: item for item in self.behaviour4.item_ids}
        profile = self._scored(self.alice, self.behaviour4, {
            items["d"]: 40.0, items["i"]: 75.0, items["s"]: 20.0, items["c"]: 10.0,
        })
        self.assertEqual(profile.dominant_type_id, items["i"])
        self.assertEqual(profile.headline, items["i"].display_name)
        self.assertEqual(profile.working_with, items["i"].working_with)

    def test_a_tie_names_no_dominant_dimension(self):
        """🔴 À égalité, la première
        dimension sortait « la plus élevée ». Un gestionnaire lit ça comme un
        fait."""
        items = {item.code: item for item in self.behaviour4.item_ids}
        profile = self._scored(self.alice, self.behaviour4, {
            items["d"]: 60.0, items["i"]: 60.0, items["s"]: 10.0,
        })
        self.assertFalse(profile.dominant_type_id)
        self.assertFalse(profile.headline)

    def test_headline_reads_the_named_type_on_a_single_framework(self):
        profile = self._profile(self.alice)
        self.assertFalse(profile.dominant_type_id)
        self.assertEqual(profile.headline, "ISTJ")

    def test_headline_is_not_stored_so_nobody_can_sort_staff_by_type(self):
        """🔴 La décision de conception, posée en test : un champ stocké serait
        filtrable et groupable, et trier le personnel par type de personnalité
        est l'usage que ce module refuse de servir.

        ⚠️ Et la mesure porte sur les COLONNES, pas sur `field.store` : un
        One2many rend `store=True` tout en n'ayant aucune colonne. Lire le
        drapeau aurait fait échouer le test sur un champ parfaitement sain,
        ou l'aurait fait passer sur un champ qui ne l'est pas.
        """
        for name in ("headline", "working_with", "dominant_type_id"):
            with self.subTest(field=name):
                self.assertFalse(self.env["bf.ex.profile"]._fields[name].store)
        self.env.cr.execute("""
            SELECT column_name FROM information_schema.columns
             WHERE table_name = 'hr_employee' AND column_name LIKE 'ex_profile%'
        """)
        self.assertFalse(self.env.cr.fetchall(),
                         "aucune colonne de profil sur la fiche employé")

    def test_a_score_outside_the_scale_is_refused(self):
        items = {item.code: item for item in self.behaviour4.item_ids}
        with self.assertRaises(ValidationError):
            self._scored(self.alice, self.behaviour4, {items["d"]: 140.0})

    def test_a_dimension_of_another_framework_is_refused(self):
        with self.assertRaises(ValidationError):
            self._scored(self.alice, self.behaviour4, {self.type_istj: 10.0})

    def test_the_same_dimension_cannot_be_scored_twice(self):
        profile = self._scored(self.alice, self.behaviour4, {self.style_d: 10.0})
        with self.assertRaises(Exception):
            with mute_logger("odoo.sql_db"):
                self.env["bf.ex.profile.score"].create({
                    "profile_id": profile.id, "type_id": self.style_d.id, "value": 20.0,
                })

    def test_onchange_prefills_the_dimensions_and_clears_the_type(self):
        form = self.env["bf.ex.profile"].new({
            "employee_id": self.alice.id,
            "framework_id": self.types16.id,
            "type_id": self.type_istj.id,
        })
        form.framework_id = self.behaviour4
        form._onchange_framework_id()
        self.assertFalse(form.type_id)
        self.assertEqual(len(form.score_ids), 4)
        # Le calcul du dominant doit tenir sur des lignes NewId : `-score.id`
        # y lèverait TypeError avant tout essai. Et sur un formulaire à zéros,
        # il ne doit nommer PERSONNE : quatre dimensions à égalité.
        self.assertFalse(form.dominant_type_id)
        self.assertFalse(form.headline)

    def test_declared_by_is_recorded_without_being_asked(self):
        profile = self._profile(self.alice)
        self.assertEqual(profile.declared_by_id, self.env.user)


@tagged("post_install", "-at_install")
class TestAcknowledgement(ProfileCase):
    """L'accord de la personne, que personne d'autre ne peut donner."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_alice = cls._user("Alice", "alice_profile_ack")
        cls.user_hr = cls._user("Administration", "hr_profile_ack",
                                ("base.group_user", "hr.group_hr_user"))
        cls.alice = cls._employee("Alice Accord", user=cls.user_alice)

    def test_the_person_accepts_their_own_profile(self):
        profile = self._profile(self.alice)
        profile.with_user(self.user_alice).action_acknowledge()
        self.assertTrue(profile.acknowledged)
        self.assertEqual(profile.acknowledged_on, self.today)

    def test_administration_cannot_accept_for_somebody_else(self):
        """⚠️ Méthode publique, donc appelable par RPC. L'administration a tous
        les droits d'écriture sur l'enregistrement : si le contrôle portait sur
        un droit, elle passerait. Un accord qu'un tiers peut cocher ne vaut
        rien."""
        profile = self._profile(self.alice)
        with self.assertRaises(AccessError):
            profile.with_user(self.user_hr).action_acknowledge()
        self.assertFalse(profile.acknowledged)

    def test_sudo_does_not_lift_the_check(self):
        profile = self._profile(self.alice)
        with self.assertRaises(AccessError):
            profile.sudo().action_acknowledge()

    def test_the_person_can_withdraw_their_acceptance(self):
        profile = self._profile(self.alice)
        profile.with_user(self.user_alice).action_acknowledge()
        profile.with_user(self.user_alice).action_withdraw_consent()
        self.assertFalse(profile.acknowledged)
        self.assertFalse(profile.acknowledged_on)

    def test_withdrawal_is_also_the_person_s_alone(self):
        profile = self._profile(self.alice)
        profile.with_user(self.user_alice).action_acknowledge()
        with self.assertRaises(AccessError):
            profile.with_user(self.user_hr).action_withdraw_consent()

    def test_the_person_can_delete_a_profile_entered_by_administration(self):
        """Le vrai recours : déclarer est volontaire, donc se retirer doit
        l'être. Une ligne saisie par les RH reste effaçable par la personne."""
        profile = self._profile(self.alice, source="hr")
        profile.with_user(self.user_alice).unlink()
        self.assertFalse(profile.exists())


@tagged("post_install", "-at_install")
class TestSelfServiceActions(ProfileCase):
    """Les deux portes : la mienne, et celle de mon équipe."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_alice = cls._user("Alice", "alice_profile_act")
        cls.user_orphan = cls._user("Sans fiche", "orphan_profile_act")
        cls.user_boss = cls._user("Gestionnaire", "boss_profile_act")
        cls.boss = cls._employee("Gestionnaire", user=cls.user_boss)
        cls.alice = cls._employee("Alice Action", user=cls.user_alice, manager=cls.boss)

    def test_my_profile_opens_on_my_own_declarations(self):
        action = self.env["bf.ex.profile"].with_user(self.user_alice) \
            .action_open_my_profile()
        self.assertIn(("employee_id", "=", self.alice.id), action["domain"])
        self.assertEqual(action["context"]["default_employee_id"], self.alice.id)

    def test_my_profile_says_why_it_cannot_open(self):
        with self.assertRaises(UserError):
            self.env["bf.ex.profile"].with_user(self.user_orphan) \
                .action_open_my_profile()

    def test_team_action_is_scoped_to_the_direct_team(self):
        action = self.env["bf.ex.profile"].with_user(self.user_boss) \
            .action_open_team_profiles()
        self.assertIn(("employee_id.parent_id", "=", self.boss.id), action["domain"])

    def test_team_action_says_why_it_cannot_open(self):
        with self.assertRaises(UserError):
            self.env["bf.ex.profile"].with_user(self.user_orphan) \
                .action_open_team_profiles()

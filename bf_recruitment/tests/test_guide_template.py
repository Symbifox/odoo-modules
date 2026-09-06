# Part of bf_recruitment. Voir LICENSE.
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestGuideTemplate(TransactionCase):
    """Le catalogue de modèles et la grille qu'on en tire."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env["bf.interview.guide.template"]
        cls.templates = cls.Template.with_context(active_test=False).search([])
        cls.presel = cls.env.ref("bf_recruitment.tmpl_presel_telephonique")

        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.officer = Users.create({
            "name": "Officier catalogue", "login": "officer_tmpl",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("hr_recruitment.group_hr_recruitment_user").id,
            ])],
        })

    # ------------------------------------------------------------------
    # Le catalogue lui-même
    # ------------------------------------------------------------------

    def test_catalogue_is_delivered(self):
        """Le module livre le catalogue annoncé, dans ses trois catégories."""
        self.assertGreaterEqual(len(self.templates), 32)
        categories = set(self.templates.mapped("category"))
        self.assertEqual(categories, {"transversale", "metier", "secteur"})

    def test_every_template_would_publish(self):
        """Chaque modèle passe les conditions de publication d'une grille.

        Un modèle sans critère, ou avec une pondération nulle, produirait une
        grille que `action_publish` refuse : le défaut se découvrirait chez le
        client, pas ici.
        """
        for template in self.templates:
            with self.subTest(template=template.name):
                self.assertTrue(template.criterion_ids, "aucun critère")
                self.assertTrue(all(c.weight > 0 for c in template.criterion_ids))
                self.assertTrue(template.instructions_html, "aucune consigne")
                self.assertTrue(template.code, "aucun code")

    def test_every_criterion_carries_a_question_and_its_anchors(self):
        """Un critère sans ancrage laisse deux évaluateurs noter deux choses.

        C'est la promesse du catalogue : la note 1 et la note maximale sont
        décrites en comportements observables, pas laissées au jugement.
        """
        for template in self.templates:
            for criterion in template.criterion_ids:
                with self.subTest(template=template.name, criterion=criterion.name):
                    self.assertTrue(criterion.question_html, "aucune question")
                    self.assertTrue(criterion.description, "rien sur ce qu'on cherche")
                    scores = criterion.anchor_ids.mapped("score")
                    self.assertIn(1, scores)
                    self.assertIn(template.scale_max, scores)
                    self.assertTrue(all(1 <= s <= template.scale_max for s in scores))

    def test_codes_are_unique(self):
        codes = self.templates.mapped("code")
        self.assertEqual(len(codes), len(set(codes)))

    # ------------------------------------------------------------------
    # La grille qu'on en tire
    # ------------------------------------------------------------------

    def test_created_guide_is_a_faithful_draft(self):
        action = self.presel.action_create_guide()
        guide = self.env["bf.interview.guide"].browse(action["res_id"])

        self.assertEqual(guide.state, "brouillon")
        self.assertEqual(guide.version, 1)
        self.assertEqual(guide.source_template_id, self.presel)
        self.assertEqual(guide.company_id, self.env.company)
        self.assertEqual(guide.name, self.presel.name)
        self.assertEqual(guide.scale_max, self.presel.scale_max)
        self.assertEqual(len(guide.criterion_ids), len(self.presel.criterion_ids))

        for source, copied in zip(self.presel.criterion_ids, guide.criterion_ids):
            self.assertEqual(copied.name, source.name)
            self.assertEqual(copied.weight, source.weight)
            self.assertEqual(copied.is_knockout, source.is_knockout)
            self.assertEqual(copied.knockout_min, source.knockout_min)
            self.assertEqual(
                copied.anchor_ids.mapped("score"), source.anchor_ids.mapped("score"),
            )
            self.assertEqual(
                copied.anchor_ids.mapped("label"), source.anchor_ids.mapped("label"),
            )

    def test_created_guide_is_publishable_and_then_frozen(self):
        """La paire : la grille tirée du catalogue sert vraiment.

        Elle se publie sans retouche, et la publication la gèle comme
        n'importe quelle autre.
        """
        guide = self.env["bf.interview.guide"].browse(
            self.presel.action_create_guide()["res_id"]
        )
        guide.action_publish()
        self.assertEqual(guide.state, "publiee")
        with self.assertRaises(UserError):
            guide.write({"name": "Autre chose"})

    def test_the_guide_is_detached_from_its_template(self):
        """Retoucher la grille ne touche pas le catalogue, et l'inverse non plus.

        Sans copie profonde des ancrages, une organisation qui adapte sa grille
        récrirait le modèle livré, donc celui de toutes les suivantes.
        """
        guide = self.env["bf.interview.guide"].browse(
            self.presel.action_create_guide()["res_id"]
        )
        criterion = guide.criterion_ids[0]
        anchor = criterion.anchor_ids[0]
        source_label = self.presel.criterion_ids[0].anchor_ids[0].label

        criterion.write({"name": "Critère retouché"})
        anchor.write({"label": "Ancrage retouché"})

        self.assertEqual(self.presel.criterion_ids[0].anchor_ids[0].label, source_label)
        self.assertNotEqual(self.presel.criterion_ids[0].name, "Critère retouché")

    def test_guide_count_follows(self):
        self.assertEqual(self.presel.guide_count, 0)
        self.presel.action_create_guide()
        self.presel.invalidate_recordset(["guide_count"])
        self.assertEqual(self.presel.guide_count, 1)

    def test_a_template_without_criteria_refuses(self):
        empty = self.Template.create({
            "name": "Modèle vide", "round_type": "screening", "category": "metier",
        })
        with self.assertRaises(UserError):
            empty.action_create_guide()

    # ------------------------------------------------------------------
    # Le catalogue est en lecture seule
    # ------------------------------------------------------------------

    def test_officer_reads_the_catalogue_but_does_not_write_it(self):
        """Un recruteur tire des grilles ; il ne récrit pas le catalogue.

        Le catalogue est livré par le module : une retouche locale serait
        écrasée à la prochaine mise à jour sans que personne le sache.
        """
        as_officer = self.presel.with_user(self.officer)
        self.assertTrue(as_officer.name)
        with self.assertRaises(AccessError):
            as_officer.write({"name": "Récrit"})
        with self.assertRaises(AccessError):
            self.Template.with_user(self.officer).create({
                "name": "Le mien", "round_type": "screening", "category": "metier",
            })

    def test_officer_can_draw_a_guide_from_the_catalogue(self):
        """La paire du contrôle ci-dessus : lecture seule n'est pas inutile."""
        action = self.presel.with_user(self.officer).action_create_guide()
        guide = self.env["bf.interview.guide"].browse(action["res_id"])
        self.assertEqual(guide.source_template_id, self.presel)
        self.assertEqual(len(guide.criterion_ids), len(self.presel.criterion_ids))

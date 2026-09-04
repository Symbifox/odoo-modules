# Part of bf_recruitment. Voir LICENSE.
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestInterviewBook(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref("base.main_company")
        cls.job = cls.env["hr.job"].create({
            "name": "Conseiller TI",
            "company_id": cls.company.id,
        })
        cls.guide = cls.env["bf.interview.guide"].create({
            "name": "Conseiller TI - tour 2",
            "round_type": "technique",
            "scale_max": 5,
            "criterion_ids": [
                (0, 0, {
                    "name": "Diagnostic sous pression",
                    "weight": 2.0,
                    "sequence": 10,
                    "anchor_ids": [
                        (0, 0, {"score": 1, "label": "Se fige"}),
                        (0, 0, {"score": 5, "label": "Isole la cause en nommant ses hypotheses"}),
                    ],
                }),
                (0, 0, {
                    "name": "Habilitation requise",
                    "weight": 1.0,
                    "sequence": 20,
                    "is_knockout": True,
                    "knockout_min": 3,
                }),
            ],
        })
        cls.guide.action_publish()

        Users = cls.env["res.users"].with_context(no_reset_password=True)
        interviewer_group = cls.env.ref("hr_recruitment.group_hr_recruitment_interviewer")
        officer_group = cls.env.ref("hr_recruitment.group_hr_recruitment_user")
        cls.alice = Users.create({
            "name": "Alice Panelliste", "login": "alice_t",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id, interviewer_group.id])],
        })
        cls.bruno = Users.create({
            "name": "Bruno Panelliste", "login": "bruno_t",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id, interviewer_group.id])],
        })
        cls.officer = Users.create({
            "name": "Officier", "login": "officer_t",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id, officer_group.id])],
        })

        # `candidate_id` est NOT NULL en base, et l'inverse de `partner_name`
        # ne tourne qu'APRES l'insertion : la personne se cree d'abord.
        cls.candidate = cls.env["hr.candidate"].create({"partner_name": "Marie Tremblay"})
        cls.applicant = cls.env["hr.applicant"].create({
            "candidate_id": cls.candidate.id,
            "job_id": cls.job.id,
            "company_id": cls.company.id,
        })
        cls.interview = cls.env["bf.interview"].create({
            "applicant_id": cls.applicant.id,
            "guide_id": cls.guide.id,
            "round_number": 1,
            "interviewer_ids": [(6, 0, [cls.alice.id, cls.bruno.id])],
        })

    # ------------------------------------------------------------------
    # Le gel de la grille
    # ------------------------------------------------------------------

    def test_published_guide_is_frozen(self):
        with self.assertRaises(UserError):
            self.guide.write({"name": "Autre nom"})
        with self.assertRaises(UserError):
            self.guide.criterion_ids[0].write({"weight": 9.0})
        with self.assertRaises(UserError):
            self.guide.criterion_ids[0].unlink()

    def test_published_guide_still_accepts_job_assignment(self):
        """Rattacher la grille a un poste ne change pas ce qui a ete evalue."""
        self.guide.write({"job_ids": [(4, self.job.id)]})
        self.assertIn(self.job, self.guide.job_ids)

    def test_new_version_is_a_separate_draft(self):
        new_guide = self.env["bf.interview.guide"].browse(
            self.guide.action_new_version()["res_id"]
        )
        self.assertEqual(new_guide.version, 2)
        self.assertEqual(new_guide.state, "brouillon")
        self.assertEqual(new_guide.previous_version_id, self.guide)
        self.assertEqual(len(new_guide.criterion_ids), 2)
        # L'ancienne n'a pas bouge : la seance de l'an dernier reste lisible.
        self.assertEqual(self.guide.version, 1)
        self.assertEqual(self.guide.state, "publiee")

    def test_a_published_guide_never_goes_back_to_draft(self):
        with self.assertRaises(UserError):
            self.guide.write({"state": "brouillon"})
        self.guide.action_set_archived()
        self.assertEqual(self.guide.state, "archivee")
        with self.assertRaises(UserError):
            self.guide.write({"state": "brouillon"})

    def test_interview_refuses_a_draft_guide(self):
        draft = self.env["bf.interview.guide"].create({
            "name": "Brouillon",
            "criterion_ids": [(0, 0, {"name": "Un critere"})],
        })
        with self.assertRaises(ValidationError):
            self.env["bf.interview"].create({
                "applicant_id": self.applicant.id,
                "guide_id": draft.id,
                "interviewer_ids": [(6, 0, [self.alice.id])],
            })

    def test_publish_refuses_an_empty_guide(self):
        empty = self.env["bf.interview.guide"].create({"name": "Vide"})
        with self.assertRaises(UserError):
            empty.action_publish()

    # ------------------------------------------------------------------
    # Les notations
    # ------------------------------------------------------------------

    def test_ratings_are_generated_per_criterion_and_per_interviewer(self):
        self.assertEqual(len(self.interview.rating_line_ids), 4)
        self.assertEqual(
            set(self.interview.rating_line_ids.mapped("user_id")),
            {self.alice, self.bruno},
        )

    def test_removing_an_interviewer_keeps_written_ratings(self):
        alice_ratings = self.interview.rating_line_ids.filtered(lambda r: r.user_id == self.alice)
        alice_ratings[0].with_user(self.alice).write({"score": 4})
        self.interview.write({"interviewer_ids": [(6, 0, [self.bruno.id])]})
        remaining = self.env["bf.interview.rating"].search([
            ("interview_id", "=", self.interview.id), ("user_id", "=", self.alice.id),
        ])
        # La ligne notee survit, la ligne vierge disparait.
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining.score, 4)

    def test_score_is_a_weighted_mean_across_raters(self):
        self._rate(self.alice, [4, 5])
        self._rate(self.bruno, [2, 3])
        self._submit(self.alice)
        self._submit(self.bruno)
        self.interview.invalidate_recordset()
        # Critere 1 (poids 2) : moyenne 3. Critere 2 (poids 1) : moyenne 4.
        self.assertAlmostEqual(self.interview.score_total, 3 * 2.0 + 4 * 1.0, places=2)
        self.assertAlmostEqual(self.interview.score_max, 5 * 3.0, places=2)
        self.assertEqual(self.interview.submitted_count, 4)

    def test_knockout_is_flagged_not_enforced(self):
        self._rate(self.alice, [5, 1])
        self._submit(self.alice)
        self.interview.invalidate_recordset()
        self.assertTrue(self.interview.knockout_failed)
        # Le drapeau n'ecarte personne : la candidature reste ouverte.
        self.assertFalse(self.applicant.refuse_reason_id)

    def test_submit_requires_every_criterion_scored(self):
        ratings = self.interview.with_user(self.alice).rating_line_ids
        ratings[0].with_user(self.alice).write({"score": 4})
        with self.assertRaises(UserError):
            self.interview.with_user(self.alice).action_submit()

    def test_submitted_rating_is_immutable(self):
        self._rate(self.alice, [4, 4])
        self._submit(self.alice)
        rating = self.env["bf.interview.rating"].search([
            ("interview_id", "=", self.interview.id), ("user_id", "=", self.alice.id),
        ], limit=1)
        with self.assertRaises(UserError):
            rating.with_user(self.alice).write({"score": 5})

    def test_a_rating_belongs_to_its_author(self):
        bruno_rating = self.env["bf.interview.rating"].search([
            ("interview_id", "=", self.interview.id), ("user_id", "=", self.bruno.id),
        ], limit=1)
        with self.assertRaises(UserError):
            bruno_rating.with_user(self.alice).write({"score": 1})

    # ------------------------------------------------------------------
    # Le depot a l'aveugle
    # ------------------------------------------------------------------

    def test_blind_hides_the_other_ratings_until_i_submit(self):
        self._rate(self.bruno, [5, 5])
        self._submit(self.bruno)

        # Alice n'a rien depose : elle ne voit que ses propres lignes.
        self.env.invalidate_all()
        visible = self.env["bf.interview.rating"].with_user(self.alice).search([
            ("interview_id", "=", self.interview.id),
        ])
        self.assertEqual(set(visible.mapped("user_id")), {self.alice})

        self._rate(self.alice, [3, 3])
        self._submit(self.alice)

        self.env.invalidate_all()
        visible = self.env["bf.interview.rating"].with_user(self.alice).search([
            ("interview_id", "=", self.interview.id),
        ])
        self.assertEqual(set(visible.mapped("user_id")), {self.alice, self.bruno})

    def test_not_blind_shows_everything(self):
        self.interview.write({"blind": False})
        self._rate(self.bruno, [5, 5])
        self._submit(self.bruno)
        self.env.invalidate_all()
        visible = self.env["bf.interview.rating"].with_user(self.alice).search([
            ("interview_id", "=", self.interview.id),
        ])
        self.assertEqual(set(visible.mapped("user_id")), {self.alice, self.bruno})

    def test_officer_sees_every_rating(self):
        self._rate(self.bruno, [5, 5])
        self._submit(self.bruno)
        self.env.invalidate_all()
        visible = self.env["bf.interview.rating"].with_user(self.officer).search([
            ("interview_id", "=", self.interview.id),
        ])
        self.assertEqual(len(visible), 4)

    def test_interviewer_sees_only_the_panels_they_sit_on(self):
        other = self.env["bf.interview"].create({
            "applicant_id": self.applicant.id,
            "guide_id": self.guide.id,
            "round_number": 2,
            "interviewer_ids": [(6, 0, [self.bruno.id])],
        })
        self.env.invalidate_all()
        visible = self.env["bf.interview"].with_user(self.alice).search([
            ("applicant_id", "=", self.applicant.id),
        ])
        self.assertIn(self.interview, visible)
        self.assertNotIn(other, visible)

    # ------------------------------------------------------------------
    # Les deux cahiers
    # ------------------------------------------------------------------

    def test_candidate_copy_hides_the_evaluator_names(self):
        """La copie remise a la personne evaluee ne nomme aucun tiers."""
        self._rate(self.alice, [4, 4])
        self._submit(self.alice)
        self.interview.action_mark_held()
        reason = self.env["hr.applicant.refuse.reason"].create({"name": "Profil non retenu"})
        self.applicant.write({
            "refuse_reason_id": reason.id,
            "decision_note": "Le tour 2 n'a pas montre le diagnostic attendu.",
        })
        Report = self.env["ir.actions.report"]

        def render(xmlid):
            body, _kind = Report._render_qweb_html(xmlid, self.applicant.ids)
            return body.decode() if isinstance(body, bytes) else body

        full = render("bf_recruitment.report_interview_book")
        copy = render("bf_recruitment.report_interview_book_candidate")

        self.assertIn("Panelliste", full)
        self.assertNotIn("Panelliste", copy)
        # La personne qui a decide est un tiers elle aussi.
        self.assertNotIn(self.env.user.name, copy)
        # Ce qui a ete evalue reste, lui : la copie n'est pas une coquille vide.
        self.assertIn("Diagnostic sous pression", copy)
        self.assertIn("diagnostic attendu", copy)

    # ------------------------------------------------------------------
    # La decision
    # ------------------------------------------------------------------

    def test_refusing_after_a_held_interview_requires_a_written_reason(self):
        self.interview.action_mark_held()
        reason = self.env["hr.applicant.refuse.reason"].create({"name": "Profil non retenu"})
        with self.assertRaises(ValidationError):
            self.applicant.write({"refuse_reason_id": reason.id})
        self.applicant.write({
            "refuse_reason_id": reason.id,
            "decision_note": "Le tour 2 n'a pas montre le diagnostic attendu.",
        })
        self.assertEqual(self.applicant.decided_by_id, self.env.user)
        self.assertTrue(self.applicant.decision_date)

    def test_refusing_without_any_interview_stays_frictionless(self):
        candidate = self.env["hr.candidate"].create({"partner_name": "Sans entrevue"})
        fresh = self.env["hr.applicant"].create({
            "candidate_id": candidate.id,
            "job_id": self.job.id,
        })
        reason = self.env["hr.applicant.refuse.reason"].create({"name": "Hors criteres"})
        fresh.write({"refuse_reason_id": reason.id})
        self.assertEqual(fresh.refuse_reason_id, reason)
        self.assertEqual(fresh.decided_by_id, self.env.user)

    def test_applicant_score_averages_held_interviews(self):
        self._rate(self.alice, [4, 4])
        self._submit(self.alice)
        self.interview.action_mark_held()
        self.applicant.invalidate_recordset()
        self.assertEqual(self.applicant.held_interview_count, 1)
        self.assertAlmostEqual(
            self.applicant.interview_score_pct, self.interview.score_pct, places=2,
        )

    # ------------------------------------------------------------------
    # Outils
    # ------------------------------------------------------------------

    def _rate(self, user, scores):
        ratings = self.env["bf.interview.rating"].sudo().search([
            ("interview_id", "=", self.interview.id), ("user_id", "=", user.id),
        ], order="sequence, id")
        self.assertEqual(len(ratings), len(scores))
        for rating, score in zip(ratings, scores):
            rating.with_user(user).write({"score": score})

    def _submit(self, user):
        self.interview.with_user(user).action_submit()

    def test_anchor_out_of_scale_raises_on_its_own_create(self):
        """La note hors échelle doit lever à l'écriture de l'ancrage.

        🔴 Le `@api.constrains` de la grille écoute `criterion_ids` et
        `scale_max` : créer un ancrage ne touche ni l'un ni l'autre. Sans la
        contrainte posée sur l'ancrage lui-même, la création passait et
        l'erreur ne sortait qu'à la prochaine écriture sur la grille, en
        accusant une opération sans rapport.
        """
        criterion = self.guide.criterion_ids[0]
        with self.assertRaises(ValidationError):
            self.env["bf.interview.anchor"].create({
                "criterion_id": criterion.id, "score": 9, "label": "Hors échelle",
            })
            self.env.flush_all()

    def test_score_survives_a_sequential_deposit(self):
        """🔴 Le score doit compter TOUT LE MONDE, même déposé en séquence.

        Le défaut, trouvé par le QA de bout en bout du 2026-08-31 : le calcul
        traversait `interview.sudo().rating_line_ids`. Le cache de l'ORM
        appartient à la transaction, pas à l'environnement, et cette relation
        n'a ni domaine ni dépendance au contexte : une seule case de cache. La
        lecture filtrée de la DEUXIÈME personne du panel, faite au moment où
        elle dépose, remplissait la case avec ses seules lignes, et le `sudo()`
        qui suivait relisait la case.

        Résultat en démonstration : six notations déposées en base,
        `submitted_count` stocké à trois, et un score qui était exactement
        celui du dernier à déposer.

        Ce test dépose dans l'ordre, chacun sous son propre compte, puis lit
        les champs STOCKÉS. Il tombe si quelqu'un remet une traversée de
        relation à la place du `search()`.
        """
        self._rate(self.alice, [5, 4])
        self._submit(self.alice)
        self._rate(self.bruno, [3, 2])
        self._submit(self.bruno)
        self.env.flush_all()
        self.interview.invalidate_recordset()

        self.assertEqual(
            self.interview.submitted_count, 4,
            "Le compte stocké ne voit pas les quatre notations déposées : le "
            "calcul a tourné sur la vue d'un seul évaluateur.",
        )
        self.assertEqual(
            set(self.interview.submitted_user_ids), {self.alice, self.bruno},
            "Les deux évaluateurs doivent figurer parmi ceux qui ont déposé.",
        )
        criteria = self.guide.criterion_ids.sorted("sequence")
        attendu = sum(
            ((a + b) / 2.0) * c.weight
            for (a, b), c in zip(((5, 3), (4, 2)), criteria)
        )
        self.assertAlmostEqual(
            self.interview.score_total, attendu, places=2,
            msg="Le score doit être la moyenne des DEUX évaluateurs par critère.",
        )


# Part of bf_recruitment_privacy. Voir LICENSE.
"""Ce qu'on prouve ici.

1. La campagne détruit POUR DE VRAI, au lieu d'archiver en certifiant.
2. Elle emporte la personne, les séances, les notations et les pièces jointes.
3. Elle LÈVE quand l'agrégat manque, et une ligne qui lève n'est pas certifiée.
4. L'agrégat ne porte aucun nom, et il survit à la destruction.
5. La surcharge RELAIE à `super()` pour les modèles qu'elle ne possède pas.
"""

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRecruitmentPrivacy(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref("base.main_company")
        cls.rule = cls.env.ref("bf_recruitment_privacy.retention_recruitment_application")
        cls.rule_hr = cls.env.ref("privacy_consent.retention_calendar_hr_files")
        cls.Aggregate = cls.env["bf.interview.aggregate"]
        cls.Classification = cls.env["privacy.document.classification"]

        cls.job = cls.env["hr.job"].create({
            "name": "Conseiller TI",
            "company_id": cls.company.id,
        })
        cls.guide = cls.env["bf.interview.guide"].create({
            "name": "Conseiller TI - tour 2",
            "round_type": "technique",
            "scale_max": 5,
            "company_id": cls.company.id,
            "criterion_ids": [
                (0, 0, {"name": "Diagnostic sous pression", "weight": 2.0, "sequence": 10}),
                (0, 0, {"name": "Clarte de la parole", "weight": 1.0, "sequence": 20}),
            ],
        })
        cls.guide.action_publish()

        cls.panelist = cls.env["res.users"].create({
            "name": "Membre du panel",
            "login": "panel_privacy",
            "email": "panel@example.invalid",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("hr_recruitment.group_hr_recruitment_interviewer").id,
            ])],
        })

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------

    def _make_applicant(self, name="Camille Sans-Nom"):
        """Une candidature complète : personne, CV, séance tenue et notée."""
        candidate = self.env["hr.candidate"].create({
            "partner_name": name,
            "email_from": "camille@example.invalid",
            "company_id": self.company.id,
        })
        applicant = self.env["hr.applicant"].create({
            "candidate_id": candidate.id,
            "job_id": self.job.id,
            "company_id": self.company.id,
        })
        self.env["ir.attachment"].create({
            "name": "cv.pdf",
            "res_model": "hr.applicant",
            "res_id": applicant.id,
            "datas": b"MTIzNA==",
        })
        self.env["ir.attachment"].create({
            "name": "lettre.pdf",
            "res_model": "hr.candidate",
            "res_id": candidate.id,
            "datas": b"MTIzNA==",
        })
        interview = self.env["bf.interview"].create({
            "applicant_id": applicant.id,
            "guide_id": self.guide.id,
            "company_id": self.company.id,
            "interviewer_ids": [(6, 0, [self.panelist.id])],
            "date_start": "2024-03-04 14:00:00",
        })
        for rating in interview.rating_line_ids:
            rating.sudo().write({"score": 4, "comment": "Explique en nommant ses hypotheses."})
        interview.rating_line_ids.sudo().write({"state": "depose"})
        interview.action_mark_held()
        self.env["ir.attachment"].create({
            "name": "notes-panel.pdf",
            "res_model": "bf.interview",
            "res_id": interview.id,
            "datas": b"MTIzNA==",
        })
        return candidate, applicant, interview

    def _classify(self, applicant):
        applicant.write({"date_closed": "2024-03-20 10:00:00"})
        return self.Classification.search([
            ("res_model", "=", "hr.applicant"), ("res_id", "=", applicant.id),
        ])

    def _campaign_for(self, classification):
        campaign = self.env["privacy.destruction.campaign"].create({
            "name": "Purge des candidatures",
            "retention_calendar_id": classification.retention_calendar_id.id,
            "cutoff_date": "2099-12-31",
            "company_id": self.company.id,
        })
        campaign.action_scan()
        line = campaign.line_ids.filtered(
            lambda l: l.res_model == classification.res_model
            and l.res_id == classification.res_id
        )
        self.assertTrue(line, "Le balayage n'a pas retenu la candidature classée.")
        campaign.action_approve()
        return campaign, line

    # ------------------------------------------------------------------
    # 1. La classification, et la bascule
    # ------------------------------------------------------------------

    def test_closing_an_application_classifies_it(self):
        """Sans classification, la règle de conservation ne s'applique à rien."""
        _candidate, applicant, _interview = self._make_applicant()
        self.assertFalse(self.Classification.search([
            ("res_model", "=", "hr.applicant"), ("res_id", "=", applicant.id),
        ]), "Une candidature ouverte n'a pas encore d'horloge qui court.")

        classification = self._classify(applicant)
        self.assertEqual(len(classification), 1)
        self.assertEqual(classification.retention_calendar_id, self.rule)
        self.assertEqual(str(classification.document_date), "2024-03-20")

    def test_a_refusal_starts_the_clock_at_the_refusal(self):
        """🔴 `date_closed` est la date d'EMBAUCHE, pas la clôture.

        Le coeur ne la pose que sur une étape `hired_stage`. Un refus pose
        `refuse_date`. Sans ce repli, l'horloge d'une candidature refusée
        partait de sa CRÉATION, et le dossier se détruisait trop tôt.
        """
        _candidate, applicant, _interview = self._make_applicant("Refusée")
        motif = self.env["hr.applicant.refuse.reason"].search([], limit=1)
        applicant.write({
            "decision_note": "Deux tours tenus, habilitation absente.",
            "refuse_reason_id": motif.id,
            "refuse_date": "2026-06-15 09:00:00",
            "active": False,
        })
        classification = self.Classification.search([
            ("res_model", "=", "hr.applicant"), ("res_id", "=", applicant.id),
        ])
        self.assertEqual(len(classification), 1, "Un refus doit classer le dossier.")
        self.assertEqual(
            str(classification.document_date), "2026-06-15",
            "L'horloge part de la création au lieu du refus : le dossier se "
            "détruira avant le terme annoncé.",
        )

    def test_a_refusal_never_counts_as_a_hire(self):
        """🔴 Le contournement était pire que le trou.

        `hr.job.no_of_hired_employee` compte les candidatures qui portent une
        `date_closed`. Écrire ce champ à la main pour donner une date à la
        classification fait donc compter une personne REFUSÉE comme embauchée,
        et divise le coût par embauche d'autant.
        """
        _candidate, applicant, _interview = self._make_applicant("Refusée aussi")
        motif = self.env["hr.applicant.refuse.reason"].search([], limit=1)
        applicant.write({
            "decision_note": "Deux tours tenus, habilitation absente.",
            "refuse_reason_id": motif.id,
            "refuse_date": "2026-06-15 09:00:00",
            "active": False,
        })
        self.assertFalse(
            applicant.date_closed,
            "Un refus a posé une date d'embauche.",
        )
        self.job.invalidate_recordset()
        self.assertEqual(
            self.job.no_of_hired_employee, 0,
            "Une candidature refusée est comptée parmi les embauches du poste.",
        )

    def test_hiring_switches_the_file_to_hr_001(self):
        """La bascule est un changement de rattachement, pas d'échéance."""
        candidate, applicant, _interview = self._make_applicant()
        classification = self._classify(applicant)
        self.assertEqual(classification.retention_calendar_id, self.rule)

        employee = self.env["hr.employee"].create({
            "name": "Camille Sans-Nom", "company_id": self.company.id,
        })
        candidate.write({"employee_id": employee.id})
        applicant.write({"date_closed": "2024-04-01 10:00:00"})

        classification.invalidate_recordset()
        self.assertEqual(
            classification.retention_calendar_id, self.rule_hr,
            "Une personne embauchée doit passer sous RH-001.",
        )
        self.assertEqual(
            self.rule.total_retention_days, self.rule_hr.total_retention_days,
            "Les deux règles portent la même durée : un seul régime pour tout "
            "le dossier. Si ce test tombe, la bascule est devenue un changement "
            "d'échéance, ce que le régime retenu écarte.",
        )

    def test_classification_is_idempotent(self):
        """Deux clôtures ne font pas deux lignes à détruire."""
        _candidate, applicant, _interview = self._make_applicant()
        self._classify(applicant)
        applicant.write({"date_closed": "2024-05-02 10:00:00"})
        found = self.Classification.with_context(active_test=False).search([
            ("res_model", "=", "hr.applicant"), ("res_id", "=", applicant.id),
        ])
        self.assertEqual(len(found), 1)
        self.assertEqual(str(found.document_date), "2024-05-02")

    # ------------------------------------------------------------------
    # 2. L'agrégat
    # ------------------------------------------------------------------

    def test_aggregate_holds_no_identifier(self):
        _candidate, _applicant, interview = self._make_applicant("Alex Personne")
        self.Aggregate._build_for_year(2024, company=self.company)
        aggregate = self.Aggregate.search([("year", "=", 2024)])
        self.assertEqual(len(aggregate), 1)
        self.assertEqual(aggregate.interviews, 1)
        self.assertEqual(aggregate.candidates, 1)
        self.assertEqual(aggregate.evaluators, 1)
        self.assertEqual(aggregate.ratings, 2)
        self.assertEqual(len(aggregate.criterion_ids), 2)

        # Aucun champ de l'agrégat ne peut porter un nom de personne.
        # ⚠️ `create_uid` et `write_uid` sont exclus : ils nomment qui a lancé le
        # CALCUL, pas qui a été évalué ni qui a noté. Les retirer serait mentir
        # sur l'auteur d'une écriture ; les compter comme un identifiant de
        # candidat rendrait ce contrôle incapable de discriminer.
        audit_fields = ("create_uid", "write_uid")
        for field_name, field in aggregate._fields.items():
            if field_name in audit_fields:
                continue
            if field.type in ("many2one", "one2many", "many2many"):
                self.assertNotIn(
                    field.comodel_name,
                    ("res.users", "res.partner", "hr.candidate", "hr.applicant",
                     "hr.employee", "bf.interview", "bf.interview.rating"),
                    "L'agrégat ne doit pointer vers aucune personne ni vers "
                    "aucune séance : %s" % field_name,
                )
        self.assertNotIn("Alex Personne", str(aggregate.read()[0]))
        self.assertEqual(interview.state, "tenue")

    def test_aggregate_is_idempotent(self):
        self._make_applicant()
        self.Aggregate._build_for_year(2024, company=self.company)
        self.Aggregate._build_for_year(2024, company=self.company)
        self.assertEqual(
            self.Aggregate.search_count([("year", "=", 2024)]), 1,
            "Recalculer une année doit réécrire, pas empiler.",
        )

    def test_aggregate_counts_archived_interviews(self):
        """Une séance archivée garde ses notations : l'agrégat doit les voir."""
        _candidate, _applicant, interview = self._make_applicant()
        interview.write({"active": False})
        self.Aggregate._build_for_year(2024, company=self.company)
        aggregate = self.Aggregate.search([("year", "=", 2024)])
        self.assertEqual(aggregate.interviews, 1)

    def test_criterion_spread_is_measured(self):
        """L'écart entre évaluateurs est ce qui dit si un critère est compris."""
        _candidate, _applicant, interview = self._make_applicant()
        second = self.env["res.users"].create({
            "name": "Second panel", "login": "panel2_privacy",
            "email": "panel2@example.invalid",
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("hr_recruitment.group_hr_recruitment_interviewer").id,
            ])],
        })
        interview.write({"interviewer_ids": [(4, second.id)]})
        theirs = interview.rating_line_ids.filtered(lambda r: r.user_id == second)
        theirs.sudo().write({"score": 1, "state": "depose"})

        self.Aggregate._build_for_year(2024, company=self.company)
        aggregate = self.Aggregate.search([("year", "=", 2024)])
        line = aggregate.criterion_ids[0]
        self.assertEqual(line.ratings, 2)
        self.assertEqual(line.score_min, 1)
        self.assertEqual(line.score_max, 4)
        self.assertEqual(line.rater_spread_mean, 3.0)

    # ------------------------------------------------------------------
    # 3. La destruction
    # ------------------------------------------------------------------

    def test_campaign_without_aggregate_raises_and_certifies_nothing(self):
        """Le garde qui compte : agréger d'abord, détruire ensuite."""
        _candidate, applicant, _interview = self._make_applicant()
        classification = self._classify(applicant)
        campaign, line = self._campaign_for(classification)

        campaign.action_execute()

        self.assertEqual(line.state, "failed")
        self.assertIn("agrég", line.error_message)
        self.assertFalse(
            line.register_entry_id,
            "Une ligne qui lève ne doit RIEN inscrire au registre immuable.",
        )
        self.assertTrue(
            applicant.exists(), "La candidature ne devait pas être détruite.",
        )

    def test_campaign_destroys_for_real(self):
        candidate, applicant, interview = self._make_applicant()
        classification = self._classify(applicant)
        self.Aggregate._build_for_year(2024, company=self.company)
        campaign, line = self._campaign_for(classification)

        applicant_id, candidate_id, interview_id = applicant.id, candidate.id, interview.id
        campaign.action_execute()

        self.assertEqual(line.state, "done", line.error_message or "")
        self.assertTrue(line.register_entry_id, "Une destruction réelle s'inscrit.")

        Applicant = self.env["hr.applicant"].with_context(active_test=False)
        Candidate = self.env["hr.candidate"].with_context(active_test=False)
        Interview = self.env["bf.interview"].with_context(active_test=False)
        self.assertFalse(
            Applicant.browse(applicant_id).exists(),
            "🔴 Archivée au lieu d'être détruite : le défaut que ce pont corrige.",
        )
        self.assertFalse(
            Candidate.browse(candidate_id).exists(),
            "La personne porte le nom, le courriel et le téléphone : elle doit "
            "partir avec sa dernière candidature.",
        )
        self.assertFalse(Interview.browse(interview_id).exists())
        self.assertFalse(
            self.env["bf.interview.rating"].search([
                ("interview_id", "=", interview_id),
            ]),
            "Les notations et leurs commentaires doivent partir aussi.",
        )

    def test_attachments_and_messages_go_too(self):
        """🔴 La cascade SQL saute l'ORM et laisserait les CV derrière."""
        candidate, applicant, interview = self._make_applicant()
        classification = self._classify(applicant)
        self.Aggregate._build_for_year(2024, company=self.company)
        campaign, line = self._campaign_for(classification)

        targets = [
            ("hr.applicant", applicant.id),
            ("hr.candidate", candidate.id),
            ("bf.interview", interview.id),
        ]
        for model, res_id in targets:
            self.assertTrue(self.env["ir.attachment"].search_count([
                ("res_model", "=", model), ("res_id", "=", res_id),
            ]), "Le montage du test doit poser une pièce jointe sur %s." % model)

        campaign.action_execute()
        self.assertEqual(line.state, "done", line.error_message or "")

        for model, res_id in targets:
            self.assertFalse(self.env["ir.attachment"].search_count([
                ("res_model", "=", model), ("res_id", "=", res_id),
            ]), "Pièce jointe orpheline laissée sur %s." % model)
            self.assertFalse(self.env["mail.message"].search_count([
                ("model", "=", model), ("res_id", "=", res_id),
            ]), "Fil de discussion laissé sur %s." % model)

    def test_aggregate_survives_the_destruction(self):
        _candidate, applicant, _interview = self._make_applicant()
        classification = self._classify(applicant)
        self.Aggregate._build_for_year(2024, company=self.company)
        campaign, line = self._campaign_for(classification)
        campaign.action_execute()
        self.assertEqual(line.state, "done", line.error_message or "")

        aggregate = self.Aggregate.search([("year", "=", 2024)])
        self.assertEqual(len(aggregate), 1)
        self.assertEqual(aggregate.interviews, 1)
        self.assertEqual(aggregate.source_interview_count, 1)

    def test_candidate_with_another_application_survives(self):
        candidate, applicant, _interview = self._make_applicant()
        other = self.env["hr.applicant"].create({
            "candidate_id": candidate.id,
            "job_id": self.job.id,
            "company_id": self.company.id,
        })
        classification = self._classify(applicant)
        self.Aggregate._build_for_year(2024, company=self.company)
        campaign, line = self._campaign_for(classification)
        campaign.action_execute()

        self.assertEqual(line.state, "done", line.error_message or "")
        self.assertTrue(
            candidate.exists(),
            "Une personne qui a une autre candidature ouverte ne se détruit pas.",
        )
        self.assertTrue(other.exists())

    def test_hired_application_refuses_destruction(self):
        candidate, applicant, _interview = self._make_applicant()
        classification = self._classify(applicant)
        self.Aggregate._build_for_year(2024, company=self.company)
        employee = self.env["hr.employee"].create({
            "name": "Camille Sans-Nom", "company_id": self.company.id,
        })
        candidate.write({"employee_id": employee.id})
        campaign, line = self._campaign_for(classification)

        campaign.action_execute()
        self.assertEqual(line.state, "failed")
        self.assertIn("RH-001", line.error_message)
        self.assertFalse(line.register_entry_id)
        self.assertTrue(applicant.exists())

    def test_anonymize_is_refused(self):
        _candidate, applicant, _interview = self._make_applicant()
        classification = self._classify(applicant)
        self.Aggregate._build_for_year(2024, company=self.company)
        campaign, line = self._campaign_for(classification)
        line.write({"destruction_method": "anonymize"})

        campaign.action_execute()
        self.assertEqual(line.state, "failed")
        self.assertFalse(line.register_entry_id)
        self.assertTrue(applicant.exists())

    # ------------------------------------------------------------------
    # 4. La chaîne des ponts
    # ------------------------------------------------------------------

    def test_override_relays_to_super(self):
        """La chaîne compte six ponts : ne pas relayer les casse tous.

        On vise un modèle que ce pont ne possède pas (`res.partner`) et on
        vérifie que le comportement du socle s'applique encore.
        """
        partner = self.env["res.partner"].create({"name": "Fournisseur quelconque"})
        classification = self.Classification.create({
            "res_model": "res.partner",
            "res_id": partner.id,
            "pi_category": "identification",
            "retention_calendar_id": self.rule.id,
            "document_date": "2015-01-01",
            "company_id": self.company.id,
        })
        campaign, line = self._campaign_for(classification)
        campaign.action_execute()

        self.assertEqual(line.state, "done", line.error_message or "")
        self.assertFalse(
            partner.active,
            "Le socle archive un modèle qui porte `active` : c'est son "
            "comportement, et le relais doit le laisser s'appliquer.",
        )

    def test_classifiable_models_compose(self):
        allowed = self.Classification._privacy_classifiable_models()
        for model in ("hr.applicant", "hr.candidate", "bf.interview"):
            self.assertIn(model, allowed)
        self.assertNotIn("bf.interview.rating", allowed)
        self.assertNotIn("bf.interview.guide", allowed)
        # Les modèles du socle et ceux des autres ponts doivent survivre.
        self.assertIn("res.partner", allowed)
        self.assertIn("ir.attachment", allowed)

    def test_constraint_goes_through_the_hook(self):
        """La contrainte doit consulter le crochet, pas la constante.

        C'est ce qui fait que le pont marche sur les deux lignées de
        `privacy_consent` : celle qui porte `_privacy_classifiable_models()` et
        celle du catalogue, qui ne l'a pas et lit `ALLOWED_MODELS` en direct.
        Si quelqu'un retire la surcharge de `_check_allowed_model`, ce test
        reste vert sur l'arbre locataire et le module redevient inerte sur le
        catalogue : c'est pourquoi il vérifie le CHEMIN, pas seulement le
        résultat.
        """
        called = []
        original = type(self.Classification)._privacy_classifiable_models

        def spy(records):
            called.append(True)
            return original(records)

        self.patch(type(self.Classification), "_privacy_classifiable_models", spy)
        with self.assertRaises(ValidationError):
            self.Classification.create({
                "res_model": "ir.cron",
                "res_id": 1,
                "pi_category": "identification",
                "company_id": self.company.id,
            })
        self.assertTrue(
            called,
            "La contrainte n'est pas passée par `_privacy_classifiable_models()`. "
            "Sur la lignée publiée de privacy_consent, le pont serait inerte.",
        )

    def test_rating_cannot_be_classified(self):
        _candidate, _applicant, interview = self._make_applicant()
        with self.assertRaises(ValidationError):
            self.Classification.create({
                "res_model": "bf.interview.rating",
                "res_id": interview.rating_line_ids[0].id,
                "pi_category": "identification",
                "company_id": self.company.id,
            })

    def test_interview_can_be_destroyed_alone(self):
        _candidate, applicant, interview = self._make_applicant()
        classification = self.Classification.create({
            "res_model": "bf.interview",
            "res_id": interview.id,
            "pi_category": "identification",
            "retention_calendar_id": self.rule.id,
            "document_date": "2015-01-01",
            "company_id": self.company.id,
        })
        self.Aggregate._build_for_year(2024, company=self.company)
        campaign, line = self._campaign_for(classification)
        interview_id = interview.id
        campaign.action_execute()

        self.assertEqual(line.state, "done", line.error_message or "")
        self.assertFalse(
            self.env["bf.interview"].with_context(active_test=False)
            .browse(interview_id).exists()
        )
        self.assertTrue(
            applicant.exists(),
            "Détruire une séance ne détruit pas la candidature.",
        )

    def test_missing_record_raises_instead_of_certifying(self):
        _candidate, applicant, _interview = self._make_applicant()
        classification = self._classify(applicant)
        self.Aggregate._build_for_year(2024, company=self.company)
        campaign, line = self._campaign_for(classification)
        applicant.unlink()

        campaign.action_execute()
        self.assertEqual(line.state, "failed")
        self.assertFalse(
            line.register_entry_id,
            "Un enregistrement déjà parti ne s'atteste pas : sans ça, le "
            "registre certifierait une destruction qu'on n'a pas faite.",
        )

    # ------------------------------------------------------------------
    # Le contact laissé derrière
    # ------------------------------------------------------------------

    def _destroy_via_campaign(self, applicant):
        classification = self._classify(applicant)
        self.Aggregate._build_for_year(2024, company=self.company)
        campaign, line = self._campaign_for(classification)
        campaign.action_execute()
        self.assertEqual(line.state, "done", line.error_message or "")
        return line

    def test_the_contact_goes_with_the_person(self):
        """🔴 Le défaut mesuré le 2026-08-31 : après une destruction CERTIFIÉE,
        le contact créé par le flux restait ACTIF, avec son nom et son courriel,
        et plus rien ne le rattachait à quoi que ce soit."""
        candidate, applicant, _interview = self._make_applicant()
        partner = candidate.partner_id
        self.assertTrue(
            partner,
            "Le montage ne prouve rien si le coeur n'a pas créé de contact.",
        )
        partner_id = partner.id
        self._destroy_via_campaign(applicant)
        self.assertFalse(
            self.env["res.partner"].with_context(active_test=False)
            .browse(partner_id).exists(),
            "Le contact a survécu à la destruction de la personne.",
        )

    def test_a_contact_used_elsewhere_is_archived_not_destroyed(self):
        """La règle retenue : détruit s'il n'est référencé nulle part,
        archivé sinon. Un contact qui sert ailleurs n'est pas à nous."""
        candidate, applicant, _interview = self._make_applicant()
        partner = candidate.partner_id
        self.env["res.partner"].create({
            "name": "Rattachement qui retient le contact",
            "parent_id": partner.id,
        })
        partner_id = partner.id
        self._destroy_via_campaign(applicant)

        survivant = self.env["res.partner"].with_context(
            active_test=False).browse(partner_id)
        self.assertTrue(
            survivant.exists(),
            "Un contact que quelque chose référence ne se détruit pas.",
        )
        self.assertFalse(
            survivant.active,
            "Mais il ne reste pas actif non plus : il sort des vues.",
        )

    def test_the_contact_survives_while_another_application_does(self):
        """Le contact suit la personne, pas la candidature."""
        candidate, applicant, _interview = self._make_applicant()
        autre = self.env["hr.applicant"].create({
            "candidate_id": candidate.id,
            "job_id": self.job.id,
            "company_id": self.company.id,
        })
        partner = candidate.partner_id
        self._destroy_via_campaign(applicant)

        self.assertTrue(autre.exists(), "Le montage ne prouve rien sinon.")
        self.assertTrue(partner.exists())
        self.assertTrue(
            partner.active,
            "Tant qu'il reste une candidature, on ne touche pas au contact.",
        )

    def test_a_fresh_contact_is_seen_as_referenced_by_nobody(self):
        """⚠️ La paire du garde `id != %s`.

        `res_partner` porte des clés qui pointent vers `res_partner`, dont
        `commercial_partner_id`, qui vaut son PROPRE identifiant pour un contact
        sans société. Sans l'exclusion de sa propre ligne, aucun contact ne
        serait jamais orphelin et le module n'en détruirait aucun.
        """
        partner = self.env["res.partner"].create({"name": "Personne isolée"})
        ligne = self.env["privacy.destruction.campaign.line"]
        self.assertEqual(
            ligne._recruitment_partner_references(partner), [],
            "Un contact tout neuf ne doit être retenu par rien.",
        )
        enfant = self.env["res.partner"].create({
            "name": "Un enfant", "parent_id": partner.id,
        })
        self.assertIn(
            "res_partner.parent_id",
            ligne._recruitment_partner_references(partner),
            "Et le rattachement d'un enfant doit être VU, sinon le garde ne "
            "discrimine rien.",
        )
        self.assertTrue(enfant.exists())

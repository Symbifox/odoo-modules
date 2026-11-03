from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestProfilePrivacy(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["hr.employee"])
        cls.company = cls.env.company
        cls.types16 = cls.env.ref(
            "bf_employee_experience_profile.framework_types16")
        cls.istj = cls.env.ref("bf_employee_experience_profile.type_types16_istj")
        cls.behaviour4 = cls.env.ref(
            "bf_employee_experience_profile.framework_behaviour4")

    def _employee(self, name, departure=None):
        return self.env["hr.employee"].create({
            "name": name, "company_id": self.company.id,
            "departure_date": departure or False,
        })

    def _profile(self, employee):
        return self.env["bf.ex.profile"].create({
            "employee_id": employee.id,
            "framework_id": self.types16.id,
            "type_id": self.istj.id,
        })

    # ------------------------------------------------------------ déclaré

    def test_purpose_requires_express_consent(self):
        """Déclarer est volontaire : personne n'est tenu de remettre à son
        employeur une mesure de sa personnalité."""
        purpose = self.env.ref(
            "bf_employee_experience_profile_privacy.purpose_ex_profile")
        self.assertTrue(purpose.requires_consent)
        self.assertTrue(purpose.requires_express_opt_in)

        benefits = self.env.ref("bf_employee_experience_privacy.purpose_ex_benefits")
        self.assertFalse(benefits.requires_express_opt_in,
                         "les deux régimes ne doivent pas se confondre")

    def test_the_purpose_names_profiling_and_what_it_is_not(self):
        """⚠️ Le gestionnaire direct lit la fiche. La définition du profilage
        de l'art. 8.1 LPRPSP nomme « l'analyse du rendement au travail » : le
        résumé en langage clair doit le dire, et dire que ce n'est pas une
        évaluation."""
        purpose = self.env.ref(
            "bf_employee_experience_profile_privacy.purpose_ex_profile")
        summary = (purpose.plain_language_summary or "").lower()
        self.assertIn("profilage", summary)
        self.assertIn("rendement", summary)
        self.assertIn("pas une évaluation", summary)
        self.assertIn("gestionnaire", summary)

    def test_calendar_has_no_clock(self):
        """La durée est le lien d'emploi, pas un nombre d'années."""
        calendar = self.env.ref(
            "bf_employee_experience_profile_privacy.retention_ex_profile")
        self.assertEqual(calendar.code, "RH-EX-3")
        self.assertEqual(calendar.active_retention_years, 0)
        self.assertEqual(calendar.semi_active_retention_years, 0)
        self.assertEqual(calendar.final_disposition, "destroy")
        self.assertEqual(calendar.applies_to_model, "bf.ex.profile")

    def test_the_calendar_is_not_a_medical_record(self):
        """⚠️ Contrairement aux allergies : un questionnaire de style de
        comportement n'est pas un acte de santé, et le classer « médical »
        l'alignerait par erreur sur MED-001 et ses 7 ans."""
        calendar = self.env.ref(
            "bf_employee_experience_profile_privacy.retention_ex_profile")
        self.assertEqual(calendar.document_type, "hr_file")
        allergies = self.env.ref(
            "bf_employee_experience_health_privacy.retention_ex_allergy",
            raise_if_not_found=False,
        )
        if allergies:
            self.assertEqual(allergies.document_type, "medical",
                             "la règle des allergies, elle, reste médicale")

    def test_purge_cron_is_delivered_off(self):
        """🔴 Une purge irréversible ne doit jamais être un effet de bord
        d'une installation."""
        cron = self.env.ref(
            "bf_employee_experience_profile_privacy.cron_privacy_purge_departed_profiles")
        self.assertFalse(cron.active)

    def test_the_declaration_is_classifiable_but_not_the_catalogue(self):
        models = self.env["privacy.document.classification"] \
            ._privacy_classifiable_models()
        self.assertIn("bf.ex.profile", models)
        self.assertNotIn("bf.ex.profile.framework", models)
        self.assertNotIn("bf.ex.profile.type", models)

    # -------------------------------------------------------------- purge

    def test_a_present_employee_is_never_purged(self):
        self._profile(self._employee("Toujours là"))
        self.assertEqual(self.env["bf.ex.profile"]._cron_privacy_purge_departed(), 0)

    def test_a_recent_departure_is_within_the_grace_period(self):
        gone = self._employee("Partie hier", departure=self.today - relativedelta(days=1))
        self._profile(gone)
        self.assertEqual(self.env["bf.ex.profile"]._cron_privacy_purge_departed(), 0)

    def test_an_old_departure_is_purged_and_registered(self):
        gone = self._employee("Partie depuis longtemps",
                              departure=self.today - relativedelta(days=200))
        profile = self._profile(gone)
        before = self.env["privacy.destruction.register"].search_count([])
        purged = self.env["bf.ex.profile"]._cron_privacy_purge_departed()
        self.assertEqual(purged, 1)
        self.assertFalse(profile.exists())
        self.assertEqual(
            self.env["privacy.destruction.register"].search_count([]), before + 1)

    def test_the_register_entry_never_names_the_type(self):
        """🔴 L'inscrire reviendrait à conserver pour toujours, dans un
        registre immuable, le renseignement qu'on vient de détruire."""
        gone = self._employee("Partie et inscrite",
                              departure=self.today - relativedelta(days=200))
        self._profile(gone)
        self.env["bf.ex.profile"]._cron_privacy_purge_departed()
        entry = self.env["privacy.destruction.register"].search(
            [("res_model", "=", "bf.ex.profile")], order="id desc", limit=1)
        self.assertTrue(entry)
        blob = " ".join([
            entry.document_description or "", entry.pi_categories or "",
            entry.res_name or "", entry.legal_basis or "",
        ])
        self.assertNotIn("ISTJ", blob)
        self.assertNotIn(self.types16.name, blob)
        self.assertIn(gone.display_name, entry.res_name or "",
                      "le registre nomme la personne, c'est son objet")

    def test_the_scores_leave_with_the_declaration(self):
        gone = self._employee("Partie chiffrée",
                              departure=self.today - relativedelta(days=200))
        items = {item.code: item for item in self.behaviour4.item_ids}
        profile = self.env["bf.ex.profile"].create({
            "employee_id": gone.id,
            "framework_id": self.behaviour4.id,
            "score_ids": [
                (0, 0, {"type_id": items["d"].id, "value": 55.0}),
                (0, 0, {"type_id": items["s"].id, "value": 20.0}),
            ],
        })
        score_ids = profile.score_ids.ids
        self.env["bf.ex.profile"]._cron_privacy_purge_departed()
        self.assertFalse(self.env["bf.ex.profile.score"].browse(score_ids).exists())

    def test_no_aggregate_survives_the_purge(self):
        """⚠️ Le pont des avantages garde un agrégat anonyme ; ici, non. La
        répartition des types d'un effectif parti ne sert aucune décision."""
        self.assertNotIn("bf.ex.profile.aggregate", self.env)

    # ----------------------------------------------------------- campagne

    def test_a_campaign_refuses_to_anonymise(self):
        """Anonymiser un type de profil n'a pas de sens : ce qui est sensible
        est le lien entre la personne et le type."""
        employee = self._employee("Campagne")
        profile = self._profile(employee)
        line = self.env["privacy.destruction.campaign.line"].new({
            "res_model": "bf.ex.profile",
            "res_id": profile.id,
            "res_name": employee.display_name,
            "destruction_method": "anonymize",
        })
        with self.assertRaises(UserError):
            line._execute_destruction()
        self.assertTrue(profile.exists())

    def test_a_campaign_on_a_gone_record_says_so(self):
        line = self.env["privacy.destruction.campaign.line"].new({
            "res_model": "bf.ex.profile",
            "res_id": 0,
            "res_name": "Déjà partie",
            "destruction_method": "delete",
        })
        with self.assertRaises(UserError):
            line._execute_destruction()

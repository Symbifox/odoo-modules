from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestHealthPrivacy(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["hr.employee"])
        cls.company = cls.env.company
        cls.peanut = cls.env.ref("bf_employee_experience_health.allergen_peanut")
        cls.latex = cls.env.ref("bf_employee_experience_health.allergen_latex")

    def _employee(self, name, departure=None):
        return self.env["hr.employee"].create({
            "name": name, "company_id": self.company.id,
            "departure_date": departure or False,
        })

    def _allergy(self, employee, allergen=None, severity="severe"):
        return self.env["bf.ex.allergy"].create({
            "employee_id": employee.id,
            "allergen_id": (allergen or self.peanut).id,
            "severity": severity,
        })

    # ---------------- déclaration ----------------

    def test_purpose_requires_express_consent(self):
        """⚠️ Contrairement aux avantages : un renseignement de santé est
        sensible (art. 59 LPRPSP) et sa collecte est volontaire."""
        purpose = self.env.ref("bf_employee_experience_health_privacy.purpose_ex_allergy")
        self.assertTrue(purpose.requires_consent)
        self.assertTrue(purpose.requires_express_opt_in)

        benefits = self.env.ref("bf_employee_experience_privacy.purpose_ex_benefits")
        self.assertFalse(benefits.requires_express_opt_in,
                         "les deux régimes ne doivent pas se confondre")

    def test_calendar_has_no_clock(self):
        """La durée est le lien d'emploi, pas un nombre d'années. Écrire 3 ou 7
        donnerait une fausse précision."""
        calendar = self.env.ref("bf_employee_experience_health_privacy.retention_ex_allergy")
        self.assertEqual(calendar.code, "RH-EX-2")
        self.assertEqual(calendar.active_retention_years, 0)
        self.assertEqual(calendar.semi_active_retention_years, 0)
        self.assertEqual(calendar.final_disposition, "destroy")

    def test_purge_cron_is_delivered_off(self):
        """🔴 Une purge irréversible ne doit jamais être un effet de bord
        d'une installation."""
        cron = self.env.ref(
            "bf_employee_experience_health_privacy.cron_privacy_purge_departed_allergies"
        )
        self.assertFalse(cron.active)

    def test_declaration_is_classifiable_but_not_the_catalogue(self):
        allowed = self.env["privacy.document.classification"]._privacy_classifiable_models()
        self.assertIn("bf.ex.allergy", allowed)
        self.assertNotIn("bf.ex.allergen", allowed,
                         "un catalogue d'allergènes ne concerne personne")

    # ---------------- la purge ----------------

    def test_grace_period_protects_a_fresh_departure(self):
        """Un départ se défait parfois dans les premières semaines, et une
        déclaration détruite ne se retrouve pas."""
        fresh = self._employee("Partie hier", departure=self.today - relativedelta(days=1))
        allergy = self._allergy(fresh)
        self.assertNotIn(allergy, self.env["bf.ex.allergy"]._departed_declarations())

    def test_old_departure_is_purged_and_written_to_the_register(self):
        gone = self._employee("Partie il y a longtemps",
                              departure=self.today - relativedelta(days=200))
        allergy = self._allergy(gone)
        allergy_id = allergy.id
        Register = self.env["privacy.destruction.register"]
        before = Register.search_count([])

        count = self.env["bf.ex.allergy"]._cron_privacy_purge_departed()

        self.assertEqual(count, 1)
        self.assertFalse(self.env["bf.ex.allergy"].browse(allergy_id).exists())
        self.assertEqual(Register.search_count([]), before + 1)

    def test_register_entry_never_names_the_allergen(self):
        """🔴 Le registre est IMMUABLE. Y inscrire l'allergène reviendrait à
        conserver pour toujours le renseignement de santé qu'on vient de
        détruire."""
        gone = self._employee("Partie", departure=self.today - relativedelta(days=200))
        self._allergy(gone, allergen=self.peanut)
        Register = self.env["privacy.destruction.register"]
        before = Register.search([]).ids

        self.env["bf.ex.allergy"]._cron_privacy_purge_departed()

        entry = Register.search([("id", "not in", before)], limit=1)
        self.assertTrue(entry)
        blob = "%s %s %s" % (
            entry.document_description, entry.pi_categories or "", entry.notes or "",
        )
        self.assertNotIn("Arachides", blob)
        self.assertIn("déclaration", blob.lower())

    def test_active_employee_is_never_purged(self):
        here = self._employee("Toujours là")
        allergy = self._allergy(here)
        self.env["bf.ex.allergy"]._cron_privacy_purge_departed()
        self.assertTrue(allergy.exists())

    # ---------------- la campagne ----------------

    def _line(self, record, method="delete"):
        campaign = self.env["privacy.destruction.campaign"].create({
            "name": "Campagne d'essai santé",
        })
        return self.env["privacy.destruction.campaign.line"].create({
            "campaign_id": campaign.id,
            "res_model": record._name, "res_id": record.id,
            "res_name": record.display_name, "destruction_method": method,
        })

    def test_campaign_destroys_a_declaration(self):
        employee = self._employee("Sujet campagne")
        allergy = self._allergy(employee)
        allergy_id = allergy.id
        self._line(allergy)._execute_destruction()
        self.assertFalse(self.env["bf.ex.allergy"].browse(allergy_id).exists())

    def test_campaign_refuses_anonymisation(self):
        employee = self._employee("Sujet refus")
        allergy = self._allergy(employee)
        with self.assertRaises(UserError):
            self._line(allergy, method="anonymize")._execute_destruction()
        self.assertTrue(allergy.exists())

    def test_bridge_relays_to_the_others(self):
        """⚠️ Deux ponts de plus se suivent maintenant sur cette méthode."""
        line = self.env["privacy.destruction.campaign.line"].new({
            "res_model": "res.partner", "res_id": 0,
        })
        self.assertIsNone(line._execute_destruction())

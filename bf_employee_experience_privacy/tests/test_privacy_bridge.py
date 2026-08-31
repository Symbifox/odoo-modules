from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPrivacyBridge(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["hr.employee"])
        cls.company = cls.env.company
        cls.employee = cls.env["hr.employee"].create({
            "name": "Sujet", "company_id": cls.company.id,
        })
        cls.other = cls.env["hr.employee"].create({
            "name": "Autre", "company_id": cls.company.id,
        })
        cls.benefit = cls.env["bf.ex.benefit"].create({
            "name": "Physiothérapie (essai)", "company_id": cls.company.id,
            "category": "health", "cost_model": "per_use",
        })
        for employee in (cls.employee, cls.other):
            cls.env["bf.ex.entitlement"].create({
                "employee_id": employee.id, "benefit_id": cls.benefit.id,
                "source": "manual", "reason": "Pour l'essai.",
                "date_start": cls.today - relativedelta(years=1),
            })
        cls.usage = cls.env["bf.ex.usage"].create({
            "employee_id": cls.employee.id, "benefit_id": cls.benefit.id,
            "date": cls.today, "amount": 120.0,
        })
        cls.usage.action_confirm()

    # ---------------- déclaration ----------------

    def test_purpose_and_calendar_are_declared(self):
        purpose = self.env.ref("bf_employee_experience_privacy.purpose_ex_benefits")
        self.assertEqual(purpose.code, "ex_benefits")
        self.assertFalse(purpose.requires_consent,
                         "administrer un avantage est l'exécution du lien d'emploi")
        calendar = self.env.ref("bf_employee_experience_privacy.retention_ex_benefits")
        self.assertEqual(calendar.code, "RH-EX-1")
        self.assertEqual(calendar.active_retention_years, 3)
        self.assertEqual(calendar.semi_active_retention_years, 2)
        self.assertEqual(calendar.final_disposition, "destroy")
        self.assertTrue(calendar.requires_approval)

    def test_our_models_are_classifiable_and_the_catalogue_is_not(self):
        allowed = self.env["privacy.document.classification"]._privacy_classifiable_models()
        for model in ("bf.ex.entitlement", "bf.ex.usage", "bf.ex.claim"):
            self.assertIn(model, allowed)
        self.assertNotIn("bf.ex.benefit", allowed,
                         "un avantage est une décision d'entreprise, pas un "
                         "renseignement sur quelqu'un")
        self.assertNotIn("bf.ex.eligibility.rule", allowed)

    def test_other_bridges_still_work(self):
        """⚠️ La chaîne `_execute_destruction` compte plusieurs ponts. Une ligne
        qui ne nous concerne pas doit passer au suivant, pas mourir ici."""
        line = self.env["privacy.destruction.campaign.line"].new({
            "res_model": "res.partner", "res_id": 0,
        })
        # res_id nul : la méthode générique sort tôt sans rien faire.
        self.assertIsNone(line._execute_destruction())

    def test_no_active_field_on_our_models(self):
        """🔴 Le garde qui vieillit bien.

        La méthode générique de `privacy_consent` ARCHIVE au lieu de détruire
        dès que le modèle porte un champ `active`, tout en certifiant une
        suppression au registre immuable. Aucun de nos trois modèles n'en porte
        aujourd'hui. Le jour où quelqu'un en ajoute un, ce test tombe, et c'est
        exactement ce qu'on veut : la surcharge devra en tenir compte.
        """
        for model in ("bf.ex.entitlement", "bf.ex.usage", "bf.ex.claim"):
            self.assertNotIn("active", self.env[model]._fields, model)

    # ---------------- l'agrégat ----------------

    def test_aggregate_carries_no_names(self):
        Aggregate = self.env["bf.ex.usage.aggregate"]
        Aggregate._build_for_year(self.today.year, company=self.company)
        record = Aggregate.search([
            ("benefit_id", "=", self.benefit.id), ("year", "=", self.today.year),
        ])
        self.assertEqual(len(record), 1)
        self.assertEqual(record.people, 1)
        self.assertEqual(record.entitled_people, 2)
        self.assertAlmostEqual(record.uptake_rate, 50.0)
        self.assertAlmostEqual(record.amount, 120.0)
        # Aucun champ ne pointe vers une personne concernée.
        # `create_uid` et `write_uid` sont les champs d'audit de l'ORM : ils
        # nomment qui a CALCULÉ l'agrégat, pas qui a utilisé l'avantage. Les
        # exclure serait malhonnête s'ils portaient de la donnée de sujet ;
        # ici ils n'en portent pas, et Odoo les pose sur toute table.
        audit = {"create_uid", "write_uid"}
        for name, field in record._fields.items():
            if name in audit:
                continue
            self.assertNotIn(
                field.comodel_name or "", ("hr.employee", "res.partner", "res.users"),
                "l'agrégat ne doit citer aucune personne concernée (champ %s)" % name,
            )

    def test_aggregate_is_idempotent(self):
        Aggregate = self.env["bf.ex.usage.aggregate"]
        first = Aggregate._build_for_year(self.today.year, company=self.company)
        second = Aggregate._build_for_year(self.today.year, company=self.company)
        self.assertEqual(first, second, "un seul agrégat par avantage et par année")

    def test_aggregate_survives_the_destruction_of_its_lines(self):
        """C'est toute la raison d'être du modèle."""
        Aggregate = self.env["bf.ex.usage.aggregate"]
        Aggregate._build_for_year(self.today.year, company=self.company)
        record = Aggregate.search([("benefit_id", "=", self.benefit.id)])
        self.usage.unlink()
        record.invalidate_recordset()
        self.assertTrue(record.exists())
        self.assertEqual(record.people, 1)
        self.assertAlmostEqual(record.amount, 120.0)

    # ---------------- la destruction ----------------

    def _line(self, record, method="delete"):
        campaign = self.env["privacy.destruction.campaign"].create({
            "name": "Campagne d'essai",
        })
        return self.env["privacy.destruction.campaign.line"].create({
            "campaign_id": campaign.id,
            "res_model": record._name,
            "res_id": record.id,
            "res_name": record.display_name,
            "destruction_method": method,
            "retention_calendar_id": self.env.ref(
                "bf_employee_experience_privacy.retention_ex_benefits").id,
        })

    def test_destruction_refuses_an_unaggregated_year(self):
        """🔴 L'ordre est agréger, puis détruire. L'inverse perd la mesure."""
        line = self._line(self.usage)
        with self.assertRaises(UserError) as caught:
            line._execute_destruction()
        self.assertIn("agrégé", str(caught.exception))
        self.assertTrue(self.usage.exists(), "rien n'a été détruit")

    def test_destruction_proceeds_once_aggregated(self):
        self.env["bf.ex.usage.aggregate"]._build_for_year(
            self.today.year, company=self.company,
        )
        usage_id = self.usage.id
        line = self._line(self.usage)
        line._execute_destruction()
        self.assertFalse(self.env["bf.ex.usage"].browse(usage_id).exists())

    def test_destruction_takes_the_attachments_with_it(self):
        """⚠️ `mail.thread.unlink` emporte les messages, PAS les pièces jointes
        rattachées directement à l'enregistrement. Un reçu y survivrait."""
        self.env["bf.ex.usage.aggregate"]._build_for_year(
            self.today.year, company=self.company,
        )
        attachment = self.env["ir.attachment"].create({
            "name": "recu.pdf", "res_model": "bf.ex.usage", "res_id": self.usage.id,
            "datas": b"cmVjdQ==",
        })
        attachment_id = attachment.id
        self._line(self.usage)._execute_destruction()
        self.assertFalse(self.env["ir.attachment"].browse(attachment_id).exists())

    def test_anonymize_is_refused(self):
        line = self._line(self.usage, method="anonymize")
        with self.assertRaises(UserError):
            line._execute_destruction()
        self.assertTrue(self.usage.exists())

    def test_missing_record_raises_rather_than_certifying(self):
        """🔴 `action_execute` écrit l'entrée de registre APRÈS l'appel, sans
        regarder l'état. Le seul moyen d'empêcher une certification fausse est
        de lever."""
        line = self._line(self.usage)
        self.usage.unlink()
        with self.assertRaises(UserError):
            line._execute_destruction()

    def test_entitlement_and_claim_destroy_without_aggregate(self):
        """Le garde de l'agrégat ne vise QUE l'usage : c'est lui qui porte la
        mesure. Un droit ou une demande se détruisent sans lui."""
        right = self.env["bf.ex.entitlement"].search([
            ("employee_id", "=", self.other.id),
        ], limit=1)
        right_id = right.id
        self._line(right)._execute_destruction()
        self.assertFalse(self.env["bf.ex.entitlement"].browse(right_id).exists())

    # ---------------- qui a le droit de construire l'agrégat ----------------

    def _plain_user(self):
        return self.env["res.users"].create({
            "name": "Sans droits", "login": "sansdroits_agg_test",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })

    def test_build_all_is_refused_to_a_plain_employee(self):
        """`action_build_all` est publique, donc appelable par RPC. L'ACL ne
        donne que la lecture à `base.group_user`, mais `_build_for_year` écrit
        en `sudo()` : sans garde, un compte en lecture seule créait des lignes.
        """
        with self.assertRaises(AccessError):
            self.env["bf.ex.usage.aggregate"].with_user(
                self._plain_user()).action_build_all()

    def test_recompute_is_refused_to_a_plain_employee(self):
        """Même porte, autre poignée : `action_recompute` passe par le même
        `_build_for_year`."""
        aggregate = self.env["bf.ex.usage.aggregate"]._build_for_year(
            self.today.year, company=self.company, benefits=self.benefit)
        with self.assertRaises(AccessError):
            aggregate.with_user(self._plain_user()).action_recompute()

    def test_a_plain_employee_cannot_open_the_destruction_gate(self):
        """🔴 Ce que la garde protège vraiment.

        La campagne refuse de détruire une ligne d'usage dont l'année n'est pas
        agrégée. Si n'importe qui peut construire l'agrégat, n'importe qui peut
        satisfaire cette condition, et l'ordre « agréger d'abord, détruire
        ensuite » ne tient plus.
        """
        Aggregate = self.env["bf.ex.usage.aggregate"]
        self.assertFalse(
            Aggregate._has_coverage(self.benefit, self.today.year, self.company),
            "l'année ne doit pas encore être couverte au départ")
        with self.assertRaises(AccessError):
            Aggregate.with_user(self._plain_user()).action_build_all()
        self.assertFalse(
            Aggregate._has_coverage(self.benefit, self.today.year, self.company),
            "la tentative refusée ne doit avoir rien écrit")

    def test_hr_still_builds(self):
        hr = self.env["res.users"].create({
            "name": "RH", "login": "rh_agg_test",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("hr.group_hr_user").id])],
        })
        self.env["bf.ex.usage.aggregate"].with_user(hr).action_build_all()
        self.assertTrue(
            self.env["bf.ex.usage.aggregate"]._has_coverage(
                self.benefit, self.today.year, self.company))

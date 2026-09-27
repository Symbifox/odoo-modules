"""La langue d'une règle financière suit la personne qui la lit.

🔴 Les règles du fonds de prévoyance, de l'auto-assurance, des
intérêts, de la répartition et de l'état des charges étaient des champs calculés
STOCKÉS : la phrase se figeait dans la langue de qui avait déclenché le calcul.
Or ces phrases passent telles quelles au budget remis à l'assemblée et à l'état
des charges remis au proposant acquéreur.

Chaque essai calcule dans une langue et relit dans l'autre, dans les deux sens.
Voir `bf_property_governance/tests/test_reader_language.py` pour le patron.
"""
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged

CROSSINGS = (("en_CA", "fr_CA"), ("fr_CA", "en_CA"))


@tagged("post_install", "-at_install")
class TestReaderLanguage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)
        # Les libellés de sélection vivent en base, pas dans le code : sans ce
        # chargement, un banc installé sans l'anglais les rendrait en français
        # et l'essai accuserait le module d'un défaut du banc.
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_property_finance"], ["en_CA"], overwrite=True
        )

    def _crossed(self, build, expected):
        """Calcule sous la langue de l'écrivain, relit sous celle du lecteur."""
        for writer, reader in CROSSINGS:
            env = self.env(context=dict(self.env.context, lang=writer))
            record = build(env)
            env.flush_all()
            for fname, fragments in expected.items():
                read = record.with_context(lang=reader)
                read.invalidate_recordset([fname])
                self.assertIn(
                    fragments[reader],
                    read[fname] or "",
                    "%s.%s calculé en %s, relu en %s"
                    % (record._name, fname, writer, reader),
                )

    # ── Outillage : chaque cas monte son propre syndicat ──

    def _syndicat(self, env, **kw):
        vals = {"name": "Syndicat bilingue", "fraction_base": 1000}
        vals.update(kw)
        syndicat = env["bf.property.organisation"].create(vals)
        building = env["bf.property.building"].create(
            {"name": "Immeuble bilingue", "organisation_id": syndicat.id}
        )
        for index, quote_part in enumerate([600.0, 400.0], start=1):
            unit = env["bf.property.unit"].create(
                {"name": "F-%d" % index, "building_id": building.id,
                 "quote_part": quote_part}
            )
            owner = env["res.partner"].create(
                {"name": "Payeur bilingue %d" % index,
                 "email": "f%d@example.invalid" % index}
            )
            env["bf.property.ownership"].create(
                {"unit_id": unit.id, "partner_id": owner.id}
            )
        return syndicat

    def _budget(self, env, syndicat):
        budget = env["bf.property.budget"].create({
            "name": "Exercice bilingue",
            "organisation_id": syndicat.id,
            "date_start": fields.Date.today().replace(month=1, day=1),
            "date_end": fields.Date.today().replace(month=12, day=31),
        })
        env["bf.property.budget.line"].create({
            "budget_id": budget.id, "name": "Exploitation",
            "charge_type": "common", "amount": 1000.0,
        })
        return budget

    def _issued_call(self, env, syndicat, due_date):
        budget = self._budget(env, syndicat)
        budget.consultation_assembly_id = env["bf.property.assembly"].create({
            "name": "AG de consultation", "organisation_id": syndicat.id,
            "date": fields.Datetime.now(),
        })
        budget.action_consult()
        call = env["bf.property.fund.call"].create({
            "name": "Appel bilingue", "budget_id": budget.id,
            "period_start": budget.date_start, "period_end": budget.date_end,
            "due_date": due_date,
        })
        call.action_compute_lines()
        call.action_issue()
        return call

    # ── Le syndicat ──

    def test_the_syndicat_rules_follow_the_reader(self):
        self._crossed(lambda env: self._syndicat(env), {
            "contingency_rule": {
                "fr_CA": "Aucun plancher n'est porté",
                "en_CA": "No floor is recorded",
            },
            "contingency_fixing_rule": {
                "fr_CA": "Aucune première étude n'est portée",
                "en_CA": "No first study is recorded",
            },
            "self_insurance_rule": {
                "fr_CA": "Aucune franchise n'est renseignée",
                "en_CA": "No deductible is entered",
            },
        })

    def test_the_catchup_rule_follows_the_reader(self):
        def build(env):
            return self._syndicat(
                env, contingency_first_study_date=fields.Date.today()
            )

        self._crossed(build, {
            "contingency_catchup_rule": {
                "fr_CA": "L'étude ne révèle aucune insuffisance",
                "en_CA": "The study reveals no insufficiency",
            },
        })

    # ── Le budget ──

    def test_the_budget_texts_follow_the_reader(self):
        """⚠️ L'avertissement du budget REPREND la règle du syndicat.

        Deux calculés, l'un dans l'autre : il ne suffit pas que le second suive
        le lecteur, il faut que le premier le suive aussi.
        """
        self._crossed(lambda env: self._budget(env, self._syndicat(env)), {
            "contingency_warning": {
                "fr_CA": "Aucun plancher n'est porté",
                "en_CA": "No floor is recorded",
            },
        })
        self._crossed(
            lambda env: self._budget(env, self._syndicat(env)).line_ids, {
                "allocation_rule": {
                    "fr_CA": "Art. 1064 al. 1 C.c.Q. : en proportion",
                    "en_CA": "Art. 1064 para. 1 CCQ: in proportion",
                },
            },
        )

    def test_the_budget_report_names_its_charge_types_in_the_reader_language(self):
        """Les postes du tableau « fixé, appelé, encaissé » venaient d'une
        constante de module : « Charges communes générales » dans toutes les
        langues."""
        budget = self._budget(self.env, self._syndicat(self.env))
        rows = budget.with_context(lang="en_CA")._report_lines()
        self.assertIn("General common expenses", [row["label"] for row in rows])
        rows = budget.with_context(lang="fr_CA")._report_lines()
        self.assertIn("Charges communes générales", [row["label"] for row in rows])

    # ── L'appel de fonds et l'état des charges ──

    def test_the_interest_rule_follows_the_reader(self):
        def build(env):
            syndicat = self._syndicat(env)
            call = self._issued_call(env, syndicat, fields.Date.today())
            return call.line_ids[:1]

        self._crossed(build, {
            "interest_rule": {
                "fr_CA": "Le syndicat ne porte pas d'intérêt",
                "en_CA": "The syndicate records no interest",
            },
        })

    def test_the_statement_rule_follows_the_reader(self):
        def build(env):
            syndicat = self._syndicat(env)
            return env["bf.property.charge.statement"].create({
                "organisation_id": syndicat.id,
                "unit_id": syndicat.unit_ids[:1].id,
                "requester_partner_id": env["res.partner"].create(
                    {"name": "Proposant acquéreur"}
                ).id,
                "request_date": fields.Date.today(),
            })

        self._crossed(build, {
            "binding_rule": {
                "fr_CA": "À fournir au plus tard le",
                "en_CA": "To provide no later than",
            },
        })

    # ── L'assemblée ──

    def test_the_art1087_list_follows_the_reader(self):
        """⚠️ Les six pièces étaient une constante de module sans `_()`."""
        def build(env):
            return env["bf.property.assembly"].create({
                "name": "AGA bilingue",
                "organisation_id": self._syndicat(env).id,
                "date": fields.Datetime.now(),
                "assembly_type": "annual",
            })

        self._crossed(build, {
            "art1087_missing": {
                "fr_CA": "Le bilan",
                "en_CA": "The balance sheet",
            },
        })

    def test_the_deprivation_note_is_written_in_the_actor_language(self):
        """Art. 1094 : la note au fil est dans la langue de qui applique.

        🔴 Deux défauts dans la même phrase. Le `_()` des lignes était appelé
        dans une expression génératrice, où Odoo ne trouve pas la langue : les
        lignes restaient en français. Et le corps était passé en `str` à
        `message_post`, qui l'échappe : les balises `<p>` et `<li>` s'affichaient
        en clair au fil.
        """
        syndicat = self._syndicat(self.env)
        self._issued_call(
            self.env, syndicat, fields.Date.today() - relativedelta(months=4)
        )
        assembly = self.env["bf.property.assembly"].create({
            "name": "AG des impayés", "organisation_id": syndicat.id,
            "date": fields.Datetime.now(),
        })
        assembly.action_load_attendance()
        self.assertTrue(assembly.deprivation_candidate_count)
        assembly.with_context(lang="en_CA").action_apply_deprivation()
        note = assembly.message_ids.filtered(lambda m: "1094" in (m.body or ""))
        self.assertEqual(len(note), 1)
        body = str(note.body)
        self.assertIn("Art. 1094 CCQ applied", body)
        self.assertIn("outstanding since", body)
        self.assertIn("<li>", body)
        self.assertNotIn("&lt;", body)

"""Une taxe seule, le taux libre, les taxes dans
l'opération, le libellé des profils, et les exemples du champ de calcul."""

import re
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from odoo.tests import BaseCase, TransactionCase, new_test_user, tagged

from ..lib.expression import CalcError, evaluate
from ..models.bf_calculator_entry import TAX_WORDS

NBSP = " "
MODULE = Path(__file__).resolve().parent.parent


@tagged("post_install", "-at_install")
class TestExpressionTaxes(BaseCase):
    """L'évaluateur seul : il ne connaît aucun taux, l'appelant les injecte."""

    TAXES = {
        "taxes": [("TPS", Decimal("5")), ("TVQ", Decimal("9.975"))],
        "tax": [("TPS", Decimal("5")), ("TVQ", Decimal("9.975"))],
        "tvq": [("TVQ", Decimal("9.975"))],
        "tvh": [],  # trois taux portent ce nom : le mot ne tranche pas
    }

    def ev(self, text, **kw):
        kw.setdefault("taxes", self.TAXES)
        kw.setdefault("tax_words", TAX_WORDS)
        return evaluate(text, NBSP, **kw)

    def test_add_taxes(self):
        res = self.ev("1 000 + taxes")
        self.assertEqual(res.value, Decimal("1149.75"))
        self.assertEqual(res.display(), "1 000 + taxes")
        self.assertEqual([(n, str(r)) for n, r, _a in res.tax_steps[0]["taxes"]],
                         [("TPS", "5"), ("TVQ", "9.975")])
        self.assertEqual(res.tax_steps[0]["sign"], "+")

    def test_remove_taxes_is_the_reverse(self):
        self.assertEqual(self.ev("1 149,75 - taxes").value, Decimal("1000"))
        self.assertEqual(self.ev("1 099,75 - tvq").value, Decimal("1000"))

    def test_one_tax_only(self):
        self.assertEqual(self.ev("1 000 + tvq").value, Decimal("1099.75"))

    def test_taxes_after_a_calculation(self):
        # Le mot porte sur ce qui précède, parenthèses et produits compris.
        self.assertEqual(self.ev("(400 + 600) + taxes").value, Decimal("1149.75"))
        self.assertEqual(self.ev("2 * 500 + tvq").value, Decimal("1099.75"))

    def test_ambiguous_word_is_refused_by_name(self):
        with self.assertRaises(CalcError) as caught:
            self.ev("1 000 + tvh")
        self.assertEqual(caught.exception.code, "ambiguous_tax")
        self.assertEqual(caught.exception.params["text"], "tvh")

    def test_tax_needs_an_amount_and_the_right_operator(self):
        for text, code in (("+ taxes", "tax_needs_amount"),
                           ("1 000 * taxes", "tax_bad_operator"),
                           ("1 000 / tvq", "tax_bad_operator")):
            with self.assertRaises(CalcError) as caught:
                self.ev(text)
            self.assertEqual(caught.exception.code, code, text)

    def test_a_tax_word_cannot_name_a_variable(self):
        with self.assertRaises(CalcError) as caught:
            self.ev("tvq = 9,975")
        self.assertEqual(caught.exception.code, "bad_variable")

    def test_the_frozen_words_alone_hold_both_guards(self):
        """🔴 Ces deux gardes ne doivent RIEN devoir à ``taxes``.
        Les taux ne sont injectés que si l'expression porte un mot de taxe
        précédé de + ou - ; une affectation n'en porte jamais, et
        « 1 000 * tvq » non plus. Mesuré sans aucun taux : seule la liste
        figée est en jeu."""
        for text, code in (("tvq = 9,975", "bad_variable"),
                           ("taxes = 10", "bad_variable"),
                           ("1 000 * tvq", "tax_bad_operator"),
                           ("1 000 / taxes", "tax_bad_operator")):
            with self.assertRaises(CalcError) as caught:
                evaluate(text, NBSP, tax_words=TAX_WORDS)
            self.assertEqual(caught.exception.code, code, text)
        # Et le témoin de la mesure : sans la liste, les deux gardes dorment.
        self.assertEqual(evaluate("tvq = 9,975", NBSP).value, Decimal("9.975"))
        with self.assertRaises(CalcError) as caught:
            evaluate("1 000 * tvq", NBSP)
        self.assertEqual(caught.exception.code, "unknown_variable")

    def test_a_tax_word_elsewhere_stays_a_label(self):
        # « 6 taxes » : un descriptif, comme « 6 semaines ».
        res = self.ev("6 taxes * 2")
        self.assertEqual(res.value, Decimal(12))
        self.assertEqual(res.tax_steps, [])

    def test_without_taxes_the_word_is_unknown(self):
        # Aucun mot injecté : « taxes » redevient un nom de variable inconnu.
        with self.assertRaises(CalcError) as caught:
            evaluate("1 000 + taxes", NBSP)
        self.assertEqual(caught.exception.code, "unknown_variable")

    def test_injected_arithmetic_wins(self):
        seen = []

        def tax_op(value, components, sign):
            seen.append((value, sign))
            return Decimal("42"), [("TPS", Decimal("5"), Decimal("2"))], False

        res = self.ev("1 000 + taxes", tax_op=tax_op)
        self.assertEqual(res.value, Decimal("42"))
        self.assertEqual(seen, [(Decimal("1000"), "+")])
        self.assertFalse(res.tax_steps[0]["exact"])


@tagged("post_install", "-at_install")
class TestTaxProfiles(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        # L'anglais canadien aussi : plusieurs règles ne se mesurent
        # que dans les DEUX langues (la virgule y est le séparateur de
        # milliers, et c'est elle qui faussait la lecture d'un taux libre).
        cls.env["res.lang"]._activate_lang("en_CA")
        cls.alice = new_test_user(
            cls.env, "calc3_alice", groups="base.group_user", lang="fr_CA")
        cls.Entry = cls.env["bf.calculator.entry"]
        if "account.tax" in cls.env:
            cls.env["account.tax"].search([]).write({"active": False})

    def as_alice(self):
        return self.Entry.with_user(self.alice).with_context(lang="fr_CA")

    def _taxes(self):
        """Le couple québécois en groupe, une TVH seule, et trois TVH pour voir
        un mot ambigu. Les enfants sont en « none », comme dans un vrai plan."""
        Tax = self.env["account.tax"]
        Group = self.env["account.tax.group"]
        company = self.env.company
        g_tps = Group.create({"name": "TPS", "company_id": company.id})
        g_tvq = Group.create({"name": "TVQ", "company_id": company.id})
        g_tvh = Group.create({"name": "TVH", "company_id": company.id})
        tps = Tax.create({"name": "5% TPS", "amount": 5, "amount_type": "percent",
                          "type_tax_use": "none", "tax_group_id": g_tps.id,
                          "company_id": company.id})
        tvq = Tax.create({"name": "9.975% TVQ", "amount": 9.975, "amount_type": "percent",
                          "type_tax_use": "none", "tax_group_id": g_tvq.id,
                          "company_id": company.id})
        group = Tax.create({"name": "14.975% TPS+TVQ", "amount_type": "group",
                            "type_tax_use": "sale", "company_id": company.id,
                            "children_tax_ids": [(6, 0, [tps.id, tvq.id])]})
        tvh = [Tax.create({"name": f"{rate}% TVH", "amount": rate, "amount_type": "percent",
                           "type_tax_use": "sale", "tax_group_id": g_tvh.id,
                           "company_id": company.id}) for rate in (13, 14, 15)]
        sejour = Tax.create({"name": "Taxe de séjour", "amount": 3.5,
                             "amount_type": "percent", "type_tax_use": "sale",
                             "company_id": company.id})
        return group, tps, tvq, tvh, sejour

    # ------------------------------------------------------------------
    # 1. Une taxe de groupe est aussi offerte seule
    # ------------------------------------------------------------------
    def test_group_components_are_offered_alone(self):
        if "account.tax" not in self.env:
            self.skipTest("account absent")
        group, tps, tvq, _tvh, _sejour = self._taxes()
        keys = [p["key"] for p in self.as_alice().calc_tax_profiles()]
        self.assertEqual(keys[0], f"tax:{group.id}", "le groupe québécois reste en tête")
        # Les deux composants du couple suivent le groupe : la TPS extraite du
        # couple est québécoise elle aussi, même si son nom ne dit pas « TVQ ».
        self.assertEqual(set(keys[1:3]), {f"tax:{tvq.id}", f"tax:{tps.id}"})
        self.assertEqual(keys[-1], "manual")

    def test_a_single_component_computes_alone(self):
        if "account.tax" not in self.env:
            self.skipTest("account absent")
        _group, _tps, tvq, _tvh, _sejour = self._taxes()
        res = self.as_alice().calc_tax("1 099,75", f"tax:{tvq.id}", "reverse")
        self.assertTrue(res["ok"])
        self.assertEqual(res["base"], f"1{NBSP}000,00{NBSP}$")
        self.assertEqual([t["amount"] for t in res["taxes"]], [f"99,75{NBSP}$"])

    def test_component_shared_by_two_groups_appears_once(self):
        if "account.tax" not in self.env:
            self.skipTest("account absent")
        group, tps, tvq, _tvh, _sejour = self._taxes()
        Tax = self.env["account.tax"]
        tvp = Tax.create({"name": "6% TVP SK", "amount": 6, "amount_type": "percent",
                          "type_tax_use": "none", "company_id": self.env.company.id})
        # Créé APRÈS le couple québécois, exprès : la TPS appartient aux deux
        # groupes, et le second n'est pas québécois.
        Tax.create({"name": "11% TPS+TVP SK", "amount_type": "group",
                    "type_tax_use": "sale", "company_id": self.env.company.id,
                    "children_tax_ids": [(6, 0, [tps.id, tvp.id])]})
        keys = [p["key"] for p in self.as_alice().calc_tax_profiles()]
        self.assertEqual(keys.count(f"tax:{tps.id}"), 1)
        self.assertIn(f"tax:{tvp.id}", keys)
        self.assertIn(f"tax:{group.id}", keys)
        # 🔴 La TPS reste rangée avec le Québec : un composant est québécois dès
        # qu'UN de ses groupes l'est, et pas selon le dernier groupe parcouru.
        self.assertEqual(keys[0], f"tax:{group.id}")
        self.assertEqual(set(keys[1:3]), {f"tax:{tps.id}", f"tax:{tvq.id}"},
                         "la TPS du couple TPS+TVQ a quitté le bloc québécois")

    # ------------------------------------------------------------------
    # 2. Le libellé porte le taux quand le nom ne le porte pas
    # ------------------------------------------------------------------
    def test_label_adds_the_rate_only_when_missing(self):
        if "account.tax" not in self.env:
            self.skipTest("account absent")
        _group, _tps, _tvq, _tvh, sejour = self._taxes()
        profiles = {p["key"]: p for p in self.as_alice().calc_tax_profiles()}
        self.assertEqual(profiles[f"tax:{sejour.id}"]["label"],
                         f"Taxe de séjour (3,5{NBSP}%)")
        for key, profile in profiles.items():
            if key.startswith("tax:") and "%" in profile["name"]:
                self.assertEqual(profile["label"], profile["name"],
                                 "un nom qui porte déjà son taux ne se répète pas")

    def test_label_compares_numbers_not_substrings(self):
        """🔴 La détection « le nom porte
        déjà son taux » lisait des SOUS-CHAÎNES. « 5 » est dans « Eco-frais
        2015 », et « 100 ».rstrip("0") vaut « 1 », donc « Taxe 1 » passait pour
        un nom qui porte son taux de 100 %. On compare des nombres."""
        entry = self.as_alice()
        for nom, total, attendu in (
            ("Eco-frais 2015", "5", f"Eco-frais 2015 (5{NBSP}%)"),
            ("Taxe 1", "100", f"Taxe 1 (100{NBSP}%)"),
            ("14.975% TPS+TVQ", "14.975", "14.975% TPS+TVQ"),
            ("15%", "15", "15%"),
            ("TVQ", "9.975", f"TVQ (9,975{NBSP}%)"),
            # Et l'égalité est décimale, pas textuelle : « 5.00 » vaut 5.
            ("5.00% TPS", "5", "5.00% TPS"),
        ):
            self.assertEqual(entry._tax_label(nom, Decimal(total)), attendu, nom)

    # ------------------------------------------------------------------
    # 3. Le taux libre
    # ------------------------------------------------------------------
    def test_manual_rate(self):
        res = self.as_alice().calc_tax("114,98", "manual", "reverse", rates_text="5 + 9,975")
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["base"], f"100,00{NBSP}$")
        self.assertEqual([t["amount"] for t in res["taxes"]], [f"5,00{NBSP}$", f"9,98{NBSP}$"])
        self.assertIn("9,975", res["profile"])

    def test_manual_rate_single(self):
        res = self.as_alice().calc_tax("100", "manual", "forward", rates_text="9,975 %")
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["total"], f"109,98{NBSP}$")
        # Un seul taux : la ligne s'appelle « Taxe », sans numéro (quelle que
        # soit la langue du catalogue).
        self.assertNotRegex(res["taxes"][0]["name"], r"\d")

    def test_manual_rates_are_numbered_when_several(self):
        res = self.as_alice().calc_tax("100", "manual", "forward", rates_text="5 + 9,975")
        noms = [t["name"] for t in res["taxes"]]
        self.assertEqual(len(set(noms)), 2, noms)
        self.assertRegex(noms[1], r"2")

    def test_manual_rate_refusals(self):
        for rates in ("", "0", "-5", "120", "abc", "1+2+3+4+5"):
            res = self.as_alice().calc_tax("100", "manual", "forward", rates_text=rates)
            self.assertFalse(res["ok"], f"taux accepté à tort : {rates!r}")
            self.assertTrue(res["error"])

    def test_each_manual_rate_part_is_a_single_number(self):
        """🔴 ``re.split(r"[+;/]", ...)`` ne connaît pas la
        virgule, donc « 5, 9.975 » n'était pas coupé et ``parse_number`` lisait
        « 5,9.975 » comme 59,975 %, sans refus. La virgule ne DOIT pas devenir
        un séparateur (en français c'est la décimale, et « 9,975 » est un seul
        taux) : chaque morceau est validé, et « 1e-40 », qui passait la borne
        ]0, 100] pour s'afficher « 0 % », tombe par le même chemin."""
        for rates in ("5, 9.975", "5,9.975", "1e-40", "1e9", "5 9,975",
                      "5;9.975 9", "9,975 $", "--5", "5 et 9",
                      # 🔴 Le découpage
                      # acceptait « ; » et « / », que ni l'exemple, ni le
                      # message de refus, ni la fiche ne nomment. « 15/2 »
                      # passait pour 15 % + 2 %.
                      "15/2", "5;9.975", "9,975/2"):
            res = self.as_alice().calc_tax("100", "manual", "forward", rates_text=rates)
            self.assertFalse(res["ok"], f"taux accepté à tort : {rates!r}")
            morceau = [m.strip() for m in rates.split("+") if m.strip()][-1]
            self.assertIn(morceau, res["error"], rates)
            self.assertIn("+", res["error"], f"le refus ne dit pas comment séparer : {rates!r}")

    def test_the_manual_rates_that_must_pass(self):
        """L'autre bord : une validation trop serrée refuserait les formes
        voulues. 9,975 reste UN taux, et il vaut bien 9,975 %.

        🔴 Le taux était relu avec
        le séparateur de MILLIERS de la langue. En session anglaise, la virgule
        groupe les milliers, donc « 9,975 » (le taux officiel du Québec, écrit
        avec la décimale du Québec) était REFUSÉ et « 0,075 » était accepté à
        75 %, sans un mot : 100,00 $ devenaient 175,00 $. L'essai d'avant ne
        tournait qu'en fr_CA et certifiait pourtant la règle générale ; celui-ci
        mesure les DEUX langues, et il lit ``raw``, qui ne porte pas de format.
        """
        for lang in ("fr_CA", "en_CA"):
            entry = self.Entry.with_user(self.alice).with_context(lang=lang)
            for rates, brut in (("9,975", "109.98"), ("9.975", "109.98"),
                                ("9,975 %", "109.98"), (" 9,975% ", "109.98"),
                                ("5 + 9,975", "114.98"), ("5+9.975", "114.98"),
                                # Le cas qui dit tout : 0,075 % est 0,075 %, pas 75 %.
                                ("0,075", "100.08"), ("0.075", "100.08"),
                                ("1,250", "101.25"), ("100", "200"),
                                ("2 + 3 + 4 + 5", "114")):
                res = entry.calc_tax("100", "manual", "forward", rates_text=rates)
                self.assertTrue(res["ok"], f"[{lang}] {rates!r} refusé à tort : "
                                           f"{res.get('error')}")
                self.assertEqual(res["raw"], brut, f"[{lang}] {rates!r}")

    def test_the_manual_rates_are_refused_in_both_languages_too(self):
        """Le même bord, dans les deux langues : une lecture indépendante de la
        langue ne doit pas non plus OUVRIR ce qui était fermé."""
        for lang in ("fr_CA", "en_CA"):
            entry = self.Entry.with_user(self.alice).with_context(lang=lang)
            for rates in ("", "0", "0,0", "-5", "120", "100,001", "abc",
                          "1+2+3+4+5", "5, 9.975", "1e-40", "15/2", "9 975"):
                res = entry.calc_tax("100", "manual", "forward", rates_text=rates)
                self.assertFalse(res["ok"],
                                 f"[{lang}] taux accepté à tort : {rates!r} "
                                 f"→ {res.get('raw')}")
                self.assertTrue(res["error"], rates)

    def test_manual_rate_is_recorded_with_its_rates(self):
        res = self.as_alice().calc_tax("100", "manual", "forward", record=True,
                                       rates_text="3,5")
        entry = self.Entry.browse(res["entry"]["id"])
        self.assertEqual(entry.mode, "tax")
        self.assertIn("3,5", entry.display_text)
        self.assertIn(f"103,50{NBSP}$", entry.display_text)

    # ------------------------------------------------------------------
    # 4. Les taxes dans l'opération
    # ------------------------------------------------------------------
    def test_expression_adds_the_chosen_profile(self):
        if "account.tax" not in self.env:
            self.skipTest("account absent")
        self._taxes()
        res = self.as_alice().calc_evaluate("1 000 + taxes")
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["result_text"], f"1{NBSP}149,75")
        self.assertIn("TPS", res["note"])
        self.assertIn(f"99,75{NBSP}$", res["note"])

    def test_expression_follows_the_profile_passed(self):
        if "account.tax" not in self.env:
            self.skipTest("account absent")
        _group, _tps, _tvq, tvh, _sejour = self._taxes()
        res = self.as_alice().calc_evaluate("1 000 + taxes", tax_profile=f"tax:{tvh[0].id}")
        self.assertEqual(res["result_text"], f"1{NBSP}130")
        # Et un mot nommé marche même si le profil choisi est une autre taxe.
        res = self.as_alice().calc_evaluate("1 000 + tvq", tax_profile=f"tax:{tvh[0].id}")
        self.assertEqual(res["result_text"], f"1{NBSP}099,75")

    def test_expression_and_tax_tab_agree_to_the_cent(self):
        if "account.tax" not in self.env:
            self.skipTest("account absent")
        group, *_rest = self._taxes()
        # 100,10 $ : aucun montant avant taxes ne redonne ce total au cent près.
        tab = self.as_alice().calc_tax("100,10", f"tax:{group.id}", "reverse")
        expression = self.as_alice().calc_evaluate("100,10 - taxes")
        self.assertEqual(expression["result_text"].replace(NBSP, " "),
                         tab["base"].replace(f"{NBSP}$", "").replace(NBSP, " "))
        self.assertTrue(tab["inexact"])
        # Le détail des taxes suit le calcul, et la note dit que le total ne
        # retombe pas juste (texte selon la langue : on mesure le détail).
        self.assertIn("TVQ", expression["note"])
        self.assertIn(f"8,68{NBSP}$", expression["note"])

    def test_expression_is_recorded_with_its_note(self):
        if "account.tax" not in self.env:
            self.skipTest("account absent")
        self._taxes()
        res = self.as_alice().calc_record("Devis : 1 000 + taxes")
        entry = self.Entry.browse(res["entry"]["id"])
        self.assertEqual(entry.title, "Devis")
        self.assertEqual(entry.display_text, "1 000 + taxes")
        self.assertEqual(entry.result_text, f"1{NBSP}149,75")
        self.assertIn("TVQ", entry.note)
        self.assertIn("TVQ", entry._chatter_line())

    def test_ambiguous_tax_word_names_itself(self):
        if "account.tax" not in self.env:
            self.skipTest("account absent")
        self._taxes()
        res = self.as_alice().calc_evaluate("1 000 + tvh")
        self.assertFalse(res["ok"])
        self.assertIn("tvh", res["error"])

    def test_english_name_of_the_same_tax(self):
        if "account.tax" not in self.env:
            self.skipTest("account absent")
        self._taxes()
        # La base nomme ses taxes en français ; « qst » doit valoir « tvq ».
        res = self.as_alice().calc_evaluate("1 000 + qst")
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["result_text"], f"1{NBSP}099,75")

    def test_no_tax_word_no_tax_read(self):
        """Témoin : la comptabilité n'est lue QUE si un mot de taxe paraît.
        Sans ce garde, chaque frappe de l'aperçu chercherait dans account.tax."""
        if "account.tax" not in self.env:
            self.skipTest("account absent")
        self._taxes()

        def refuse(self):
            raise AssertionError("account.tax lu sans mot de taxe")

        with patch.object(type(self.Entry), "_tax_profiles", refuse):
            res = self.as_alice().calc_evaluate("1 000 + 10 % * 2")
            self.assertTrue(res["ok"], res.get("error"))
            # Le témoin prouve qu'il mesure : avec le mot, il doit lever.
            with self.assertRaises(AssertionError):
                self.as_alice().calc_evaluate("1 000 + taxes")

    def test_manual_rate_applies_in_the_operation_too(self):
        """🔴 Le taux libre s'applique AUSSI dans l'opération.
        Mesuré : onglet Taxes, taux libre 20 %, 1 000
        → 1 200,00 $ ; onglet Calculer, le même profil choisi, « 1 000 + taxes »
        → 1 149,75 (le premier profil comptable, en silence). Deux réponses
        dans le même panneau. Cet essai mesure l'ACCORD des deux chemins."""
        if "account.tax" not in self.env:
            self.skipTest("account absent")
        self._taxes()
        onglet = self.as_alice().calc_tax("1 000", "manual", "forward", rates_text="20")
        self.assertEqual(onglet["total"], f"1{NBSP}200,00{NBSP}$")
        res = self.as_alice().calc_evaluate("1 000 + taxes", tax_profile="manual",
                                            rates_text="20")
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["result_text"], f"1{NBSP}200")
        # Le sens inverse aussi, et jusqu'à l'historique.
        res = self.as_alice().calc_record("1 200 - taxes", tax_profile="manual",
                                           rates_text="20")
        self.assertTrue(res["ok"], res.get("error"))
        entry = self.Entry.browse(res["entry"]["id"])
        self.assertEqual(entry.result_text, f"1{NBSP}000")
        self.assertIn(f"200,00{NBSP}$", entry.note)

    def test_unusable_manual_rates_refuse_the_operation(self):
        """Et jamais en changeant de profil : le refus est celui de l'onglet
        Taxes, mot pour mot. Un repli muet donnerait un résultat juste pour un
        autre profil que celui choisi, ce qui est précisément le défaut gardé ici."""
        if "account.tax" not in self.env:
            self.skipTest("account absent")
        self._taxes()
        for rates in ("", "5, 9.975", "1e-40", "abc", "0", "120"):
            onglet = self.as_alice().calc_tax("1 000", "manual", "forward",
                                               rates_text=rates)
            res = self.as_alice().calc_evaluate("1 000 + taxes", tax_profile="manual",
                                                rates_text=rates)
            self.assertFalse(res["ok"], f"taux accepté à tort : {rates!r}")
            self.assertEqual(res["error"], onglet["error"], rates)

    def test_a_named_tax_still_works_on_the_manual_profile(self):
        """Le taux libre ne ferme pas les mots nommés : « + tvq » reste la TVQ
        de la comptabilité, même quand le profil choisi est le taux libre."""
        if "account.tax" not in self.env:
            self.skipTest("account absent")
        self._taxes()
        res = self.as_alice().calc_evaluate("1 000 + tvq", tax_profile="manual",
                                            rates_text="20")
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["result_text"], f"1{NBSP}099,75")

    def test_a_variable_already_named_like_a_tax_loses_that_meaning(self):
        """🔴 ``TAX_WORDS`` ne
        ferme que l'affectation NEUVE. Une ``bf.calculator.variable`` nommée
        « tvq » DÉJÀ en base gardait les deux sens : « 1 000 * tvq » valait
        9 975 (la variable) et « 1 000 + tvq » valait 1 099,75 (la taxe), dans
        la même session. L'évaluateur ne reçoit plus une telle variable."""
        if "account.tax" not in self.env:
            self.skipTest("account absent")
        self._taxes()
        self.env["bf.calculator.variable"].create({
            "user_id": self.alice.id, "name": "tvq",
            "value": 9.975, "value_exact": "9.975"})
        entry = self.as_alice()
        # Le mauvais opérateur se nomme, au lieu de rendre la variable.
        res = entry.calc_evaluate("1 000 * tvq")
        self.assertFalse(res["ok"], res)
        self.assertIn("tvq", res["error"])
        self.assertIn("+", res["error"])
        # Et le mot garde son seul sens : la taxe.
        self.assertEqual(entry.calc_evaluate("1 000 + tvq")["result_text"],
                         f"1{NBSP}099,75")
        # La pastille reste visible dans le panneau : la personne peut la
        # supprimer, le module ne touche pas à sa variable.
        self.assertIn("tvq", [v["name"] for v in entry.calc_variables()])
        # Un nom anglais réservé compte autant (« qst »), et une variable
        # ordinaire, elle, continue de servir.
        self.env["bf.calculator.variable"].create({
            "user_id": self.alice.id, "name": "taux",
            "value": 125, "value_exact": "125"})
        self.assertEqual(entry.calc_evaluate("2 * taux")["result_text"], "250")

    def test_an_unknown_profile_key_refuses_instead_of_answering_for_another(self):
        """🔴 Une clé de profil
        inconnue retombait en SILENCE sur le premier profil. Mesuré :
        ``tax:999999`` rendait 1 149,75, et l'onglet Taxes annonçait
        « 14.975% TPS+TVQ » pour un profil qu'on ne lui avait pas demandé.
        Une clé vide, elle, garde le repli : c'est la première ouverture."""
        if "account.tax" not in self.env:
            self.skipTest("account absent")
        self._taxes()
        entry = self.as_alice()
        refus = [entry.calc_evaluate("1 000 + taxes", tax_profile="tax:999999"),
                 entry.calc_record("1 000 + taxes", tax_profile="tax:999999"),
                 entry.calc_tax("1 000", "tax:999999", "forward")]
        for res in refus:
            self.assertFalse(res["ok"], res)
            # Le message dit OÙ choisir ; « Taxes » est le nom de l'onglet dans
            # les deux langues.
            self.assertIn("Taxes", res["error"])
        self.assertEqual(len({r["error"] for r in refus}), 1,
                         "les deux chemins ne refusent pas du même mot")
        # Une clé vide ou absente : le repli sur le premier profil tient.
        self.assertEqual(entry.calc_evaluate("1 000 + taxes")["result_text"],
                         f"1{NBSP}149,75")
        self.assertEqual(entry.calc_evaluate("1 000 + taxes", tax_profile="")
                         ["result_text"], f"1{NBSP}149,75")
        self.assertTrue(entry.calc_tax("1 000", False, "forward")["ok"])
        # Et un calcul ORDINAIRE ne lit aucun profil : rien ne le refuse, même
        # avec une clé périmée retenue d'une société précédente.
        self.assertTrue(entry.calc_evaluate("2 + 2", tax_profile="tax:999999")["ok"])

    def test_the_quebec_fallback_is_always_there(self):
        """🔴 La branche « aucun profil » de ``_tax_keywords``
        rendait ``{}`` et ne pouvait PAS se déclencher, ``_tax_profiles``
        rendant toujours au moins le repli du Québec. La branche est retirée ;
        cet essai garde l'invariant qui la remplace (la classe désactive toutes
        les taxes de vente, et aucune n'est semée ici)."""
        entry = self.as_alice()
        profils = entry.calc_tax_profiles()
        self.assertEqual(profils[0]["key"], "default_qc", [p["key"] for p in profils])
        self.assertEqual(entry.calc_evaluate("1 000 + taxes")["result_text"],
                         f"1{NBSP}149,75")
        self.assertTrue(entry.calc_tax("1 149,75", "default_qc", "reverse")["ok"])

    def test_a_tax_word_cannot_name_a_variable_through_the_model(self):
        """🔴 Par le VRAI chemin : le modèle n'injecte ``taxes`` que
        si l'expression porte un mot de taxe précédé de + ou -. Une affectation
        n'en porte jamais, donc « tvq = 9,975 » était acceptée, et ensuite
        « 1 000 * tvq » valait 9 975 (la variable) alors que « 1 000 + tvq »
        valait 1 099,75 (la taxe). La liste figée ferme les deux."""
        if "account.tax" not in self.env:
            self.skipTest("account absent")
        self._taxes()
        res = self.as_alice().calc_evaluate("tvq = 9,975")
        self.assertFalse(res["ok"], res)
        self.assertIn("tvq", res["error"])
        res = self.as_alice().calc_record("tvq = 9,975")
        self.assertFalse(res["ok"], res)
        self.assertFalse(
            self.env["bf.calculator.variable"].sudo().search([("name", "=", "tvq")]),
            "la variable a été rangée malgré le refus")
        # Et le mauvais opérateur se nomme, lui aussi, par le vrai chemin.
        res = self.as_alice().calc_evaluate("1 000 * tvq")
        self.assertFalse(res["ok"], res)
        self.assertIn("tvq", res["error"])
        self.assertIn("+", res["error"])

    def test_a_tax_word_in_head_position_is_named_too(self):
        """🔴 En TÊTE d'expression, le mot de taxe sortait par deux chemins qui
        mentaient chacun à leur façon. « tvq * 2 » rendait « Il manque un
        nombre », qui nomme le mauvais coupable : le nombre ne manque pas, le
        mot n'est pas à sa place. Et « tvq + 100 » rendait **100**, EN SILENCE,
        le mot passant pour un descriptif comme « semaines » : le même nom,
        deux sens, celui qui réussit étant le plus dangereux des deux.

        Le repère qui nommait le mot cherchait un mot PRÉCÉDÉ d'un opérateur ;
        rien ne précède le premier jeton, donc il ne voyait jamais la tête."""
        if "account.tax" not in self.env:
            self.skipTest("account absent")
        self._taxes()
        entry = self.as_alice()
        for texte in ("tvq * 2", "tvq", "tps / 2", "TVQ * 2", "tvq.", "taxes * 2",
                      "taxes", "tvq + 100", "tvq - 100"):
            res = entry.calc_evaluate(texte)
            self.assertFalse(res["ok"], f"« {texte} » passe encore : {res}")
            self.assertIn("+", res["error"],
                          f"« {texte} » ne dit pas avec quel opérateur : {res['error']}")
            self.assertNotIn("100", res.get("result_text", ""),
                             f"« {texte} » a quand même calculé : {res}")

    def test_an_ordinary_word_in_head_position_stays_a_label(self):
        """⚠️ La garde ne vise QUE les mots de taxe, et cet essai arme le piège.
        L'aperçu du panneau tourne à chaque caractère tapé : nommer tout mot nu
        en tête ferait dire « variable inconnue : p » dès la première frappe.
        Un descriptif en tête doit donc rester un descriptif, et un mot inconnu
        garder le message d'avant, qui ne le nomme pas."""
        if "account.tax" not in self.env:
            self.skipTest("account absent")
        self._taxes()
        entry = self.as_alice()
        res = entry.calc_evaluate("pommes * 2")
        self.assertFalse(res["ok"], res)
        self.assertNotIn("pommes", res["error"],
                         "un mot quelconque en tête est nommé : l'aperçu devient bavard")
        # Le descriptif en tête, lui, calcule toujours.
        for texte in ("semaines 6 * 750", "6 semaines * 750"):
            res = entry.calc_evaluate(texte)
            self.assertTrue(res["ok"], f"« {texte} » ne calcule plus : {res}")
            self.assertEqual(res["value"], 4500.0, res)
        # Entre parenthèses, le mot appartient au nombre d'à côté : pas une tête.
        res = entry.calc_evaluate("(tvq) * 2")
        self.assertFalse(res["ok"], res)
        self.assertNotIn("+", res["error"], res["error"])
        # 🔴 Et le mot de taxe qui ÉTIQUETTE une vraie taxe reste un descriptif :
        # « TVQ 1 149,75 - taxes » vaut 1 000 et marchait avant la garde. La
        # refuser aurait cassé un usage pour en corriger un autre.
        for texte, attendu in (("TVQ 1 149,75 - taxes", 1000.0),
                               ("Taxes 1 000 + taxes", 1149.75)):
            res = entry.calc_evaluate(texte)
            self.assertTrue(res["ok"], f"« {texte} » est refusé à tort : {res}")
            self.assertEqual(res["value"], attendu, res)
        # Le titre écrit à la façon documentée ne dépend d'aucune de ces règles.
        res = entry.calc_evaluate("tvq : 1 000 * 2")
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["title"], res["value"]), ("tvq", 2000.0), res)
        # Et les chemins qui marchaient marchent encore.
        self.assertEqual(entry.calc_evaluate("1 000 + tvq")["value"], 1099.75)


def _luminance(hexa):
    canaux = [int(hexa[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lineaire = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canaux]
    return 0.2126 * lineaire[0] + 0.7152 * lineaire[1] + 0.0722 * lineaire[2]


def _contraste(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@tagged("post_install", "-at_install")
class TestPlaceholderContrast(BaseCase):
    """Le texte d'exemple est atténué jusqu'à AA, pas plus bas.
    Les deux fonds sont mesurés : clair = bg-body-tertiary ($gray-100 d'Odoo 18),
    sombre = l'écran du panneau sous bf_dark_mode."""

    CLAIR = "#f8f9fa"
    SOMBRE = "#3d4344"
    PANNEAU_SOMBRE = "#2e3132"

    def _couleurs(self):
        scss = (MODULE / "static/src/calculator.scss").read_text()
        clair = re.search(r"&::placeholder \{ color: (#[0-9a-f]{6});", scss)
        sombre = re.search(r"o_bf_calc_expression::placeholder \{\s*(?://[^\n]*\n\s*)*"
                           r"color: (#[0-9a-f]{6});", scss)
        self.assertTrue(clair and sombre, "les couleurs d'exemple ont changé de forme")
        return clair.group(1), sombre.group(1)

    def test_example_text_holds_aa_on_both_themes(self):
        clair, sombre = self._couleurs()
        self.assertGreaterEqual(round(_contraste(clair, self.CLAIR), 2), 4.5, clair)
        self.assertGreaterEqual(round(_contraste(sombre, self.SOMBRE), 2), 4.5, sombre)
        self.assertGreaterEqual(round(_contraste(sombre, self.PANNEAU_SOMBRE), 2), 4.5, sombre)

    def test_the_module_does_not_repaint_the_brand_button(self):
        """⚠️ La touche « = » est blanche sur le bleu de marque (2,62:1), et
        c'est l'arbitrage du propriétaire de la marque, écrit dans
        `bluefox_branding` : « NE PAS CORRIGER ». Le module ne doit donc pas
        poser son encre sur un `btn-primary` : une passe d'accessibilité qui
        « corrigerait » ça reviendrait sur sa décision, et cet essai le dit."""
        scss = (MODULE / "static/src/calculator.scss").read_text()
        self.assertNotRegex(scss, r"o_bf_calc_eq[^\n]*btn-primary[^\n]*\{[^}]*color:",
                            "le module repeint le bouton de marque")

    def test_example_text_is_actually_dimmed(self):
        """Et l'autre bord : une couleur qui ne descend pas ne répond pas à la
        demande. Les valeurs d'avant (5,78:1 et 5,25:1) sont refusées."""
        clair, sombre = self._couleurs()
        self.assertLess(_contraste(clair, self.CLAIR), 5.0, clair)
        self.assertLess(_contraste(sombre, self.SOMBRE), 5.0, sombre)


@tagged("post_install", "-at_install")
class TestInactiveLanguage(TransactionCase):
    """🔴 Mesuré : sur une base en production, en_US n'était PAS
    active. Une session dans une langue inactive faisait tomber tout calcul en
    « Expected singleton », parce que `_lang_get` rend un recordset vide."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["res.lang"]._activate_lang("en_CA")
        cls.alice = new_test_user(cls.env, "calc3_lang", groups="base.group_user")
        cls.Entry = cls.env["bf.calculator.entry"]

    def _deactivate_en_us(self):
        Lang = self.env["res.lang"].with_context(active_test=False)
        en = Lang.search([("code", "=", "en_US")], limit=1)
        if en and en.active:
            try:
                en.write({"active": False})
            except Exception:
                self.skipTest("en_US ne se désactive pas sur cette base")

    def test_calculation_survives_en_us_not_activated(self):
        """⚠️ en_US est le seul cas qui compte : `env.lang` d'Odoo 18 refuse
        toute AUTRE langue inactive avant même d'entrer dans le module
        (`api.py` : « Invalid language code »). en_US, lui, passe toujours, et
        c'est pour ça que l'enregistrement inactif de `res.lang` arrivait
        jusqu'au formatage.

        🔴 Cet essai passait
        ``"default_qc"`` à ``calc_tax``, une clé que la comptabilité de la base
        n'offre PAS. Il tombait donc sur le repli muet d'une clé inconnue, et
        mesurait le premier profil en croyant mesurer le profil
        par défaut. Il demande maintenant son profil à la liste, et il mesure
        aussi les CHIFFRES rendus, non le seul ``ok``."""
        self._deactivate_en_us()
        entry = self.Entry.with_user(self.alice).with_context(lang="en_US")
        res = entry.calc_evaluate("2 + 2")
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["raw"], "4")
        res = entry.calc_record("6 (weeks) * 750 $")
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["raw"], "4500")
        profil = entry.calc_tax_profiles()[0]
        res = entry.calc_tax("1 149,75", profil["key"], "reverse")
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["profile"], profil["name"],
                         "la taxe a répondu pour un autre profil que celui demandé")

    def test_the_fallback_prefers_a_language_of_the_same_family(self):
        """🔴 Le repli prenait la
        première langue active PAR ORDRE DE NOM (``_order = "active desc,name"``).
        Là où ce nom est anglais, personne ne l'a vu ; là où il ne l'est pas,
        une session ``en_US`` lisait et écrivait les nombres à la FRANÇAISE sans
        rien dire. Mesuré en renommant la langue anglaise : « 1,250 + 10 »
        rendait 11,25 au lieu de 1 260, et un montant sortait « $1 000,00 ».
        L'essai mesure donc le SÉPARATEUR et la VALEUR, pas un ``ok``."""
        Lang = self.env["res.lang"].with_context(active_test=False)
        anglaise = Lang.search([("code", "=", "en_CA"), ("active", "=", True)], limit=1)
        if not anglaise:
            self.skipTest("aucune autre langue anglaise active sur cette base")
        self._deactivate_en_us()
        # Le piège, armé : la première langue active par ordre de nom n'est
        # plus anglaise. Sans le correctif, le repli tombe sur le français.
        anglaise.write({"name": "Zzz " + (anglaise.name or "")})
        self.env["res.lang"].invalidate_model()
        premiere = self.env["res.lang"].search([("active", "=", True)], limit=1)
        self.assertNotEqual(premiere.code[:2], "en",
                            "le piège n'est pas armé : la première langue active "
                            "est encore anglaise, l'essai ne mesurerait rien")
        entry = self.Entry.with_user(self.alice).with_context(lang="en_US")
        self.assertEqual(entry._thousands_sep(), anglaise.thousands_sep,
                         "le repli a pris le séparateur d'une autre famille")
        self.assertNotEqual(entry._thousands_sep(), premiere.thousands_sep)
        # « 1,250 » : mille deux cent cinquante en anglais, 1,25 en français.
        res = entry.calc_evaluate("1,250 + 10")
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["raw"], "1260", "le nombre a été lu à la française")
        # Et le rendu : la FORME anglaise, qui ne dépend d'aucun taux de la
        # base (« $1,149.75 »), et non l'insécable et la virgule décimale du
        # français (« $1 149,75 »), qui sortaient avant le correctif.
        profil = entry.calc_tax_profiles()[0]
        taxe = entry.calc_tax("1 000", profil["key"], "forward")
        self.assertTrue(taxe["ok"], taxe.get("error"))
        self.assertRegex(taxe["total"], r"^\$\d{1,3}(,\d{3})*\.\d{2}$", taxe["total"])


@tagged("post_install", "-at_install")
class TestCatalogs(BaseCase):
    """🔴 L'en-tête « Language: fr_CA »
    avait disparu du `.po` (la 18.0.1.2.0 le portait). Et le `.pot` est une
    LISTE BLANCHE : une entrée du `.po` absente du `.pot` est jetée en silence
    au chargement, donc elle ne se verrait nulle part."""

    def _catalogue(self, nom):
        import polib
        return polib.pofile(str(MODULE / "i18n" / nom))

    def test_the_po_declares_its_language(self):
        entetes = self._catalogue("fr_CA.po").metadata
        self.assertEqual(entetes.get("Language"), "fr_CA")

    def test_the_two_catalogs_hold_the_same_terms(self):
        po = self._catalogue("fr_CA.po")
        pot = self._catalogue("bf_calculator.pot")
        msgids = {e.msgid for e in po}
        self.assertEqual(len(po), len(pot),
                         "le .po et le .pot ne portent pas le même compte")
        hors = sorted(msgids - {e.msgid for e in pot})
        self.assertFalse(hors, f"entrées du .po absentes du .pot (jetées) : {hors}")
        vides = sorted(e.msgid for e in po if not e.msgstr.strip())
        self.assertFalse(vides, f"entrées sans traduction : {vides}")


@tagged("post_install", "-at_install")
class TestTabIsValidated(BaseCase):
    """🔴 Un ``bf_calculator.tab`` inconnu
    en localStorage (un vieux nom d'onglet, une clé bricolée) ouvrait un
    panneau SANS CORPS, mesuré à 106 px de haut et aucun onglet actif. La
    valeur retenue est validée contre la liste des onglets.
    ⚠️ Le tirage et l'état du panneau ne se mesurent pas en Python : ces
    contrôles lisent la SOURCE, et le parcours au navigateur pose une vraie
    valeur bidon en localStorage."""

    ONGLETS = ["calc", "tax", "hours", "percent", "date", "column"]

    def _source(self):
        return (MODULE / "static/src/calculator_panel.js").read_text()

    def test_the_stored_tab_is_checked_against_the_list(self):
        source = self._source()
        liste = re.search(r"const TABS = \[(.*?)\];", source, re.S)
        self.assertTrue(liste, "la liste des onglets a changé de forme")
        self.assertEqual(re.findall(r'"(\w+)"', liste.group(1)), self.ONGLETS)
        lecture = re.search(r"function readTab\(\) \{(.*?)\n\}", source, re.S)
        self.assertTrue(lecture, "readTab a disparu")
        self.assertIn("TABS.includes(tab)", lecture.group(1),
                      "l'onglet lu n'est plus validé contre la liste")
        self.assertIn('"calc"', lecture.group(1), "aucun repli sur l'onglet Calcul")
        # Et l'état du panneau passe bien par readTab, non par readPref nu.
        self.assertRegex(source, r"tab: readTab\(\),")
        self.assertNotRegex(source, r"tab: readPref\(")

    def test_every_tab_change_is_remembered(self):
        """🔴 ``reuse()`` posait
        ``state.tab = "calc"`` SANS ``writePref``, donc le panneau rouvrait sur
        l'onglet d'avant alors qu'on venait de reprendre un calcul. Chaque
        écriture de l'onglet s'accompagne maintenant de sa mémoire, et c'est le
        COMPTE qui le garde : un chemin neuf qui oublierait d'écrire la
        préférence déséquilibre les deux."""
        source = self._source()
        poses = re.findall(r"state\.tab = ", source)
        memoires = re.findall(r"writePref\(TAB_KEY", source)
        self.assertGreaterEqual(len(poses), 2,
                                "les deux chemins qui posent l'onglet ont disparu")
        self.assertEqual(len(poses), len(memoires),
                         f"{len(poses)} écriture(s) d'onglet pour "
                         f"{len(memoires)} mémorisée(s)")
        reprise = re.search(r"\n    reuse\(entry\) \{(.*?)\n    \}", source, re.S)
        self.assertTrue(reprise, "reuse a changé de forme")
        self.assertIn("writePref(TAB_KEY", reprise.group(1),
                      "reprendre un calcul ne retient pas l'onglet Calcul")

    def test_the_tabs_shown_are_the_tabs_accepted(self):
        """Deux listes divergeraient en silence : les libellés sont tirés de
        TABS, donc ce que l'écran montre est ce que readTab accepte."""
        self.assertRegex(self._source(), r"return TABS\.map\(")


@tagged("post_install", "-at_install")
class TestExamplesAreTranslated(BaseCase):
    """Les exemples du champ de calcul sont tirés au hasard : chacun doit être
    traduit, pas seulement le premier (c'est le piège)."""

    def _examples(self):
        source = (MODULE / "static/src/calculator_panel.js").read_text()
        body = re.search(r"function examples\(\) \{(.*?)\n\}", source, re.S)
        self.assertTrue(body, "la liste des exemples a changé de forme")
        found = re.findall(r'_t\("((?:[^"\\]|\\.)*)"\)', body.group(1))
        # Dix exemples sont écrits : en dessous de huit, le tirage n'a plus de
        # variété et quelqu'un a vidé la liste sans le dire.
        self.assertGreaterEqual(len(found), 8, "trop peu d'exemples")
        return found

    def _catalog(self):
        text = (MODULE / "i18n/fr_CA.po").read_text()
        pairs = dict(re.findall(r'\nmsgid "((?:[^"\\]|\\.)*)"\nmsgstr "((?:[^"\\]|\\.)*)"', text))
        return pairs

    def test_every_example_is_translated(self):
        catalog = self._catalog()
        missing = [ex for ex in self._examples() if not catalog.get(ex)]
        self.assertFalse(missing, f"exemples sans traduction française : {missing}")

    def test_french_examples_use_the_french_decimal(self):
        catalog = self._catalog()
        for example in self._examples():
            french = catalog[example]
            self.assertNotRegex(french, r"\d\.\d",
                                f"décimale anglaise dans « {french} »")

    def test_every_example_has_a_letter(self):
        """🔴 Odoo n'extrait un terme que s'il porte au moins une lettre
        (`odoo/tools/translate.py`, `any(x.isalpha() ...)`). Un exemple tout en
        chiffres ne peut donc PAS être traduit, et servirait sa forme française
        (« 1 450 $ ») à un usager anglais, séparateur de milliers compris."""
        sans = [ex for ex in self._examples() if not any(c.isalpha() for c in ex)]
        self.assertFalse(sans, f"exemples intraduisibles (aucune lettre) : {sans}")

    def test_every_example_is_a_valid_calculation(self):
        """Un exemple qui ne se calcule pas enseignerait une faute.

        🔴 L'essai d'avant
        évaluait ``example.replace(".", ",")`` avec l'insécable, donc ni
        l'anglais avec le séparateur anglais, ni le français avec le sien : le
        chemin servi n'était mesuré dans AUCUNE des deux langues. Chaque
        exemple est maintenant lu dans sa langue, avec le séparateur de milliers
        de cette langue, et les deux lectures doivent donner le MÊME nombre."""
        catalogue = self._catalog()
        for anglais in self._examples():
            # L'anglais, avec le séparateur de milliers anglais : la virgule.
            res_en = evaluate(anglais, ",")
            self.assertTrue(res_en.value is not None, anglais)
            francais = catalogue[anglais]
            # Le français, avec l'insécable que res.lang sert en fr_CA.
            res_fr = evaluate(francais, NBSP)
            self.assertTrue(res_fr.value is not None, francais)
            self.assertEqual(res_fr.value, res_en.value,
                             f"« {francais} » ne vaut pas « {anglais} »")


@tagged("post_install", "-at_install")
class TestEveryEvaluatorCallCarriesTheTaxArgs(BaseCase):
    """🔴 L'onglet Heures appelait
    ``calc_evaluate`` et ``calc_record`` SANS ``tax_profile`` ni ``rates_text``,
    alors qu'il passe par le MÊME évaluateur. Mesuré : taux libre
    de 20 %, l'onglet Taxes et l'onglet Calculer disaient 1 200, l'onglet Heures
    disait 1 149,75 ; et avec des taux libres vides, Calculer refusait pendant
    que Heures calculait. Les deux arguments vivent maintenant dans UN getter,
    et cet essai garde CHAQUE appel, pour que la même faute ne se déplace pas.
    ⚠️ Le chiffre lui-même se mesure au navigateur (le même calcul dans
    Calculer et dans Heures doit donner le même nombre) : ici on garde la forme,
    et c'est la forme qui était la faute."""

    def _source(self):
        return (MODULE / "static/src/calculator_panel.js").read_text()

    def _appels(self, source):
        """Chaque appel ORM à l'évaluateur, avec le texte de ses options."""
        appels = []
        for m in re.finditer(r'orm\.call\(\s*MODEL,\s*"(calc_evaluate|calc_record)"',
                             source):
            reste = source[m.start():]
            appels.append((m.group(1), reste[:reste.index(");")]))
        return appels

    def test_the_getter_holds_both_arguments(self):
        getter = re.search(r"get taxArgs\(\) \{(.*?)\n    \}", self._source(), re.S)
        self.assertTrue(getter, "le getter taxArgs a changé de forme")
        corps = getter.group(1)
        for morceau in ("tax_profile", "rates_text", "state.taxProfile",
                        "state.taxRates"):
            self.assertIn(morceau, corps, morceau)

    def test_every_call_to_the_evaluator_carries_them(self):
        appels = self._appels(self._source())
        # Quatre appels : l'aperçu et l'enregistrement du champ de calcul, et
        # les deux de l'onglet Heures. Le COMPTE est gardé : en dessous, cet
        # essai ne mesurerait plus le chemin fautif.
        self.assertEqual(len(appels), 4, [nom for nom, _t in appels])
        self.assertEqual(sorted(nom for nom, _t in appels),
                         ["calc_evaluate", "calc_evaluate", "calc_record", "calc_record"])
        for nom, texte in appels:
            self.assertIn("...this.taxArgs", texte,
                          f"cet appel à {nom} oublie le profil de taxe et les "
                          f"taux libres : {texte[:120]}")

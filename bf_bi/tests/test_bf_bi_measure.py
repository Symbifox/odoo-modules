from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestBfBiMeasure(TransactionCase):
    """Mesures nommées sur des partenaires : un modèle toujours là, avec des règles."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Measure = cls.env["bf.bi.measure"]
        cls.Partner = cls.env["res.partner"]
        cls.designer = new_test_user(cls.env, "bi-mesure-concepteur", groups="base.group_user,bf_bi.group_bi_designer")
        cls.employee = new_test_user(cls.env, "bi-mesure-employe", groups="base.group_user")
        cls.portal = new_test_user(cls.env, "bi-mesure-portail", groups="base.group_portal")
        m_partner = cls.env["ir.model"]._get("res.partner")
        f = lambda name: cls.env["ir.model.fields"]._get("res.partner", name)
        cls.tag = cls.env["res.partner.category"].create({"name": "Banc BI mesure"})
        cls.a = cls.Partner.create({"name": "Banc A", "category_id": [(6, 0, [cls.tag.id])]})
        cls.b = cls.Partner.create({"name": "Banc B", "category_id": [(6, 0, [cls.tag.id])], "parent_id": cls.a.id})
        cls.c = cls.Partner.create({"name": "Banc C", "category_id": [(6, 0, [cls.tag.id])], "user_id": cls.employee.id})
        base = {"model_id": m_partner.id, "domain": "[('category_id', 'in', %d)]" % cls.tag.id}
        cls.count = cls.Measure.create({**base, "name": "Partenaires", "code": "essai_partenaires", "aggregator": "count"})
        cls.mine = cls.Measure.create({**base, "name": "Mes partenaires", "code": "essai_mes_partenaires",
                                       "aggregator": "count",
                                       "domain": "[('category_id', 'in', %d), ('user_id', '=', uid)]" % cls.tag.id})
        cls.ids_sum = cls.Measure.create({**base, "name": "Somme des id", "code": "essai_somme_id",
                                          "aggregator": "sum", "field_id": f("color").id})
        cls.env.flush_all()

    def value(self, code, user=None, leaf_domains=None):
        return self.Measure.with_user(user or self.env.user).bf_value(code, leaf_domains or {})

    # -- calcul -------------------------------------------------------------------

    def test_count_and_sum(self):
        self.assertEqual(self.value("essai_partenaires"), 3)
        (self.a | self.b | self.c).write({"color": 2})
        self.assertEqual(self.value("essai_somme_id"), 6)

    def test_uid_in_domain_follows_the_person_asking(self):
        self.assertEqual(self.value("essai_mes_partenaires", self.employee), 1)
        self.assertEqual(self.value("essai_mes_partenaires", self.designer), 0)

    def test_leaf_domain_from_browser_restricts(self):
        self.assertEqual(self.value("essai_partenaires", leaf_domains={"essai_partenaires": [("parent_id", "!=", False)]}), 1)

    def test_formula(self):
        self.Measure.create({"name": "Écart", "code": "essai_ecart", "kind": "formula",
                             "expression": "essai_partenaires - essai_mes_partenaires * 2"})
        self.assertEqual(self.value("essai_ecart", self.employee), 1)
        self.Measure.create({"name": "Part", "code": "essai_part", "kind": "formula",
                             "expression": "essai_mes_partenaires / essai_partenaires"})
        self.assertAlmostEqual(self.value("essai_part", self.employee), 1 / 3)

    def test_average_of_nothing_has_no_value(self):
        m = self.Measure.create({"name": "Moyenne vide", "code": "essai_moyenne_vide", "aggregator": "avg",
                                 "model_id": self.env["ir.model"]._get("res.partner").id,
                                 "field_id": self.env["ir.model.fields"]._get("res.partner", "color").id,
                                 "domain": "[('id', '=', 0)]"})
        self.assertIs(self.value(m.code), False)  # pas 0.0 ; False passe par XML-RPC
        self.Measure.create({"name": "f", "code": "essai_f_vide", "kind": "formula", "expression": "essai_moyenne_vide + 1"})
        with self.assertRaises(UserError):
            self.value("essai_f_vide")

    def test_division_by_zero_is_no_value_not_a_number(self):
        # Un taux sur une période vide n'a pas de valeur : ni 0, ni infini, ni erreur dans la tuile.
        self.Measure.create({"name": "Div", "code": "essai_div", "kind": "formula",
                             "expression": "essai_partenaires / essai_mes_partenaires"})
        self.assertIs(self.value("essai_div", self.designer), False)
        # Une formule qui s'en sert le dit, au lieu de calculer sur un trou.
        self.Measure.create({"name": "Div+", "code": "essai_div_plus", "kind": "formula", "expression": "essai_div + 1"})
        with self.assertRaises(UserError):
            self.value("essai_div_plus", self.designer)

    def test_a_broken_measure_does_not_lock_the_editor(self):
        base = self.Measure.create({"name": "Base", "code": "essai_base_archivee", "kind": "formula", "expression": "1"})
        self.Measure.create({"name": "Dépend", "code": "essai_depend", "kind": "formula", "expression": "essai_base_archivee + 1"})
        base.active = False
        codes = [m["code"] for m in self.Measure.with_user(self.designer).bf_list()]
        self.assertNotIn("essai_depend", codes)
        self.assertIn("essai_mes_partenaires", codes)

    def test_test_button_says_no_value(self):
        m = self.Measure.create({"name": "Div0", "code": "essai_bouton_div", "kind": "formula",
                                 "expression": "essai_partenaires / essai_mes_partenaires"})
        action = m.with_user(self.designer).action_test()
        self.assertEqual(action["tag"], "display_notification")
        self.assertNotIn("None", action["params"]["message"])

    def test_describe_lists_the_aggregate_leaves(self):
        self.Measure.create({"name": "Écart", "code": "essai_ecart2", "kind": "formula",
                             "expression": "(essai_partenaires - essai_mes_partenaires) / 1"})
        leaves = {leaf["code"] for leaf in self.Measure.bf_describe("essai_ecart2")["leaves"]}
        self.assertEqual(leaves, {"essai_partenaires", "essai_mes_partenaires"})

    # -- droits -------------------------------------------------------------------

    def test_record_rules_of_the_person_apply(self):
        # Un partenaire d'une autre société, invisible pour l'employé.
        autre = self.env["res.company"].create({"name": "Société banc mesure"})
        self.Partner.create({"name": "Banc D", "category_id": [(6, 0, [self.tag.id])], "company_id": autre.id})
        self.assertEqual(self.value("essai_partenaires"), 4)
        self.assertEqual(self.value("essai_partenaires", self.employee), 3)

    def test_model_without_access_is_refused(self):
        m = self.Measure.create({"name": "Groupes", "code": "essai_groupes", "aggregator": "count",
                                 "model_id": self.env["ir.model"]._get("ir.config_parameter").id})
        with self.assertRaises(AccessError):
            self.value(m.code, self.employee)

    def test_portal_user_gets_nothing(self):
        with self.assertRaises(AccessError):
            self.value("essai_partenaires", self.portal)

    def test_only_designers_define_measures(self):
        with self.assertRaises(AccessError):
            self.Measure.with_user(self.employee).create({"name": "x", "code": "essai_x", "kind": "formula",
                                                          "expression": "1"})
        self.assertTrue(self.Measure.with_user(self.designer).create(
            {"name": "x", "code": "essai_x", "kind": "formula", "expression": "1 + 1"}))

    # -- entrées refusées -------------------------------------------------------

    def test_formula_accepts_arithmetic_only(self):
        for mauvaise in ("__import__('os').system('true')", "essai_partenaires.__class__", "[1, 2]", "'abc'",
                         "essai_partenaires if 1 else 2", "f(1)"):
            with self.assertRaises(ValidationError, msg=mauvaise):
                self.Measure.create({"name": "m", "code": "essai_mauvaise", "kind": "formula", "expression": mauvaise})

    def test_cycles_and_unknown_codes_are_refused(self):
        a = self.Measure.create({"name": "a", "code": "essai_cycle_a", "kind": "formula", "expression": "1"})
        self.Measure.create({"name": "b", "code": "essai_cycle_b", "kind": "formula", "expression": "essai_cycle_a"})
        with self.assertRaises(ValidationError) as err:
            # En anglais : sur une base en français, le message (traduit) ne contiendrait pas le texte cherché.
            a.with_context(lang="en_US").expression = "essai_cycle_b + 1"
        # Le bon message : une boucle, pas « trop profond » (la limite l'attraperait aussi).
        self.assertIn("refers to itself", str(err.exception))
        with self.assertRaises(UserError):  # ValidationError en hérite
            self.Measure.create({"name": "c", "code": "essai_inconnu", "kind": "formula", "expression": "nexiste_pas"})

    def test_invalid_domain_is_refused(self):
        # Du code, puis du Python valide qui n'est pas un domaine, puis un champ inconnu.
        for mauvais in ("__import__('os')", "42", "[1, 2]", "[('name', 'LIKE!', 'a')]", "[('nexiste_pas', '=', 1)]"):
            with self.assertRaises(ValidationError, msg=mauvais):
                self.Measure.create({"name": "d", "code": "essai_domaine", "aggregator": "count",
                                     "model_id": self.env["ir.model"]._get("res.partner").id, "domain": mauvais})

    def test_browser_filters_must_be_domains(self):
        for mauvais in ("[('id','=',1)]", {"essai_partenaires": "pas un domaine"},
                        {"essai_partenaires": [("id", "=")]}):
            with self.assertRaises(UserError, msg=repr(mauvais)):
                self.Measure.bf_value("essai_partenaires", mauvais)


@tagged("post_install", "-at_install")
class TestBfBiMeasureHardening(TransactionCase):
    """Constats d'une revue de sécurité."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Measure = cls.env["bf.bi.measure"]
        cls.designer = new_test_user(cls.env, "bi-durci-concepteur", groups="base.group_user,bf_bi.group_bi_designer")
        cls.m_partner = cls.env["ir.model"]._get("res.partner")

    def test_measure_filter_cannot_reach_records(self):
        # Un filtre qui tente user.sudo().write(...) : aucun enregistrement dans le contexte.
        system = self.env.ref("base.group_system")
        for charge in ("[('id', '!=', user.sudo().write({'groups_id': [(4, %d)]}) and 0)]" % system.id,
                       "[('id', '=', user.id)]", "[('id', '=', env.uid)]"):
            with self.assertRaises(ValidationError, msg=charge):
                self.Measure.with_user(self.designer).create({
                    "name": "x", "code": "essai_charge", "aggregator": "count",
                    "model_id": self.m_partner.id, "domain": charge})
        self.assertNotIn(system, self.designer.groups_id)
        # Ce qui reste permis : uid et les dates.
        self.assertTrue(self.Measure.with_user(self.designer).create({
            "name": "ok", "code": "essai_permis", "aggregator": "count", "model_id": self.m_partner.id,
            "domain": "[('create_uid', '=', uid), ('create_date', '>=', "
                      "(context_today() - relativedelta(days=30)).strftime('%Y-%m-%d'))]"}))

    def test_shared_sub_measures_are_computed_once(self):
        # Deux sous-mesures DISTINCTES par niveau, qui partagent leurs enfants : sans mémo,
        # 2^8 agrégats par branche ; avec, les deux agrégats de base, une fois chacun.
        for base in ("essai_x0", "essai_y0"):
            self.Measure.create({"name": base, "code": base, "aggregator": "count", "model_id": self.m_partner.id})
        for i in range(1, 9):
            for nom in ("essai_x%d" % i, "essai_y%d" % i):
                self.Measure.create({"name": nom, "code": nom, "kind": "formula",
                                     "expression": "essai_x%d + essai_y%d" % (i - 1, i - 1)})
        with patch.object(type(self.Measure), "_aggregate", autospec=True,
                          side_effect=lambda rec, dom: 1.0) as agg:
            self.assertEqual(self.Measure.bf_value("essai_x8", {}), 256.0)
        self.assertEqual(agg.call_count, 2)

    def test_infinite_result_is_an_error(self):
        self.Measure.create({"name": "inf", "code": "essai_inf", "kind": "formula", "expression": "1e308 * 1e308"})
        with self.assertRaises(UserError):
            self.Measure.bf_value("essai_inf", {})

    def test_code_starts_with_a_letter(self):
        with self.assertRaises(ValidationError):
            self.Measure.create({"name": "c", "code": "9lives", "kind": "formula", "expression": "1"})

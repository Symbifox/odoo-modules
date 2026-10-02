import datetime
import json
import time
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.bf_bi_report.models.bf_bi_report import BfBiReport, is_additive, period_bounds, previous_period

D = datetime.date


@tagged("post_install", "-at_install")
class TestPeriods(TransactionCase):
    """La période équivalente se compte en mois et jours civils, jamais en durée."""

    def test_previous_period_same_calendar_length(self):
        cases = [
            # (préréglage, aujourd'hui, début et fin attendus de la période d'avant)
            ("month", D(2026, 3, 20), D(2026, 2, 1), D(2026, 2, 20)),
            ("month", D(2026, 12, 10), D(2026, 11, 1), D(2026, 11, 10)),
            ("quarter", D(2026, 2, 15), D(2025, 10, 1), D(2025, 11, 15)),
            ("year", D(2028, 3, 1), D(2027, 1, 1), D(2027, 3, 1)),   # bissextile : le 1er mars répond au 1er mars
            ("month", D(2026, 4, 30), D(2026, 3, 1), D(2026, 3, 30)),
            ("month", D(2026, 3, 31), D(2026, 2, 1), D(2026, 2, 28)),  # plafonnée à la fin du mois d'avant
            ("year", D(2026, 10, 1), D(2025, 1, 1), D(2025, 10, 1)),
            ("quarter", D(2026, 5, 31), D(2026, 1, 1), D(2026, 2, 28)),   # même quantième, borné à février
            ("quarter", D(2026, 7, 31), D(2026, 4, 1), D(2026, 4, 30)),
        ]
        for preset, today, p_start, p_end in cases:
            start, end = period_bounds(preset, today)
            self.assertEqual(previous_period(start, end, today), (p_start, p_end), (preset, today))

    def test_closed_period_compares_whole_periods(self):
        start, end = period_bounds("last_year", D(2026, 10, 1))
        self.assertEqual((start, end), (D(2025, 1, 1), D(2025, 12, 31)))
        self.assertEqual(previous_period(start, end, D(2026, 10, 1)), (D(2024, 1, 1), D(2024, 12, 31)))


@tagged("post_install", "-at_install")
class TestQuery(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Report = cls.env["bf.bi.report"]
        Partner = cls.env["res.partner"].with_context(tracking_disable=True)
        cls.acme = Partner.create({"name": "Banc rapport Acme", "is_company": True})
        cls.nord = Partner.create({"name": "Banc rapport Nord", "is_company": True})
        cls.small = Partner.create({"name": "Banc rapport Petit", "is_company": True})
        # Des contacts datés : une mesure « contacts » les compte, par mois et par société.
        cls.contacts = Partner
        rows = [(cls.acme, "2026-01-10"), (cls.acme, "2026-01-20"), (cls.acme, "2026-02-05"),
                (cls.nord, "2026-02-14"), (cls.nord, "2026-03-10"), (cls.small, "2026-03-03"),
                (cls.acme, "2025-01-15"), (cls.nord, "2025-02-01")]
        for parent, day in rows:
            contact = Partner.create({"name": "Contact banc", "parent_id": parent.id})
            cls.env.cr.execute("UPDATE res_partner SET create_date = %s WHERE id = %s", (day + " 15:00:00", contact.id))
            cls.contacts |= contact
        cls.env.invalidate_all()
        model = cls.env["ir.model"]._get("res.partner")
        fields_ = cls.env["ir.model.fields"]
        cls.measure = cls.env["bf.bi.measure"].create({
            "name": "Contacts banc", "code": "essai_rapport_contacts", "model_id": model.id, "aggregator": "count",
            "domain": "[('name', '=', 'Contact banc')]",
            "date_field_id": fields_._get("res.partner", "create_date").id,
            "partner_field_id": fields_._get("res.partner", "parent_id").id,
        })
        cls.env["bf.bi.measure"].create({"name": "Double", "code": "essai_rapport_double", "kind": "formula",
                                         "expression": "essai_rapport_contacts * 2"})
        cls.secret = cls.env["bf.bi.measure"].create({
            "name": "Paramètres", "code": "essai_rapport_secret", "aggregator": "count",
            "model_id": cls.env["ir.model"]._get("ir.config_parameter").id})
        cls.employee = new_test_user(cls.env, "bi-rapport-employe", groups="base.group_user", tz="America/Toronto")

    def query(self, spec, filters=None, today=D(2026, 3, 15), user=None):
        filters = {"period": {"preset": "year"}, "customers": [], "selection": None, **(filters or {})}
        Report = self.Report.with_user(user) if user else self.Report
        with patch.object(BfBiReport, "_bf_today", lambda self: today):
            return Report.with_context(tz="America/Toronto").bf_query(spec, filters)

    def test_total_and_previous_same_length(self):
        r = self.query({"measures": ["essai_rapport_contacts", "essai_rapport_double"]})
        self.assertEqual(r["total"], [6.0, 12.0])
        # 2025, du 1er janvier au 15 mars : les deux contacts de 2025.
        self.assertEqual(r["previous"], [2.0, 4.0])
        self.assertEqual((r["period"]["previous_start"], r["period"]["previous_end"]), ("2025-01-01", "2025-03-15"))
        # Période en cours : coupée à aujourd'hui, comme la période d'avant.
        self.assertEqual((r["period"]["start"], r["period"]["end"]), ("2026-01-01", "2026-03-15"))

    def test_future_dated_rows_do_not_skew_the_comparison(self):
        futur = self.env["res.partner"].create({"name": "Contact banc", "parent_id": self.acme.id})
        self.env.cr.execute("UPDATE res_partner SET create_date = '2026-11-02 15:00:00' WHERE id = %s", (futur.id,))
        self.env.invalidate_all()
        self.assertEqual(self.query({"measures": ["essai_rapport_contacts"]})["total"], [6.0])

    def test_repeated_measure_keeps_columns_aligned(self):
        r = self.query({"measures": ["essai_rapport_contacts", "essai_rapport_contacts", "essai_rapport_double"]})
        self.assertEqual([m["code"] for m in r["measures"]], ["essai_rapport_contacts", "essai_rapport_double"])
        self.assertEqual(r["total"], [6.0, 12.0])

    def test_month_buckets_add_up_to_the_total(self):
        r = self.query({"measures": ["essai_rapport_contacts"], "dimension": "month"})
        values = {row["label"]: row["values"][0] for row in r["rows"]}
        self.assertEqual(len(r["rows"]), 3)  # de janvier au mois en cours
        self.assertEqual((values["2026-01"], values["2026-02"], values["2026-03"]), (2.0, 2.0, 2.0))
        self.assertEqual(sum(row["values"][0] for row in r["rows"]), r["total"][0])

    def test_customers_ranked_then_others(self):
        r = self.query({"measures": ["essai_rapport_contacts"], "dimension": "customer", "limit": 2})
        self.assertEqual([row["key"] for row in r["rows"][:2]], [self.acme.id, self.nord.id])
        self.assertEqual([row["values"][0] for row in r["rows"][:2]], [3.0, 2.0])
        self.assertEqual(r["rows"][2]["key"], "others")
        self.assertEqual(r["rows"][2]["values"][0], 1.0)
        self.assertEqual(sum(row["values"][0] for row in r["rows"]), r["total"][0])

    def test_biggest_customer_wins_even_when_last_alphabetically(self):
        Partner = self.env["res.partner"]
        for k in range(55):
            company = Partner.create({"name": "Banc rapport Aa %02d" % k, "is_company": True})
            contact = Partner.create({"name": "Contact banc", "parent_id": company.id})
            self.env.cr.execute("UPDATE res_partner SET create_date = '2026-02-20 15:00:00' WHERE id = %s", (contact.id,))
        big = Partner.create({"name": "Banc rapport Zz Grand", "is_company": True})
        for k in range(5):
            contact = Partner.create({"name": "Contact banc", "parent_id": big.id})
            self.env.cr.execute("UPDATE res_partner SET create_date = '2026-02-21 15:00:00' WHERE id = %s", (contact.id,))
        self.env.invalidate_all()
        r = self.query({"measures": ["essai_rapport_contacts"], "dimension": "customer", "limit": 3})
        self.assertEqual(r["rows"][0]["key"], big.id)
        self.assertEqual(sum(row["values"][0] for row in r["rows"]), r["total"][0])

    def test_rows_without_customer_still_add_up(self):
        seul = self.env["res.partner"].create({"name": "Contact banc"})
        self.env.cr.execute("UPDATE res_partner SET create_date = '2026-02-22 15:00:00' WHERE id = %s", (seul.id,))
        self.env.invalidate_all()
        r = self.query({"measures": ["essai_rapport_contacts"], "dimension": "customer", "limit": 10})
        self.assertEqual(r["rows"][-1]["key"], "others")
        self.assertEqual(sum(row["values"][0] for row in r["rows"]), r["total"][0])

    def test_cross_filter_customer_and_month(self):
        r = self.query({"measures": ["essai_rapport_contacts"]}, {"selection": {"dim": "customer", "id": self.acme.id}})
        self.assertEqual(r["total"], [3.0])
        r = self.query({"measures": ["essai_rapport_contacts"]}, {"selection": {"dim": "month", "start": "2026-02-01"}})
        self.assertEqual(r["total"], [2.0])
        self.assertEqual((r["period"]["start"], r["period"]["end"]), ("2026-02-01", "2026-02-28"))

    def test_customer_rows_under_a_customer_selection(self):
        """Sous un filtrage croisé sur un client, le découpage par client ne garde que lui : pas
        d'« Autres » rempli du reste."""
        r = self.query({"measures": ["essai_rapport_contacts"], "dimension": "customer", "limit": 10},
                       {"selection": {"dim": "customer", "id": self.acme.id}})
        self.assertEqual([row["key"] for row in r["rows"]], [self.acme.id])

    # ------------------------------------------------------------ visuels riches
    def waterfall(self, limit=10, dimension="customer"):
        return self.query({"measures": ["essai_rapport_contacts"], "dimension": dimension, "limit": limit, "kind": "waterfall"})

    def test_waterfall_by_customer_adds_up(self):
        # Un client perdu (présent l'an dernier seulement) compte autant qu'un client gagné.
        perdu = self.env["res.partner"].create({"name": "Banc rapport Perdu", "is_company": True})
        contact = self.env["res.partner"].create({"name": "Contact banc", "parent_id": perdu.id})
        self.env.cr.execute("UPDATE res_partner SET create_date = '2025-02-10 15:00:00' WHERE id = %s", (contact.id,))
        self.env.invalidate_all()
        r = self.waterfall()
        deltas = {row["key"]: row["delta"] for row in r["rows"]}
        self.assertEqual(deltas, {self.acme.id: 2.0, self.nord.id: 1.0, self.small.id: 1.0, perdu.id: -1.0})
        self.assertEqual(r["rows"][0]["key"], self.acme.id)  # le plus grand écart d'abord
        # Période d'avant + écarts = période courante, toujours.
        self.assertEqual(r["previous"][0] + sum(deltas.values()), r["total"][0])
        r = self.waterfall(limit=1)
        self.assertEqual([row["key"] for row in r["rows"]], [self.acme.id, "others"])
        self.assertEqual(r["previous"][0] + sum(row["delta"] for row in r["rows"]), r["total"][0])

    def test_waterfall_refuses_what_does_not_add_up(self):
        Measure = self.env["bf.bi.measure"]
        ratio = Measure.create({"name": "Ratio", "code": "essai_rapport_ratio", "kind": "formula",
                                "expression": "essai_rapport_contacts / essai_rapport_double"})
        moitie = Measure.create({"name": "Moitié", "code": "essai_rapport_moitie", "kind": "formula",
                                 "expression": "-essai_rapport_double / 2 + essai_rapport_contacts"})
        self.assertTrue(is_additive(self.measure))
        self.assertTrue(is_additive(moitie))
        self.assertFalse(is_additive(ratio))
        moyenne = Measure.create({"name": "Moyenne", "code": "essai_rapport_moyenne", "aggregator": "avg",
                                  "model_id": self.env["ir.model"]._get("res.partner").id,
                                  "field_id": self.env["ir.model.fields"]._get("res.partner", "color").id})
        self.assertFalse(is_additive(moyenne))
        cases = (("essai_rapport_contacts * -1", True), ("2 * 3 * essai_rapport_contacts", True),
                 ("essai_rapport_contacts / (2 * 3)", True), ("essai_rapport_contacts / 0", False),
                 ("essai_rapport_contacts * essai_rapport_double", False),
                 ("essai_rapport_contacts + essai_rapport_moyenne", False))
        for n, (expression, attendu) in enumerate(cases):
            formule = Measure.create({"name": expression, "code": "essai_formule_%s" % n,
                                      "kind": "formula", "expression": expression})
            self.assertEqual(is_additive(formule), attendu, expression)
        r = self.query({"measures": ["essai_rapport_moyenne"], "dimension": "customer", "kind": "waterfall"})
        self.assertTrue(r["error"])
        r = self.query({"measures": ["essai_rapport_ratio"], "dimension": "customer", "kind": "waterfall"})
        self.assertEqual(r["rows"], [])
        self.assertIn("essai_rapport_ratio", r["errors"])
        self.assertEqual(r["error"], r["errors"]["essai_rapport_ratio"])
        self.assertEqual([m["name"] for m in r["measures"]], ["Ratio"])  # le titre du visuel reste
        r = self.query({"measures": ["essai_rapport_moitie"], "dimension": "month", "kind": "waterfall"})
        self.assertEqual([row["values"][0] for row in r["rows"]], [0.0, 0.0, 0.0])  # -2x/2 + x = 0, additive

    def test_additivity_is_linear_in_the_formula_tree(self):
        # Trois renvois par niveau, sur dix niveaux : sans mémoire, 3^10 visites (des minutes).
        Measure, code = self.env["bf.bi.measure"], "essai_rapport_contacts"
        for level in range(10):
            nouveau = "essai_niveau_%s" % level
            Measure.create({"name": nouveau, "code": nouveau, "kind": "formula",
                            "expression": " + ".join([code] * 3)})
            code = nouveau
        debut = time.monotonic()
        self.assertTrue(is_additive(Measure._find(code)))
        self.assertLess(time.monotonic() - debut, 1.0)

    def test_waterfall_ranks_by_absolute_change(self):
        perdu = self.env["res.partner"].create({"name": "Banc rapport Perdu", "is_company": True})
        for _n in range(3):
            contact = self.env["res.partner"].create({"name": "Contact banc", "parent_id": perdu.id})
            self.env.cr.execute("UPDATE res_partner SET create_date = '2025-02-10 15:00:00' WHERE id = %s", (contact.id,))
        self.env.invalidate_all()
        r = self.waterfall()
        self.assertEqual((r["rows"][0]["key"], r["rows"][0]["delta"]), (perdu.id, -3.0))  # |−3| avant +2

    def test_waterfall_keeps_lost_customers_beyond_the_member_cap(self):
        perdu = self.env["res.partner"].create({"name": "Banc rapport Perdu", "is_company": True})
        for _n in range(3):
            contact = self.env["res.partner"].create({"name": "Contact banc", "parent_id": perdu.id})
            self.env.cr.execute("UPDATE res_partner SET create_date = '2025-02-10 15:00:00' WHERE id = %s", (contact.id,))
        self.env.invalidate_all()
        with patch("odoo.addons.bf_bi_report.models.bf_bi_report.MAX_MEMBERS", 2):
            r = self.waterfall()
        self.assertIn(perdu.id, [row["key"] for row in r["rows"]])
        self.assertEqual(r["previous"][0] + sum(row["delta"] for row in r["rows"]), r["total"][0])

    def test_waterfall_others_carries_lines_without_customer(self):
        orphelin = self.env["res.partner"].create({"name": "Contact banc"})  # aucune société
        self.env.cr.execute("UPDATE res_partner SET create_date = '2026-02-02 15:00:00' WHERE id = %s", (orphelin.id,))
        self.env.invalidate_all()
        r = self.waterfall()
        autres = [row for row in r["rows"] if row["key"] == "others"]
        self.assertEqual([row["delta"] for row in autres], [1.0])
        self.assertEqual(r["previous"][0] + sum(row["delta"] for row in r["rows"]), r["total"][0])

    def test_rich_visuals_refuse_sources_without_customer_or_date(self):
        # « Paramètres » n'a ni champ client ni champ date : elle compterait en entier partout.
        self.env["bf.bi.measure"].create({"name": "Mixte", "code": "essai_rapport_mixte", "kind": "formula",
                                          "expression": "essai_rapport_contacts - essai_rapport_secret * 0.5"})
        for spec in ({"dimension": "customer", "kind": "waterfall"}, {"dimension": "month", "kind": "waterfall"},
                     {"dimension": "customer", "kind": "heatmap"}):
            r = self.query({"measures": ["essai_rapport_mixte"], **spec})
            self.assertTrue(r["error"], spec)
            self.assertEqual(r["rows"], [], spec)

    def test_waterfall_by_month_is_the_monthly_values(self):
        r = self.waterfall(dimension="month")
        self.assertEqual([row["values"][0] for row in r["rows"]], [2.0, 2.0, 2.0])

    def test_heatmap_cells(self):
        r = self.query({"measures": ["essai_rapport_contacts"], "dimension": "customer", "limit": 3,
                        "kind": "heatmap", "columns": "month"})
        self.assertEqual([c["key"] for c in r["columns"]], ["2026-01-01", "2026-02-01", "2026-03-01"])
        self.assertEqual(r["columns"][-1]["end"], "2026-03-15")  # le mois en cours s'arrête à aujourd'hui
        cells = {row["key"]: row["cells"] for row in r["rows"]}
        self.assertEqual(cells, {self.acme.id: [2.0, 1.0, 0.0], self.nord.id: [0.0, 1.0, 1.0], self.small.id: [0.0, 0.0, 1.0]})
        self.assertNotIn("others", cells)
        r = self.query({"measures": ["essai_rapport_contacts"], "dimension": "customer", "limit": 1, "kind": "heatmap"})
        self.assertEqual([row["key"] for row in r["rows"]], [self.acme.id])  # les plus forts, sans « Autres »
        # Des trimestres sur une période d'un mois : un trimestre tronqué filtrerait faux ; un mois.
        r = self.query({"measures": ["essai_rapport_contacts"], "dimension": "customer", "kind": "heatmap", "columns": "quarter"},
                       {"period": {"preset": "month"}})
        self.assertEqual([(c["key"], c["dim"]) for c in r["columns"]], [("2026-03-01", "month")])
        r = self.query({"measures": ["essai_rapport_contacts"], "dimension": "customer", "kind": "heatmap", "columns": "quarter"})
        self.assertEqual({row["key"]: row["cells"] for row in r["rows"]}[self.acme.id], [3.0])

    def test_rich_specs_are_validated(self):
        base = {"measures": ["essai_rapport_contacts"], "dimension": "customer"}
        for spec in ({**base, "kind": "pie"}, {**base, "kind": "heatmap", "dimension": "month"},
                     {**base, "kind": "heatmap", "columns": "year"}, {**base, "kind": "waterfall", "dimension": None},
                     {**base, "kind": "waterfall", "measures": ["essai_rapport_contacts", "essai_rapport_double"]},
                     {**base, "kind": "heatmap", "limit": 16}):
            with self.assertRaises(UserError, msg=str(spec)):
                self.query(spec)

    def test_drill_through_overrides_the_slicer(self):
        r = self.query({"measures": ["essai_rapport_contacts"]},
                       {"customers": [self.nord.id], "drill": {"dim": "customer", "id": self.acme.id}})
        self.assertEqual(r["total"], [3.0])
        # Tout le résultat (valeur, période précédente, courbe) est celui d'un filtre sur ce client.
        self.assertEqual(r, self.query({"measures": ["essai_rapport_contacts"]}, {"customers": [self.acme.id]}))
        # Sur une page d'extraction, un filtrage croisé par mois s'ajoute au client : en février,
        # Nord a 1 contact sur les 2 du mois.
        r = self.query({"measures": ["essai_rapport_contacts"]},
                       {"drill": {"dim": "customer", "id": self.nord.id}, "selection": {"dim": "month", "start": "2026-02-01"}})
        self.assertEqual(r["total"], [1.0])
        for drill in ({"dim": "month", "id": 1}, {"dim": "customer", "id": "x"}, {"dim": "customer"}, "x"):
            with self.assertRaises(UserError):
                self.query({"measures": ["essai_rapport_contacts"]}, {"drill": drill})

    def test_slicer_customers(self):
        r = self.query({"measures": ["essai_rapport_contacts"]}, {"customers": [self.nord.id, self.small.id]})
        self.assertEqual(r["total"], [3.0])

    def test_datetime_day_follows_the_person_timezone(self):
        # 2026-04-01 02:00 UTC est encore le 31 mars à Toronto : le contact compte en mars.
        late = self.env["res.partner"].create({"name": "Contact banc", "parent_id": self.small.id})
        self.env.cr.execute("UPDATE res_partner SET create_date = '2026-04-01 02:00:00' WHERE id = %s", (late.id,))
        self.env.invalidate_all()
        # Et la toute dernière seconde du 31 mars (microsecondes comprises) compte aussi.
        last = self.env["res.partner"].create({"name": "Contact banc", "parent_id": self.small.id})
        self.env.cr.execute("UPDATE res_partner SET create_date = '2026-04-01 03:59:59.5' WHERE id = %s", (last.id,))
        self.env.invalidate_all()
        r = self.query({"measures": ["essai_rapport_contacts"], "dimension": "month"}, today=D(2026, 4, 15))
        values = {row["label"]: row["values"][0] for row in r["rows"]}
        self.assertEqual((values["2026-03"], values["2026-04"]), (4.0, 0.0))

    def test_measure_the_person_cannot_read_is_an_error_not_a_crash(self):
        r = self.query({"measures": ["essai_rapport_contacts", "essai_rapport_secret"]}, user=self.employee)
        self.assertEqual(r["total"][0], 6.0)
        self.assertIsNone(r["total"][1])
        self.assertIn("essai_rapport_secret", r["errors"])

    def test_unknown_measure_is_reported(self):
        r = self.query({"measures": ["essai_rapport_contacts", "nexiste_pas"]})
        self.assertIn("nexiste_pas", r["errors"])
        self.assertEqual(len(r["measures"]), 1)

    def test_customer_rows_follow_the_person_rights(self):
        """Les clients viennent des lignes que la personne peut lire : ceux d'une société qu'elle
        ne voit pas n'apparaissent pas ; une personne de cette société, elle, les voit."""
        autre = self.env["res.company"].create({"name": "Société banc rapport clients"})
        cache = self.env["res.partner"].create({"name": "Banc rapport Caché", "is_company": True, "company_id": autre.id})
        contact = self.env["res.partner"].create({"name": "Contact banc", "parent_id": cache.id, "company_id": autre.id})
        self.env.cr.execute("UPDATE res_partner SET create_date = '2026-02-02 15:00:00' WHERE id = %s", (contact.id,))
        self.env.invalidate_all()
        r = self.query({"measures": ["essai_rapport_contacts"], "dimension": "customer", "limit": 10}, user=self.employee)
        self.assertNotIn(cache.id, [row["key"] for row in r["rows"]])
        voisin = new_test_user(self.env, "bi-rapport-voisin", groups="base.group_user",
                               company_ids=[Command.set([self.env.company.id, autre.id])], company_id=autre.id)
        Report = self.Report.with_user(voisin).with_context(allowed_company_ids=[autre.id, self.env.company.id])
        with patch.object(BfBiReport, "_bf_today", lambda self: D(2026, 3, 15)):
            r = Report.bf_query({"measures": ["essai_rapport_contacts"], "dimension": "customer", "limit": 10},
                                {"period": {"preset": "year"}, "customers": [], "selection": None})
        self.assertIn(cache.id, [row["key"] for row in r["rows"]])

    def test_computation_budget_stops_a_runaway_visual(self):
        with patch("odoo.addons.bf_bi_report.models.bf_bi_report.MAX_COMPUTATIONS", 5):
            with self.assertRaises(UserError):
                self.query({"measures": ["essai_rapport_contacts"], "dimension": "customer", "limit": 10})

    def test_trend_gives_twelve_months(self):
        r = self.query({"measures": ["essai_rapport_contacts"], "trend": True})
        self.assertEqual(len(r["trend"]), 12)
        self.assertEqual(r["trend"][-1]["key"], "2026-03-01")  # le mois en cours, pas décembre

    def test_bad_requests_are_refused(self):
        bad = [
            ({"measures": []}, {}), ({"measures": ["Pas Bon"]}, {}), ({"measures": ["a"] * 9}, {}),
            ({"measures": ["essai_rapport_contacts"], "dimension": "employee"}, {}),
            ({"measures": ["essai_rapport_contacts"], "limit": 500}, {}),
            ({"measures": ["essai_rapport_contacts"]}, {"period": {"preset": "decade"}}),
            ({"measures": ["essai_rapport_contacts"]}, {"customers": ["1"]}),
            ({"measures": ["essai_rapport_contacts"]}, {"selection": {"dim": "customer", "id": "x"}}),
            ({"measures": ["essai_rapport_contacts"]}, {"selection": {"dim": "month", "start": "pas une date"}}),
            ({"measures": ["essai_rapport_contacts"]}, {"selection": {"dim": "month", "start": ""}}),
            ({"measures": ["essai_rapport_contacts"]}, {"selection": {"dim": "month"}}),
            ({"measures": ["essai_rapport_contacts"]}, {"selection": {"dim": "month", "start": 0}}),
            ({"measures": ["essai_rapport_contacts"]}, {"selection": {"dim": "year", "start": "9999-01-01"}}),
        ]
        for spec, filters in bad:
            with self.assertRaises(UserError, msg=str((spec, filters))):
                self.query(spec, filters)


@tagged("post_install", "-at_install")
class TestReportAccess(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.reader = new_test_user(cls.env, "bi-rapport-lecteur", groups="base.group_user")
        cls.designer = new_test_user(cls.env, "bi-rapport-concepteur", groups="base.group_user,bf_bi.group_bi_designer")
        cls.finance = cls.env["res.groups"].create({"name": "Banc rapport finances"})
        layout = [{"id": "k1", "type": "kpi", "measures": ["essai_x"], "x": 0, "y": 0, "w": 3, "h": 2}]
        cls.opened = cls.env["bf.bi.report"].create({
            "name": "Ouvert", "group_ids": [Command.set([cls.env.ref("base.group_user").id])],
            "page_ids": [Command.create({"name": "Page 1", "layout": json.dumps(layout)})]})
        cls.closed = cls.env["bf.bi.report"].create({
            "name": "Finances", "group_ids": [Command.set([cls.finance.id])],
            "page_ids": [Command.create({"name": "Page 1", "layout": json.dumps(layout)})]})
        cls.draft = cls.env["bf.bi.report"].create({"name": "Brouillon", "page_ids": [Command.create({"name": "P"})]})

    def test_reader_sees_reports_opened_to_their_groups_only(self):
        self.env.invalidate_all()
        Report = self.env["bf.bi.report"].with_user(self.reader)
        names = set(Report.search([("name", "in", ["Ouvert", "Finances", "Brouillon"])]).mapped("name"))
        self.assertEqual(names, {"Ouvert"})
        self.assertEqual(Report.bf_get_report(self.opened.id)["pages"][0]["visuals"][0]["id"], "k1")
        for report in (self.closed, self.draft):
            with self.assertRaises(AccessError):
                Report.bf_get_report(report.id)
        pages = self.env["bf.bi.report.page"].with_user(self.reader).search([("report_id", "in", (self.closed | self.draft).ids)])
        self.assertFalse(pages)

    def test_designer_sees_every_report_and_reader_cannot_write(self):
        self.env.invalidate_all()
        names = set(self.env["bf.bi.report"].with_user(self.designer).search(
            [("name", "in", ["Ouvert", "Finances", "Brouillon"])]).mapped("name"))
        self.assertEqual(names, {"Ouvert", "Finances", "Brouillon"})
        with self.assertRaises(AccessError):
            self.opened.with_user(self.reader).write({"name": "Pirate"})

    def test_other_company_report_is_invisible(self):
        autre = self.env["res.company"].create({"name": "Société banc rapport"})
        self.opened.company_id = autre
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            self.env["bf.bi.report"].with_user(self.designer).bf_get_report(self.opened.id)

    def test_designer_cannot_reach_another_company(self):
        autre = self.env["res.company"].create({"name": "Société banc rapport B"})
        chez_b = self.env["bf.bi.report"].create({"name": "Chez B", "company_id": autre.id,
                                                  "group_ids": [Command.set([self.env.ref("base.group_user").id])]})
        self.env.invalidate_all()
        page = self.draft.page_ids.with_user(self.designer)
        with self.assertRaises(AccessError):
            page.write({"report_id": chez_b.id})
        with self.assertRaises(AccessError):
            self.env["bf.bi.report.page"].with_user(self.designer).create({"name": "Intruse", "report_id": chez_b.id})
        with self.assertRaises(AccessError):
            self.draft.with_user(self.designer).write({"company_id": autre.id})

    def test_archived_report_does_not_open(self):
        self.opened.active = False
        with self.assertRaises(UserError):
            self.env["bf.bi.report"].with_user(self.reader).bf_get_report(self.opened.id)

    def test_layout_is_validated(self):
        page = self.draft.page_ids
        ok = {"id": "a", "type": "column", "measures": ["essai_x"], "dimension": "month", "x": 0, "y": 0, "w": 6, "h": 4}
        page.layout = json.dumps([ok])
        bad = [
            "pas du json", json.dumps({"id": "a"}), json.dumps([{**ok, "type": "carte"}]),
            json.dumps([{**ok, "x": 8}]),
            json.dumps([{**ok, "measures": ["Pas Bon"]}]), json.dumps([ok, ok]),
            json.dumps([{**ok, "type": "line", "dimension": "customer"}]),
            json.dumps([{**ok, "title": ["x"]}]), json.dumps([{**ok, "w": True}]),
            json.dumps([{**ok, "measures": ["essai_x", "essai_x"]}]),
        ]
        for layout in bad:
            with self.assertRaises(ValidationError, msg=layout):
                page.layout = layout


@tagged("post_install", "-at_install")
class TestDesigner(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.reader = new_test_user(cls.env, "bi-rapport-lecteur2", groups="base.group_user")
        cls.designer = new_test_user(cls.env, "bi-rapport-concepteur2", groups="base.group_user,bf_bi.group_bi_designer")
        cls.kpi = {"id": "k1", "type": "kpi", "measures": ["essai_x"], "x": 0, "y": 0, "w": 3, "h": 2}
        cls.report = cls.env["bf.bi.report"].create({
            "name": "Conçu", "group_ids": [Command.set([cls.env.ref("base.group_user").id])],
            "page_ids": [Command.create({"name": "Un", "sequence": 1, "layout": json.dumps([cls.kpi])}),
                         Command.create({"name": "Deux", "sequence": 2})]})
        model = cls.env["ir.model"]._get("res.partner")
        cls.env["bf.bi.measure"].create({
            "name": "Contacts conception", "code": "essai_conception", "model_id": model.id, "aggregator": "count",
            "date_field_id": cls.env["ir.model.fields"]._get("res.partner", "create_date").id})

    def save(self, pages, user=None, revision=None, name=None, theme=None):
        report = self.report.with_user(user or self.designer)
        return report.bf_save(pages, self.report.bf_revision if revision is None else revision, name, theme=theme)

    def test_save_reconciles_pages_and_versions(self):
        one, two = self.report.page_ids.sorted("sequence")
        draft = {"id": "n1", "type": "column", "measures": [], "x": 0, "y": 2, "w": 6, "h": 4}
        result = self.save([{"id": one.id, "name": "Un bis", "visuals": [self.kpi, draft]},
                            {"id": None, "name": "Trois", "visuals": []}], name="Conçu autrement")
        self.assertEqual(result["status"], "saved")
        self.assertEqual(result["revision"], 1)
        pages = self.report.page_ids.sorted("sequence")
        self.assertEqual(pages.mapped("name"), ["Un bis", "Trois"])
        self.assertFalse(two.exists(), "la page retirée est supprimée")
        self.assertEqual(json.loads(pages[0].layout)[1]["id"], "n1", "un visuel à compléter s'enregistre")
        self.assertEqual(self.report.name, "Conçu autrement")
        self.assertEqual(len(self.report.with_user(self.designer).bf_list_versions()), 1)

    def test_reader_cannot_save_nor_see_versions(self):
        with self.assertRaises(AccessError):
            self.save([{"id": None, "name": "P", "visuals": []}], user=self.reader)
        with self.assertRaises(AccessError):
            self.report.with_user(self.reader).bf_list_versions()
        self.assertFalse(self.env["bf.bi.report"].with_user(self.reader).bf_get_report(self.report.id)["can_edit"])
        self.assertTrue(self.env["bf.bi.report"].with_user(self.designer).bf_get_report(self.report.id)["can_edit"])

    def test_stale_revision_is_a_conflict_and_writes_nothing(self):
        self.save([{"id": None, "name": "Première", "visuals": []}])
        result = self.save([{"id": None, "name": "Écrasée", "visuals": []}], revision=0)
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(self.report.page_ids.mapped("name"), ["Première"])

    def test_page_names_hold_in_every_language_and_deleted_pages_come_back(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        one = self.report.page_ids.sorted("sequence")[0]
        self.report.with_user(self.designer).with_context(lang="fr_CA").bf_save(
            [{"id": one.id, "name": "Vue d'ensemble", "visuals": []}], self.report.bf_revision)
        self.assertEqual(one.with_context(lang="en_US").name, "Vue d'ensemble")
        # Un collègue a gardé une version sans cette page ; « garder la mienne » la recrée.
        self.save([{"id": None, "name": "Ailleurs", "visuals": []}])
        result = self.save([{"id": one.id, "name": "Mienne", "visuals": []}])
        self.assertEqual(result["status"], "saved")
        self.assertEqual(self.report.page_ids.mapped("name"), ["Mienne"])

    def test_designer_cannot_save_another_company_report(self):
        autre = self.env["res.company"].create({"name": "Société banc conception"})
        chez_b = self.env["bf.bi.report"].create({"name": "Chez B", "company_id": autre.id,
                                                  "page_ids": [Command.create({"name": "P"})]})
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            chez_b.with_user(self.designer).bf_save([{"id": None, "name": "Intruse", "visuals": []}], 0)
        self.assertEqual(chez_b.page_ids.mapped("name"), ["P"])

    def test_page_of_another_report_cannot_be_taken(self):
        other = self.env["bf.bi.report"].create({"name": "Autre", "page_ids": [Command.create({"name": "Sienne"})]})
        with self.assertRaises(UserError) as caught:
            self.save([{"id": other.page_ids.id, "name": "Volée", "visuals": []}])
        self.assertNotIsInstance(caught.exception, AccessError)
        self.assertEqual(other.page_ids.report_id, other)

    def test_bad_payloads_are_refused(self):
        bad = [[], "x", [{"id": None, "name": "", "visuals": []}], [{"id": "1", "name": "P", "visuals": []}],
               [{"id": None, "name": "P", "visuals": [{"id": "a", "type": "carte"}]}],
               [{"id": None, "name": "P", "visuals": []}] * 21]
        bad += [
            [{"id": None, "name": "P", "visuals": [{**self.kpi, "target": float("nan")}]}],
            [{"id": None, "name": "P", "visuals": [{**self.kpi, "target": float("inf")}]}],
            [{"id": None, "name": "P", "visuals": [{**self.kpi, "pirate": "x"}]}],
            [{"id": None, "name": "P", "visuals": [{**self.kpi, "pirate": "x" * 70000}]}],  # clé inconnue et trop lourde
        ]
        one = self.report.page_ids.sorted("sequence")[0]
        bad.append([{"id": one.id, "name": "A", "visuals": []}, {"id": one.id, "name": "B", "visuals": []}])
        for pages in bad:
            # Un refus de VALIDATION : pas un refus d'accès (AccessError hérite de UserError).
            with self.assertRaises(UserError, msg=str(pages)[:80]) as caught:
                self.save(pages)
            self.assertNotIsInstance(caught.exception, AccessError, str(pages)[:80])

    def test_page_settings_are_saved_validated_and_read(self):
        partner = self.env["res.partner"].create({"name": "Banc réglages"})
        self.save([{"id": None, "name": "Détail", "visuals": [], "drill": "customer"},
                   {"id": None, "name": "L'an dernier", "visuals": [], "preset": "last_year", "customers": [partner.id]}])
        pages = self.env["bf.bi.report"].with_user(self.reader).bf_get_report(self.report.id)["pages"]
        self.assertEqual([(p["drill"], p["preset"], p["customers"]) for p in pages],
                         [("customer", None, []), (None, "last_year", [partner.id])])
        for bad in ({"drill": "month"}, {"preset": "decade"}, {"customers": ["1"]}, {"customers": list(range(1, 60))},
                    {"customers": [partner.id, partner.id]}, {"customers": [0]}, {"customers": [-3]}, {"customers": [10 ** 23]},
                    {"drill": "customer", "customers": [partner.id]}):
            with self.assertRaises(UserError, msg=str(bad)) as caught:
                self.save([{"id": None, "name": "P", "visuals": [], **bad}])
            self.assertNotIsInstance(caught.exception, AccessError)
        with self.assertRaises(ValidationError):
            self.report.page_ids[:1].write({"filter_customers": "pas du json"})
        # La remise en service garde les réglages des pages.
        versions = self.report.with_user(self.designer).bf_list_versions()
        self.save([{"id": None, "name": "Rien", "visuals": []}])
        self.env["bf.bi.report.version"].browse(versions[0]["id"]).with_user(self.designer).bf_restore()
        restored = self.report.page_ids.sorted("sequence")
        self.assertEqual(restored.mapped("drill_dimension"), ["customer", False])
        self.assertEqual(restored.mapped("filter_preset"), [False, "last_year"])
        self.assertEqual([p._bf_settings()["customers"] for p in restored], [[], [partner.id]])

    def test_rich_layouts_are_validated(self):
        def visual(**kw):
            return {"id": "r1", "type": "gauge", "measures": ["essai_x"], "x": 0, "y": 0, "w": 4, "h": 3, **kw}
        good = [visual(target=50, max=100), visual(type="heatmap", dimension="customer", columns="quarter", limit=15),
                visual(type="waterfall", dimension="month"), visual(type="waterfall", measures=[]),
                visual(type="heatmap", measures=[])]
        for v in good:
            self.assertEqual(self.save([{"id": None, "name": "P", "visuals": [v]}])["status"], "saved", v)
        bad = [visual(dimension="month"), visual(type="heatmap", dimension="month"), visual(type="heatmap", columns="year"),
               visual(max=0), visual(max=-1), visual(max="100"), visual(type="waterfall", measures=["essai_x", "essai_y"]),
               visual(type="heatmap", dimension="customer", limit=16), visual(type="waterfall", limit="x"),
               visual(type="heatmap", limit=None)]
        for v in bad:
            with self.assertRaises(UserError, msg=str(v)) as caught:
                self.save([{"id": None, "name": "P", "visuals": [v]}])
            self.assertNotIsInstance(caught.exception, AccessError, str(v))

    def test_theme_saved_read_and_restored(self):
        self.report.company_id.write({"primary_color": "#29ABE2", "secondary_color": "pas une couleur"})
        self.save([{"id": None, "name": "P", "visuals": []}], theme="company")
        self.assertEqual(self.report.theme, "company")
        lu = self.env["bf.bi.report"].with_user(self.reader).bf_get_report(self.report.id)["theme"]
        self.assertEqual(lu, {"name": "company", "company_colors": ["#29ABE2"]})
        with self.assertRaises(UserError) as caught:
            self.save([{"id": None, "name": "P", "visuals": []}], theme="rainbow")
        self.assertNotIsInstance(caught.exception, AccessError)
        version = self.report.with_user(self.designer).bf_list_versions()[0]["id"]
        self.save([{"id": None, "name": "P", "visuals": []}], theme="default")
        self.assertEqual(self.report.theme, "default")
        self.env["bf.bi.report.version"].browse(version).with_user(self.designer).bf_restore()
        self.assertEqual(self.report.theme, "company")
        # Le thème n'est pas une page : l'omettre ne le change pas.
        self.save([{"id": None, "name": "P", "visuals": []}])
        self.assertEqual(self.report.theme, "company")

    def test_designer_fields_say_what_adds_up(self):
        fields_ = {f["code"]: f for f in self.report.with_user(self.designer).bf_designer_fields()}
        self.assertTrue(fields_["essai_conception"]["additive"])

    def test_conflict_creates_no_version(self):
        self.save([{"id": None, "name": "Première", "visuals": []}])
        before = self.env["bf.bi.report.version"].search_count([("report_id", "=", self.report.id)])
        self.assertEqual(self.save([{"id": None, "name": "Écrasée", "visuals": []}], revision=0)["status"], "conflict")
        self.assertEqual(self.env["bf.bi.report.version"].search_count([("report_id", "=", self.report.id)]), before)

    def test_page_company_cannot_be_written(self):
        autre = self.env["res.company"].create({"name": "Société banc page"})
        page = self.report.page_ids[:1].with_user(self.designer)
        with self.assertRaises(AccessError):
            page.write({"company_id": autre.id})
        with self.assertRaises(AccessError):
            self.env["bf.bi.report.page"].with_user(self.designer).create(
                {"name": "P", "report_id": self.report.id, "company_id": autre.id})

    def test_reader_plain_orm_paths_are_refused(self):
        Page = self.env["bf.bi.report.page"].with_user(self.reader)
        with self.assertRaises(AccessError):
            Page.create({"name": "Intruse", "report_id": self.report.id})
        with self.assertRaises(AccessError):
            self.report.page_ids[:1].with_user(self.reader).write({"layout": "[]"})
        with self.assertRaises(AccessError):
            self.env["bf.bi.report.version"].with_user(self.reader).search([])

    def test_restore_across_companies_is_refused(self):
        autre = self.env["res.company"].create({"name": "Société banc version"})
        chez_b = self.env["bf.bi.report"].create({"name": "Chez B", "company_id": autre.id,
                                                  "page_ids": [Command.create({"name": "P"})]})
        chez_b.bf_save([{"id": None, "name": "Q", "visuals": []}], 0)
        version = self.env["bf.bi.report.version"].search([("report_id", "=", chez_b.id)], limit=1)
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            version.with_user(self.designer).bf_restore()

    def test_versions_are_capped_and_restore(self):
        for k in range(32):
            self.save([{"id": None, "name": "Page %s" % k, "visuals": []}])
        versions = self.report.with_user(self.designer).bf_list_versions()
        self.assertEqual(len(versions), 30)
        oldest = self.env["bf.bi.report.version"].browse(versions[-1]["id"])
        self.assertEqual(oldest.revision, 3)
        result = oldest.with_user(self.designer).bf_restore()
        self.assertEqual(result["revision"], 33)
        self.assertEqual(self.report.page_ids.mapped("name"), ["Page 2"])

    def test_designer_fields_describe_what_each_measure_can_split(self):
        fields_ = {f["code"]: f for f in self.env["bf.bi.report"].with_user(self.designer).bf_designer_fields()}
        self.assertTrue(fields_["essai_conception"]["has_date"])
        self.assertFalse(fields_["essai_conception"]["has_partner"])
        self.assertTrue(fields_["essai_conception"]["group"])
        with self.assertRaises(AccessError):
            self.env["bf.bi.report"].with_user(self.reader).bf_designer_fields()

    def test_new_report_opens_in_the_designer(self):
        action = self.env["bf.bi.report"].with_user(self.designer).action_create_and_open()
        report = self.env["bf.bi.report"].browse(action["params"]["report_id"])
        self.assertTrue(action["params"]["design"])
        self.assertEqual(len(report.page_ids), 1)
        with self.assertRaises(AccessError):
            self.env["bf.bi.report"].with_user(self.reader).action_create_and_open()


@tagged("post_install", "-at_install")
class TestTemplates(TransactionCase):
    """Les modèles livrés : des mises en page valides, proposés seulement quand leurs ponts sont là."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.designer = new_test_user(cls.env, "bi-rapport-concepteur3", groups="base.group_user,bf_bi.group_bi_designer")
        cls.reader = new_test_user(cls.env, "bi-rapport-lecteur3", groups="base.group_user")

    def test_every_template_layout_is_valid(self):
        from odoo.addons.bf_bi_report.models.templates import TEMPLATES
        Page = self.env["bf.bi.report.page"]
        for template in TEMPLATES:
            for page in template["pages"]:
                visuals = [dict(v, title=str(v["title"])) if "title" in v else v for v in page["visuals"]]
                self.assertIsNone(Page._bf_layout_errors(visuals), (template["key"], str(page["name"])))
                cells = set()
                for v in visuals:  # aucun visuel n'en recouvre un autre
                    mine = {(x, y) for x in range(v["x"], v["x"] + v["w"]) for y in range(v["y"], v["y"] + v["h"])}
                    self.assertFalse(cells & mine, (template["key"], v["id"]))
                    cells |= mine

    def test_availability_follows_installed_bridges(self):
        statuses = {t["key"]: t for t in self.env["bf.bi.report"].with_user(self.designer).bf_list_templates()}
        installed = set(self.env["ir.module.module"].search([("state", "=", "installed")]).mapped("name"))
        for key, modules in (("time", ["bf_bi_timesheet"]), ("cx", ["bf_bi_cx"]), ("hosting", ["bf_bi_hosting"]),
                             ("hour_bank", ["bf_bi_hour_bank"])):
            self.assertEqual(statuses[key]["available"], all(m in installed for m in modules), key)
        self.assertEqual(statuses["management"]["available"],
                         all(statuses[k]["available"] for k in ("time", "cx", "hosting", "hour_bank")))

    def test_create_from_template_names_every_language(self):
        statuses = self.env["bf.bi.report"].with_user(self.designer).bf_list_templates()
        available = [t["key"] for t in statuses if t["available"]]
        if not available:
            self.skipTest("aucun pont bf_bi installé")
        self.env["res.lang"]._activate_lang("fr_CA")
        Report = self.env["bf.bi.report"].with_user(self.designer).with_context(lang="en_US")
        action = Report.bf_create_from_template(available[0])
        report = self.env["bf.bi.report"].browse(action["params"]["report_id"])
        self.assertEqual(report.with_context(lang="en_US").name, next(t["name"] for t in statuses if t["key"] == available[0]))
        self.assertNotEqual(report.with_context(lang="fr_CA").name, report.with_context(lang="en_US").name)
        # Chaque visuel calcule : aucune mesure manquante.
        for page in report.page_ids:
            for visual in page._bf_visuals():
                for code in visual["measures"]:
                    self.env["bf.bi.measure"]._find(code)

    def test_saving_keeps_the_translations_of_unchanged_names(self):
        statuses = self.env["bf.bi.report"].with_user(self.designer).bf_list_templates()
        available = [t["key"] for t in statuses if t["available"]]
        if not available:
            self.skipTest("aucun pont bf_bi installé")
        self.env["res.lang"]._activate_lang("fr_CA")
        action = self.env["bf.bi.report"].with_user(self.designer).bf_create_from_template(available[0])
        report = self.env["bf.bi.report"].browse(action["params"]["report_id"])
        anglais = report.page_ids.sorted("sequence").with_context(lang="en_US").mapped("name")
        # Un concepteur francophone déplace un visuel : les noms (inchangés) gardent leur anglais.
        fr = report.with_user(self.designer).with_context(lang="fr_CA")
        pages = [{"id": p["id"], "name": p["name"], "visuals": p["visuals"], "drill": p["drill"]}
                 for p in fr.bf_get_report(report.id)["pages"]]
        self.assertEqual(fr.bf_save(pages, report.bf_revision)["status"], "saved")
        self.assertEqual(report.page_ids.sorted("sequence").with_context(lang="en_US").mapped("name"), anglais)
        # Un nom changé vaut pour toutes les langues.
        pages[0]["name"] = "Sommaire"
        fr.bf_save(pages, report.bf_revision)
        self.assertEqual(report.page_ids.sorted("sequence")[0].with_context(lang="en_US").name, "Sommaire")

    def test_restore_keeps_page_names_in_every_language(self):
        statuses = self.env["bf.bi.report"].with_user(self.designer).bf_list_templates()
        available = [t["key"] for t in statuses if t["available"]]
        if not available:
            self.skipTest("aucun pont bf_bi installé")
        self.env["res.lang"]._activate_lang("fr_CA")
        action = self.env["bf.bi.report"].with_user(self.designer).bf_create_from_template(available[0])
        report = self.env["bf.bi.report"].browse(action["params"]["report_id"]).with_user(self.designer)
        fr = report.with_context(lang="fr_CA")
        pages = [{"id": p["id"], "name": p["name"], "visuals": p["visuals"], "drill": p["drill"]}
                 for p in fr.bf_get_report(report.id)["pages"]]
        fr.bf_save(pages, report.bf_revision)  # une version qui porte les noms des deux langues
        version = report.bf_list_versions()[0]["id"]
        fr.bf_save([{"id": None, "name": "Une seule", "visuals": []}], report.bf_revision)
        self.env["bf.bi.report.version"].browse(version).with_user(self.designer).with_context(lang="fr_CA").bf_restore()
        restored = report.page_ids.sorted("sequence")
        self.assertEqual(restored.with_context(lang="fr_CA").mapped("name"), [p["name"] for p in pages])
        self.assertNotEqual(restored.with_context(lang="en_US").mapped("name"),
                            restored.with_context(lang="fr_CA").mapped("name"))
        self.assertIn("Overview", restored.with_context(lang="en_US").mapped("name"))

    def test_validation_message_in_the_reader_language(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        report = self.env["bf.bi.report"].with_user(self.designer).with_context(lang="fr_CA").create({"name": "Messages"})
        bad = [{"id": None, "name": "P", "visuals": [{"id": "g", "type": "gauge", "measures": [], "x": 0, "y": 0,
                                                       "w": 4, "h": 3, "max": -1}]}]
        with self.assertRaises(UserError) as caught:
            report.bf_save(bad, report.bf_revision)
        self.assertIn("maximum invalide", str(caught.exception))

    def test_template_list_is_for_designers(self):
        with self.assertRaises(AccessError):
            self.env["bf.bi.report"].with_user(self.reader).bf_list_templates()

    def test_template_without_its_measure_or_module_is_refused(self):
        from odoo.addons.bf_bi_report.models.templates import TEMPLATES
        Report = self.env["bf.bi.report"].with_user(self.designer)
        hour_bank = next(t for t in TEMPLATES if t["key"] == "hour_bank")
        # Un pont absent : le modèle le nomme, et refuse d'être créé.
        with patch.dict(hour_bank, {"modules": ["bf_bi_hour_bank", "bf_bi_absent"]}):
            status = next(t for t in Report.bf_list_templates() if t["key"] == "hour_bank")
            self.assertEqual((status["available"], status["missing_modules"]), (False, ["bf_bi_absent"]))
            with self.assertRaises(UserError):
                Report.bf_create_from_template("hour_bank")
        # Une mesure archivée : idem, et le rapport de direction qui la montre aussi.
        measure = self.env["bf.bi.measure"].search([("code", "=", "hour_bank_adjustments")])
        if not measure:
            self.skipTest("pont des banques d'heures absent")
        measure.active = False
        statuses = {t["key"]: t for t in Report.bf_list_templates()}
        for key in ("hour_bank", "management"):
            self.assertEqual((statuses[key]["available"], statuses[key]["missing_codes"]), (False, ["hour_bank_adjustments"]))
            with self.assertRaises(UserError):
                Report.bf_create_from_template(key)

    def test_only_designers_create_and_only_available_templates(self):
        Report = self.env["bf.bi.report"]
        with self.assertRaises(AccessError):
            Report.with_user(self.reader).bf_create_from_template("time")
        with self.assertRaises(UserError):
            Report.with_user(self.designer).bf_create_from_template("nope")
        missing = [t["key"] for t in Report.with_user(self.designer).bf_list_templates() if not t["available"]]
        for key in missing:
            with self.assertRaises(UserError):
                Report.with_user(self.designer).bf_create_from_template(key)

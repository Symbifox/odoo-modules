"""Mesures nommées sur une source externe, et correspondance des clients.

Une mesure externe agrège une colonne d'une table de connexion. Elle suit les filtres du
tableau de bord comme une mesure Odoo : la période par sa colonne date, le client par la
table de correspondance de la connexion. Elle se calcule au nom de la personne qui regarde,
qui doit avoir droit à la connexion.
"""
import datetime
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.bf_bi.models import bf_bi_connection, bf_bi_external as external
from odoo.addons.bf_bi.tests.test_bf_bi_sources import b64, xlsx

D = datetime.datetime


def factures():
    return xlsx({"Factures": [
        ["Date", "Client", "Montant", "Note"],
        [D(2026, 1, 10), "Alpha", 100, "a"],
        [D(2026, 1, 20), "  ALPHA ", 50, "b"],  # même client : casse et espaces ignorés
        [D(2026, 2, 5), "Bêta", 30, "c"],
        [D(2026, 3, 1), "Inconnu", 7, "d"],      # sans correspondance : aucun client
        [D(2025, 2, 1), "Alpha", 40, "e"],
        [None, "Bêta", 1000, "f"],               # sans date : hors de toute période
        [D(2026, 3, 2), "Bêta", None, "g"],      # sans montant : compte comme ligne, pas dans une moyenne
    ]})


@tagged("post_install", "-at_install")
class TestBfBiExternalMeasure(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, lang="en_US"))
        Partner = cls.env["res.partner"].with_context(tracking_disable=True)
        cls.alpha = Partner.create({"name": "Alphabanc Inc.", "is_company": True})
        cls.beta = Partner.create({"name": "Bêtabanc Ltée", "is_company": True})
        cls.beta_contact = Partner.create({"name": "Bea", "parent_id": cls.beta.id})
        cls.reader = new_test_user(cls.env, "bi-lecteur-ext", groups="base.group_user")
        cls.outsider = new_test_user(cls.env, "bi-exterieur-ext", groups="base.group_user")
        cls.designer = new_test_user(cls.env, "bi-concepteur-ext", groups="base.group_user,bf_bi.group_bi_designer")
        cls.connection = cls.env["bf.bi.connection"].create({
            "name": "Factures", "code": "essai_ext", "kind": "file", "file_data": b64(factures()),
            "file_name": "factures.xlsx", "user_ids": [(6, 0, [cls.reader.id, cls.designer.id])],
            "customer_ids": [(0, 0, {"name": "Alpha", "partner_id": cls.alpha.id}),
                             (0, 0, {"name": "Bêta", "partner_id": cls.beta.id})],
        })
        Measure = cls.env["bf.bi.measure"]
        cls.amount = Measure.create({
            "name": "Montant facturé", "code": "essai_ext_montant", "kind": "external", "connection_code": "essai_ext",
            "source_table": "Factures", "aggregator": "sum", "value_column": "Montant", "date_column": "Date",
            "customer_column": "Client", "unit": "currency"})
        cls.count = Measure.create({
            "name": "Factures", "code": "essai_ext_nombre", "kind": "external", "connection_code": "essai_ext",
            "source_table": "Factures", "aggregator": "count", "date_column": "Date", "customer_column": "Client"})

    def setUp(self):
        super().setUp()
        bf_bi_connection._CACHE.clear()

    def value(self, code, domains=None, user=None):
        return self.env["bf.bi.measure"].with_user(user or self.reader).bf_value(code, domains or {})

    def january(self):
        return ["&", ("date", ">=", "2026-01-01"), ("date", "<=", "2026-01-31")]

    # -- calcul -----------------------------------------------------------------------

    def test_total_without_filters(self):
        self.assertEqual(self.value("essai_ext_montant"), 1227.0)
        self.assertEqual(self.value("essai_ext_nombre"), 7.0)

    def test_period_filter_uses_the_date_column(self):
        self.assertEqual(self.value("essai_ext_montant", {"essai_ext_montant": self.january()}), 150.0)
        # Une ligne sans date n'entre dans aucune période, comme une date vide dans Odoo.
        year = ["&", ("date", ">=", "2026-01-01"), ("date", "<=", "2026-12-31")]
        self.assertEqual(self.value("essai_ext_montant", {"essai_ext_montant": year}), 187.0)
        self.assertEqual(self.value("essai_ext_montant", {"essai_ext_montant": [("date", "<", "2026-01-11")]}), 140.0)
        self.assertEqual(self.value("essai_ext_montant", {"essai_ext_montant": [("date", ">", "2026-03-01")]}), 0.0)
        self.assertEqual(self.value("essai_ext_montant", {"essai_ext_montant": [("date", "=", "2026-02-05")]}), 30.0)

    def test_customer_filter_follows_the_correspondence(self):
        m = "essai_ext_montant"
        self.assertEqual(self.value(m, {m: [("partner_id", "in", [self.alpha.id])]}), 190.0)
        self.assertEqual(self.value(m, {m: [("partner_id", "=", self.beta.id)]}), 1030.0)
        # Comme dans Odoo, « not in » et « != » gardent les lignes sans client.
        self.assertEqual(self.value(m, {m: [("partner_id", "not in", [self.alpha.id])]}), 1037.0)
        self.assertEqual(self.value(m, {m: [("partner_id", "!=", self.alpha.id)]}), 1037.0)
        self.assertEqual(self.value(m, {m: [("partner_id", "=", False)]}), 7.0)
        self.assertEqual(self.value(m, {m: [("partner_id", "in", [False, self.alpha.id])]}), 197.0)
        # Des termes juxtaposés valent ET, comme dans Odoo.
        self.assertEqual(self.value(m, {m: [("partner_id", "in", [self.alpha.id]), ("date", ">=", "2026-01-01")]}), 150.0)
        both = ["&", ("partner_id", "in", [self.alpha.id]), *self.january()]
        self.assertEqual(self.value(m, {m: both}), 150.0)
        either = ["|", ("partner_id", "=", self.beta.id), ("date", "=", "2026-01-10")]
        self.assertEqual(self.value(m, {m: either}), 1130.0)

    def test_child_of_takes_the_contacts_of_a_company(self):
        self.connection.customer_ids.filtered(lambda c: c.name == "Bêta").partner_id = self.beta_contact
        m = "essai_ext_montant"
        self.assertEqual(self.value(m, {m: [("partner_id", "child_of", [self.beta.id])]}), 1030.0)
        self.assertEqual(self.value(m, {m: [("partner_id", "in", [self.beta.id])]}), 0.0)

    def test_a_changed_correspondence_applies_at_once(self):
        m = "essai_ext_montant"
        self.assertEqual(self.value(m, {m: [("partner_id", "=", False)]}), 7.0)
        # Relevée mais pas encore reliée : la valeur compte toujours sans client.
        inconnu = self.env["bf.bi.connection.customer"].create({"connection_id": self.connection.id, "name": "inconnu"})
        self.assertEqual(self.value(m, {m: [("partner_id", "=", False)]}), 7.0)
        inconnu.partner_id = self.alpha
        self.assertEqual(self.value(m, {m: [("partner_id", "=", False)]}), 0.0)
        self.assertEqual(self.value(m, {m: [("partner_id", "=", self.alpha.id)]}), 197.0)

    def test_aggregators(self):
        Measure = self.env["bf.bi.measure"]
        base = {"kind": "external", "connection_code": "essai_ext", "source_table": "Factures",
                "value_column": "Montant", "date_column": "Date"}
        for code, aggregator, expected in (("essai_ext_moy", "avg", 1227 / 6), ("essai_ext_min", "min", 7.0),
                                           ("essai_ext_max", "max", 1000.0), ("essai_ext_cpt", "count", 7.0)):
            Measure.create(dict(base, name=code, code=code, aggregator=aggregator))
            self.assertAlmostEqual(self.value(code), expected)
        # Sans colonne à agréger, la mesure compte les lignes, quel que soit l'agrégat.
        Measure.create(dict(base, name="Lignes", code="essai_ext_lignes", aggregator="sum", value_column=False))
        self.assertEqual(self.value("essai_ext_lignes"), 7.0)
        # Sur aucune ligne : une moyenne n'a pas de valeur (case vide), une somme vaut 0.
        none = {"essai_ext_moy": [("date", ">", "2030-01-01")]}
        self.assertIs(self.value("essai_ext_moy", none), False)
        self.assertEqual(self.value("essai_ext_montant", {"essai_ext_montant": [("date", ">", "2030-01-01")]}), 0.0)

    def test_a_formula_can_mix_external_and_odoo_measures(self):
        Measure = self.env["bf.bi.measure"]
        Measure.create({"name": "Partenaires", "code": "essai_ext_partenaires", "aggregator": "count",
                        "model_id": self.env["ir.model"]._get("res.partner").id,
                        "domain": "[('id', '=', %d)]" % self.alpha.id})
        Measure.create({"name": "Mixte", "code": "essai_ext_mixte", "kind": "formula",
                        "expression": "essai_ext_montant / essai_ext_nombre + essai_ext_partenaires"})
        self.assertAlmostEqual(self.value("essai_ext_mixte"), 1227 / 7 + 1)
        leaves = self.env["bf.bi.measure"].with_user(self.reader).bf_describe("essai_ext_mixte")["leaves"]
        self.assertEqual({leaf["code"] for leaf in leaves}, {"essai_ext_montant", "essai_ext_nombre", "essai_ext_partenaires"})

    def test_describe_announces_the_fields_the_filters_apply_to(self):
        Measure = self.env["bf.bi.measure"].with_user(self.reader)
        leaf = Measure.bf_describe("essai_ext_montant")["leaves"][0]
        self.assertEqual((leaf["date_field"], leaf["date_type"], leaf["partner_field"]), ("date", "date", "partner_id"))
        self.env["bf.bi.measure"].create({
            "name": "Sans date", "code": "essai_ext_sans_date", "kind": "external", "connection_code": "essai_ext",
            "source_table": "Factures", "aggregator": "sum", "value_column": "Montant"})
        leaf = Measure.bf_describe("essai_ext_sans_date")["leaves"][0]
        self.assertEqual((leaf["date_field"], leaf["partner_field"]), (False, False))
        listed = {m["code"]: m["has_date"] for m in Measure.bf_list()}
        self.assertEqual((listed["essai_ext_montant"], listed["essai_ext_sans_date"]), (True, False))

    def test_the_table_is_read_once_through_the_cache(self):
        Connection = type(self.env["bf.bi.connection"])
        with patch.object(Connection, "_bf_fetch", autospec=True, side_effect=Connection._bf_fetch) as fetch:
            self.value("essai_ext_montant")
            self.value("essai_ext_nombre", {"essai_ext_nombre": self.january()})
            self.env["bf.bi.connection"].with_user(self.reader).bf_fetch_table("essai_ext", "Factures")
        self.assertEqual(fetch.call_count, 1)

    def test_a_new_file_is_served_at_once(self):
        # Les lignes converties sont gardées pour la transaction, mais liées à la table lue.
        self.assertEqual(self.value("essai_ext_montant"), 1227.0)
        self.connection.write({"file_data": b64(xlsx({"Factures": [["Date", "Client", "Montant"],
                                                                   [D(2026, 1, 1), "Alpha", 5]]})),
                               "file_name": "factures.xlsx"})
        self.assertEqual(self.value("essai_ext_montant"), 5.0)

    # -- refus ------------------------------------------------------------------------

    def test_filters_from_the_browser_are_checked(self):
        m = "essai_ext_montant"
        for domain in ([("Montant", ">", 0)], [("date", "like", "2026")], [("date", ">=", "2026-01")],
                       [("date", ">=", 20260101)], [("partner_id", "in", ["Alpha"])], [("partner_id", "in", [True])],
                       [("partner_id", "ilike", "a")], ["&", ("date", ">=", "2026-01-01")], ["!"], [("date", ">=")],
                       [("partner_id", "child_of", [False])], "[]",
                       # Odoo y ajoute ou retire les cases vides : refusés plutôt qu'à peu près justes.
                       ["!", ("partner_id", "=", self.beta.id)], [("date", "!=", "2026-02-05")],
                       ["&"] * 150 + [("date", ">=", "2026-01-01")] * 151):
            with self.subTest(domain=domain), self.assertRaises(UserError):
                self.value(m, {m: domain})

    def test_the_reader_needs_the_connection(self):
        with self.assertRaises(AccessError):
            self.value("essai_ext_montant", user=self.outsider)
        self.connection.user_ids = [(4, self.outsider.id)]
        self.assertEqual(self.value("essai_ext_montant", user=self.outsider), 1227.0)

    def test_a_designer_builds_only_on_a_connection_they_can_use(self):
        vals = {"name": "Concepteur", "code": "essai_ext_concepteur", "kind": "external",
                "connection_code": "essai_ext", "source_table": "Factures", "aggregator": "count"}
        Measure = self.env["bf.bi.measure"].with_user(self.designer)
        self.assertTrue(Measure.create(vals))
        self.connection.user_ids = [(3, self.designer.id)]
        with self.assertRaises(ValidationError):
            Measure.create(dict(vals, code="essai_ext_concepteur_2"))
        # Même message qu'un code inexistant : les codes ne se devinent pas.
        with self.assertRaises(ValidationError):
            Measure.create(dict(vals, code="essai_ext_concepteur_3", connection_code="nexiste_pas"))
        with self.assertRaises(ValidationError):
            self.env["bf.bi.measure"].create(dict(vals, code="essai_ext_sans_table", source_table=False))
        # Ni retoucher les colonnes d'une mesure de l'administration sur cette connexion.
        for column, value in (("value_column", "Note"), ("date_column", "Note"), ("customer_column", "Note"),
                              ("aggregator", "avg")):
            with self.subTest(column=column), self.assertRaises(ValidationError):
                self.amount.with_user(self.designer).write({column: value})

    def test_columns_are_checked(self):
        cases = ({"value_column": "Nope"}, {"value_column": "Note"}, {"date_column": "Client"},
                 {"customer_column": "Nope"}, {"date_column": "Nope"})
        messages = ("no column", "numbers only", "dates only", "no column", "no column")
        for i, (change, message) in enumerate(zip(cases, messages)):
            vals = {"name": "c", "code": "essai_ext_col_%d" % i, "kind": "external", "connection_code": "essai_ext",
                    "source_table": "Factures", "aggregator": "sum", "value_column": "Montant", **change}
            self.env["bf.bi.measure"].create(vals)
            with self.subTest(change=change), self.assertRaises(UserError) as err:
                self.value(vals["code"])
            self.assertIn(message, str(err.exception))

    def grist_like(self, rows, types):
        """La connexion répond comme une source Grist : types bruts de Grist, cellules d'erreur."""
        data = {"header": ["Quand", "Client", "Montant"], "types": types, "rows": rows, "truncated": False}
        Connection = type(self.env["bf.bi.connection"])
        return patch.object(Connection, "_bf_fetch", lambda self, table, limit=None: data)

    def test_grist_types_and_error_cells(self):
        self.env["bf.bi.measure"].create({
            "name": "Grist", "code": "essai_ext_grist", "kind": "external", "connection_code": "essai_ext",
            "source_table": "Factures", "aggregator": "sum", "value_column": "Montant", "date_column": "Quand",
            "customer_column": "Client"})
        rows = [[46023.75, "Alpha", 10], [46054.0, "Alpha", "#ERREUR"], [46055.5, "Bêta", 5]]
        with self.grist_like(rows, ["DateTime:America/Toronto", "Text", "Int"]):
            self.assertEqual(self.value("essai_ext_grist"), 15.0)  # le texte d'erreur est laissé de côté
            self.assertEqual(self.value("essai_ext_grist", {"essai_ext_grist": self.january()}), 10.0)

    def test_an_infinite_result_is_refused(self):
        self.env["bf.bi.measure"].create({
            "name": "Somme", "code": "essai_ext_somme_g", "kind": "external", "connection_code": "essai_ext",
            "source_table": "Factures", "aggregator": "sum", "value_column": "Montant"})
        for rows in ([[46023, "Alpha", 1e308], [46024, "Alpha", 1e308]], [[46023, "Alpha", 10 ** 400]]):
            bf_bi_connection._CACHE.clear()
            with self.subTest(rows=str(rows)[:40]), self.grist_like(rows, ["Date", "Text", "Numeric"]), \
                    self.assertRaises(UserError) as err:
                self.value("essai_ext_somme_g")
            self.assertIn("too large", str(err.exception))

    def test_an_empty_table_or_column_is_worth_nothing_not_an_error(self):
        with self.grist_like([], []):
            self.assertEqual(self.value("essai_ext_montant"), 0.0)
            self.assertEqual(self.value("essai_ext_nombre"), 0.0)
        bf_bi_connection._CACHE.clear()
        # Colonne vide de bout en bout : lue « Text », elle vaut 0 (somme), pas une erreur.
        with self.grist_like([[46023, "Alpha", None]], ["Date", "Text", "Text"]):
            self.env["bf.bi.measure"].create({
                "name": "Vide", "code": "essai_ext_vide", "kind": "external", "connection_code": "essai_ext",
                "source_table": "Factures", "aggregator": "sum", "value_column": "Montant", "date_column": "Quand"})
            self.assertEqual(self.value("essai_ext_vide"), 0.0)

    def test_header_spaces_do_not_matter(self):
        self.connection.write({"file_data": b64(xlsx({"Factures": [[" Date", "Client ", "Montant  "],
                                                                   [D(2026, 1, 1), "Alpha", 5]]})),
                               "file_name": "factures.xlsx"})
        self.assertEqual(self.value("essai_ext_montant", {"essai_ext_montant": [("partner_id", "=", self.alpha.id)]}), 5.0)

    def test_the_contact_belongs_to_the_connection_company(self):
        autre = self.env["res.company"].create({"name": "Société banc ext 2"})
        ailleurs = self.env["res.partner"].create({"name": "Ailleurs banc", "company_id": autre.id})
        with self.assertRaises(UserError):
            self.env["bf.bi.connection.customer"].create(
                {"connection_id": self.connection.id, "name": "Ailleurs", "partner_id": ailleurs.id})
        sans_societe = self.env["res.partner"].create({"name": "Partout banc", "company_id": False})
        self.assertTrue(self.env["bf.bi.connection.customer"].create(
            {"connection_id": self.connection.id, "name": "Partout", "partner_id": sans_societe.id}))

    def test_an_aggregation_is_required(self):
        self.amount.aggregator = False
        with self.assertRaises(UserError) as err:
            self.value("essai_ext_montant")
        self.assertIn("aggregation", str(err.exception))

    def test_a_truncated_table_is_refused(self):
        self.connection.row_limit = 3
        with self.assertRaises(UserError) as err:
            self.value("essai_ext_montant")
        self.assertIn("maximum", str(err.exception))
        self.connection.row_limit = 7
        self.assertEqual(self.value("essai_ext_montant"), 1227.0)

    # -- correspondance des clients ------------------------------------------------------

    def test_customer_keys(self):
        self.assertEqual(external.customer_key("  Érablière   Nordique "), "érablière nordique")
        self.assertEqual(external.customer_key(12.0), "12")
        self.assertEqual(external.customer_key(12.5), "12.5")
        self.assertEqual(external.customer_key(7), "7")
        for empty in (None, "", "   ", True, False):
            self.assertIsNone(external.customer_key(empty))
        self.assertEqual(external.normalized_name("Érablière Nordique inc."), "erabliere nordique")
        self.assertEqual(external.normalized_name("ÉRABLIÈRE-NORDIQUE, Ltée"), "erabliere nordique")
        self.assertIsNone(external.normalized_name("Inc."))

    def test_collecting_fills_new_values_and_suggests_contacts(self):
        Partner = self.env["res.partner"]
        gamma = Partner.create({"name": "Gammabanc conseil", "is_company": True, "ref": "GB-4242"})
        Partner.create({"name": "Deltabanc", "is_company": True})
        Partner.create({"name": "Deltabanc inc.", "is_company": True})
        Partner.create({"name": "Epsilonbanc", "is_company": True, "email": "Factures@Epsilonbanc.test"})
        # Un contact d'une autre société n'est pas suggéré ; un nom sans chiffres n'est pas une référence.
        autre = self.env["res.company"].create({"name": "Société banc ext"})
        Partner.create({"name": "Zetabanc", "is_company": True, "company_id": autre.id})
        Partner.create({"name": "Etabanc", "is_company": True, "ref": "INCONNU"})
        self.connection.write({
            "customer_ids": [(5, 0, 0)],
            "file_data": b64(xlsx({"Factures": [
                ["Date", "Client", "Montant"], [D(2026, 1, 1), "ALPHABANC INC", 1], [D(2026, 1, 2), "Betabanc", 1],
                [D(2026, 1, 3), "gb4242", 1], [D(2026, 1, 4), "Deltabanc", 1], [D(2026, 1, 5), "factures@epsilonbanc.test", 1],
                [D(2026, 1, 6), "Inconnu", 1], [D(2026, 1, 7), "alphabanc inc", 1], [D(2026, 1, 8), 42.0, 1],
                [D(2026, 1, 9), "Zetabanc", 1]]})),
            "file_name": "factures.xlsx"})
        self.connection.action_collect_customers()
        found = {c.name: (c.partner_id, c.suggested) for c in self.connection.customer_ids}
        self.assertEqual(set(found), {"ALPHABANC INC", "Betabanc", "gb4242", "Deltabanc", "factures@epsilonbanc.test",
                                      "Inconnu", "42", "Zetabanc"})
        epsilon = Partner.search([("name", "=", "Epsilonbanc")])
        self.assertEqual(found["ALPHABANC INC"], (self.alpha, True))     # forme juridique ignorée
        self.assertEqual(found["Betabanc"], (self.beta, True))           # accents et « Ltée » ignorés
        self.assertEqual(found["gb4242"], (gamma, True))               # par la référence
        self.assertEqual(found["factures@epsilonbanc.test"], (epsilon, True))
        self.assertEqual(found["Deltabanc"], (Partner, False))           # deux contacts : pas de suggestion
        self.assertEqual(found["Inconnu"], (Partner, False))
        self.assertEqual(found["Zetabanc"], (Partner, False))
        # Une seconde collecte n'ajoute ni ne change rien ; un choix fait à la main n'est plus une suggestion.
        self.connection.customer_ids.filtered(lambda c: c.name == "Betabanc").partner_id = gamma
        self.connection.action_collect_customers()
        self.assertEqual(len(self.connection.customer_ids), 8)
        beta = self.connection.customer_ids.filtered(lambda c: c.name == "Betabanc")
        self.assertEqual((beta.partner_id, beta.suggested), (gamma, False))

    def test_collecting_is_for_administrators(self):
        with self.assertRaises(AccessError):
            self.connection.with_user(self.reader).sudo().action_collect_customers()
        self.env["bf.bi.measure"].search([("connection_code", "=", "essai_ext")]).write({"customer_column": False})
        with self.assertRaises(UserError):
            self.connection.action_collect_customers()

    def test_a_value_has_one_correspondence(self):
        Customer = self.env["bf.bi.connection.customer"]
        with self.assertRaises(Exception), self.env.cr.savepoint():
            Customer.create({"connection_id": self.connection.id, "name": " alpha "})
        with self.assertRaises(ValidationError):
            Customer.create({"connection_id": self.connection.id, "name": "   "})

    def test_the_correspondence_is_admin_only(self):
        with self.assertRaises(AccessError):
            self.env["bf.bi.connection.customer"].with_user(self.designer).search_read([], ["name"])

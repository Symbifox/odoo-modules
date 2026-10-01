from unittest.mock import patch

import requests

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.bf_bi.models import bf_bi_connection

CLE = "cle-secrete-banc-0123456789"
GET = "odoo.addons.bf_bi.models.bf_bi_connection.requests.get"


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def fake_grist(url, params=None, timeout=None, headers=None):
    """Un Grist minimal : une table Budget, et la clé exigée."""
    if headers.get("Authorization") != "Bearer " + CLE:
        return FakeResponse(401, {"error": "détail interne du serveur"})
    if url.endswith("/tables"):
        return FakeResponse(200, {"tables": [{"id": "Budget"}, {"id": "Table1"}]})
    if url.endswith("/tables/Budget/columns"):
        return FakeResponse(200, {"columns": [
            {"id": "Client", "fields": {"label": "Client", "type": "Text"}},
            {"id": "Mois", "fields": {"label": "Mois", "type": "Date"}},
            {"id": "Budget_heures", "fields": {"label": "Budget (heures)", "type": "Numeric"}},
            {"id": "Etiquettes", "fields": {"label": "Étiquettes", "type": "ChoiceList"}},
            {"id": "gristHelper_Display", "fields": {"type": "Any"}},
            {"id": "manualSort", "fields": {"type": "ManualSortPos"}},
        ]})
    if url.endswith("/tables/Budget/records"):
        limit = params["limit"]
        records = [
            {"id": i, "fields": {"Client": "Client %s" % i, "Mois": 1767225600, "Budget_heures": 10 * i,
                                 "Etiquettes": ["L", "OBNL", "prioritaire"], "gristHelper_Display": "x",
                                 "manualSort": i}}
            for i in range(1, 4)
        ]
        return FakeResponse(200, {"records": records[:limit]})
    return FakeResponse(404, {})


@tagged("post_install", "-at_install")
class TestBfBiConnection(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Connection = cls.env["bf.bi.connection"]
        cls.g_finance = cls.env["res.groups"].create({"name": "Banc BI : finances"})
        cls.reader = new_test_user(cls.env, "bi-lecteur", groups="base.group_user")
        cls.finance = new_test_user(cls.env, "bi-finance", groups="base.group_user")
        cls.finance.groups_id = [(4, cls.g_finance.id)]
        cls.designer = new_test_user(cls.env, "bi-concepteur-src", groups="base.group_user,bf_bi.group_bi_designer")
        cls.portal = new_test_user(cls.env, "bi-portail", groups="base.group_portal")
        cls.connection = cls.Connection.create({
            "name": "Budgets", "code": "essai_budgets", "kind": "grist",
            "grist_url": "https://grist.banc.test", "grist_doc_id": "doc123", "grist_api_key": CLE,
        })

    def setUp(self):
        super().setUp()
        bf_bi_connection._CACHE.clear()
        self.patcher = patch(GET, side_effect=fake_grist)
        self.get = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def fetch(self, user, code="essai_budgets", table="Budget"):
        return self.Connection.with_user(user).bf_fetch_table(code, table)

    # -- portée ---------------------------------------------------------------

    def test_new_connection_is_closed_to_everyone(self):
        self.assertFalse(self.connection.is_open)
        for user in (self.reader, self.finance, self.designer):
            with self.assertRaises(AccessError):
                self.fetch(user)
        self.assertFalse(self.get.called, "Grist ne doit pas être appelé pour quelqu'un sans droit")

    def test_group_opens_the_connection_to_its_members_only(self):
        self.connection.group_ids = [(6, 0, [self.g_finance.id])]
        data = self.fetch(self.finance)
        self.assertEqual(data["header"], ["Client", "Mois", "Budget (heures)", "Étiquettes"])
        with self.assertRaises(AccessError):
            self.fetch(self.reader)

    def test_named_person_can_use_it(self):
        self.connection.user_ids = [(6, 0, [self.reader.id])]
        self.assertTrue(self.fetch(self.reader)["rows"])

    def test_portal_user_is_refused_even_if_named(self):
        self.connection.user_ids = [(6, 0, [self.portal.id])]
        with self.assertRaises(AccessError):
            self.fetch(self.portal)

    def test_unknown_code_and_refused_code_look_the_same(self):
        with self.assertRaises(AccessError) as inconnu:
            self.fetch(self.reader, code="nexiste_pas")
        with self.assertRaises(AccessError) as refuse:
            self.fetch(self.reader)
        self.assertEqual(str(inconnu.exception).replace("nexiste_pas", "X"),
                         str(refuse.exception).replace("essai_budgets", "X"))

    def test_other_company_connection_is_refused(self):
        autre = self.env["res.company"].create({"name": "Autre société banc"})
        self.connection.write({"company_id": autre.id, "user_ids": [(6, 0, [self.reader.id])]})
        with self.assertRaises(AccessError):
            self.fetch(self.reader)

    def test_list_usable_follows_access_and_carries_no_secret(self):
        def mine():
            # Seulement la connexion de l'essai : la base peut en porter d'autres.
            return [c for c in self.Connection.with_user(self.designer).bf_list_usable()
                    if c["code"] == "essai_budgets"]
        self.assertEqual(mine(), [])
        self.connection.user_ids = [(6, 0, [self.designer.id])]
        self.assertEqual(mine(), [{"code": "essai_budgets", "name": "Budgets", "kind": "grist"}])
        self.assertNotIn(CLE, repr(self.Connection.with_user(self.designer).bf_list_usable()))

    def test_list_tables_is_checked_too(self):
        with self.assertRaises(AccessError):
            self.Connection.with_user(self.designer).bf_list_tables("essai_budgets")
        self.connection.user_ids = [(6, 0, [self.designer.id])]
        self.assertEqual(self.Connection.with_user(self.designer).bf_list_tables("essai_budgets"), ["Budget", "Table1"])

    # -- secrets --------------------------------------------------------------

    def test_key_is_unreadable_outside_administrators(self):
        with self.assertRaises(AccessError):
            self.connection.with_user(self.designer).read(["grist_api_key"])
        self.connection.user_ids = [(6, 0, [self.reader.id])]
        self.assertNotIn(CLE, repr(self.fetch(self.reader)))

    def test_grist_error_body_is_not_passed_on(self):
        self.connection.sudo().grist_api_key = "mauvaise"
        self.connection.user_ids = [(6, 0, [self.reader.id])]
        with self.assertRaises(UserError) as err:
            self.fetch(self.reader)
        self.assertIn("401", str(err.exception))
        self.assertNotIn("détail interne", str(err.exception))

    def test_unreachable_source_says_so_without_details(self):
        self.get.side_effect = requests.ConnectionError("hôte interne grist.interne.test refusé")
        self.connection.user_ids = [(6, 0, [self.reader.id])]
        with self.assertRaises(UserError) as err:
            self.fetch(self.reader)
        self.assertNotIn("grist.interne.test", str(err.exception))

    # -- valeurs --------------------------------------------------------------

    def test_values_are_converted_for_the_spreadsheet(self):
        self.connection.user_ids = [(6, 0, [self.reader.id])]
        data = self.fetch(self.reader)
        premiere = data["rows"][0]
        self.assertEqual(premiere[0], "Client 1")
        self.assertEqual(premiere[1], 46023)  # 2026-01-01 en serial de tableur
        self.assertEqual(premiere[2], 10)
        self.assertEqual(premiere[3], "OBNL, prioritaire")
        self.assertEqual(len(premiere), 4, "colonnes internes de Grist écartées")
        self.assertFalse(data["truncated"])

    def test_row_limit_truncates_and_says_so(self):
        self.connection.write({"row_limit": 2, "user_ids": [(6, 0, [self.reader.id])]})
        data = self.fetch(self.reader)
        self.assertEqual(len(data["rows"]), 2)
        self.assertTrue(data["truncated"])

    def test_cache_serves_repeat_reads_and_resets_when_connection_changes(self):
        self.connection.user_ids = [(6, 0, [self.reader.id, self.finance.id])]
        self.fetch(self.reader)
        appels = self.get.call_count
        self.fetch(self.finance)
        self.assertEqual(self.get.call_count, appels, "deuxième lecture servie par le cache")
        self.connection.row_limit = 1
        self.assertEqual(len(self.fetch(self.reader)["rows"]), 1)

    def test_cache_does_not_bypass_access(self):
        self.connection.user_ids = [(6, 0, [self.finance.id])]
        self.fetch(self.finance)
        with self.assertRaises(AccessError):
            self.fetch(self.reader)

    # -- saisie -----------------------------------------------------------------

    def test_code_and_url_are_validated(self):
        with self.assertRaises(ValidationError):
            self.Connection.create({"name": "x", "code": "Pas Bon!", "grist_url": "https://a.b"})
        with self.assertRaises(ValidationError):
            self.Connection.create({"name": "x", "code": "ok_code", "grist_url": "file:///etc/passwd"})


    # -- revue de sécurité ---------------------------------------------------------------

    def test_only_administrators_test_a_connection(self):
        with self.assertRaises(AccessError):
            self.connection.with_user(self.finance).action_test_connection()
        self.assertEqual(self.get.call_count, 0, "Grist ne doit pas être appelé")

    def test_connection_follows_the_active_companies(self):
        autre = self.env["res.company"].create({"name": "Société active banc"})
        self.reader.company_ids = [(4, autre.id)]
        self.connection.write({"company_id": autre.id, "user_ids": [(6, 0, [self.reader.id])]})
        Connection = self.Connection.with_user(self.reader)
        with self.assertRaises(AccessError):  # permise mais pas active
            Connection.with_context(allowed_company_ids=[self.reader.company_id.id]).bf_fetch_table(
                "essai_budgets", "Budget")
        self.assertTrue(Connection.with_context(allowed_company_ids=[autre.id]).bf_fetch_table(
            "essai_budgets", "Budget")["rows"])

    def test_forged_company_context_is_refused(self):
        """La personne n'est PAS membre de l'autre société : la demander dans le contexte
        (que le navigateur fournit) ne lui en ouvre pas les connexions."""
        autre = self.env["res.company"].create({"name": "Société forgée banc"})
        self.connection.write({"company_id": autre.id, "user_ids": [(6, 0, [self.reader.id])]})
        forged = self.Connection.with_user(self.reader).with_context(allowed_company_ids=[autre.id])
        with self.assertRaises(AccessError):
            forged.bf_fetch_table("essai_budgets", "Budget")
        try:
            listed = [c["code"] for c in forged.bf_list_usable()]
        except AccessError:
            listed = []
        self.assertNotIn("essai_budgets", listed)

    def test_cache_is_capped(self):
        self.connection.user_ids = [(6, 0, [self.reader.id])]
        for i in range(bf_bi_connection.CACHE_MAX + 20):
            # Entrées FRAÎCHES : des périmées seraient balayées avant le plafond.
            bf_bi_connection._CACHE[("autre_base", i, "x", "t")] = (bf_bi_connection.time.monotonic(), {})
        self.fetch(self.reader)
        self.assertLessEqual(len(bf_bi_connection._CACHE), bf_bi_connection.CACHE_MAX)

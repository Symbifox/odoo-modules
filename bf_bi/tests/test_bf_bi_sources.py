"""Types de connexion : fichier déposé, API web, PostgreSQL.

La portée (fermée par défaut, groupes, sociétés) est commune à tous les types et déjà
couverte par test_bf_bi_connection ; ici, ce que chaque type lit, refuse et cache.
"""
import base64
import datetime
import io
import json
import unittest
from unittest.mock import patch

import psycopg2
import requests

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, common as odoo_common, new_test_user, tagged
from odoo.tools import config

from odoo.addons.bf_bi.models import bf_bi_connection, bf_bi_tables

HTTP = "odoo.addons.bf_bi.models.bf_bi_network.http_get"
SECRET = "jeton-secret-de-banc-0123"


def xlsx(sheets):
    import openpyxl
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


def sheet_xml(body, old, new):
    """Le classeur `body`, le XML de ses feuilles réécrit (`old` → `new`, qui doit y être)."""
    import zipfile
    src, out = zipfile.ZipFile(io.BytesIO(body)), io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename.startswith("xl/worksheets/"):
                assert old in data, old
                data = data.replace(old, new)
            dst.writestr(item, data)
    return out.getvalue()


def b64(data):
    return base64.b64encode(data).decode()


class _Base(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Messages lus en anglais (texte source), même sur une base en fr_CA.
        cls.env = cls.env(context=dict(cls.env.context, lang="en_US"))
        cls.admin = cls.env.ref("base.user_admin")
        cls.Connection = cls.env["bf.bi.connection"]
        cls.reader = new_test_user(cls.env, "bi-lecteur-src", groups="base.group_user")
        cls.designer = new_test_user(cls.env, "bi-concepteur-sources", groups="base.group_user,bf_bi.group_bi_designer")

    def setUp(self):
        super().setUp()
        bf_bi_connection._CACHE.clear()

    def fetch(self, code, table, user=None):
        return self.Connection.with_user(user or self.reader).bf_fetch_table(code, table)


# ----------------------------------------------------------------------
# Fichier déposé
# ----------------------------------------------------------------------

@tagged("post_install", "-at_install")
class TestBfBiFileSource(_Base):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workbook = xlsx({
            "Budget": [
                ["Client", "Mois", "Heures", "Payé", "Note"],
                ["Alpha", datetime.datetime(2026, 1, 1), 12.5, True, "ok"],
                [None, None, None, None, None],  # ligne vide : sautée
                ["Bêta", datetime.datetime(2026, 2, 1), 7, False, "#DIV/0!"],
            ],
            "Journal": [["Quand", "Quoi"], [datetime.datetime(2026, 3, 4, 10, 30), "appel"]],
        })
        cls.connection = cls.Connection.create({
            "name": "Classeur", "code": "essai_fichier", "kind": "file",
            "file_data": b64(cls.workbook), "file_name": "budget.xlsx",
            "user_ids": [(6, 0, [cls.reader.id])],
        })

    def test_each_sheet_is_a_table(self):
        self.assertEqual(self.Connection.with_user(self.reader).bf_list_tables("essai_fichier"), ["Budget", "Journal"])

    def test_values_and_types(self):
        data = self.fetch("essai_fichier", "Budget")
        self.assertEqual(data["header"], ["Client", "Mois", "Heures", "Payé", "Note"])
        self.assertEqual(data["types"], ["Text", "Date", "Numeric", "Bool", "Text"])
        self.assertEqual(data["rows"][0], ["Alpha", 46023, 12.5, True, "ok"])
        self.assertEqual(data["rows"][1], ["Bêta", 46054, 7, False, "#DIV/0!"])
        self.assertEqual(len(data["rows"]), 2, "ligne vide sautée")
        journal = self.fetch("essai_fichier", "Journal")
        self.assertEqual(journal["types"], ["DateTime", "Text"])
        self.assertAlmostEqual(journal["rows"][0][0], 46085 + 10.5 / 24, places=6)

    def test_unknown_sheet_is_refused_cleanly(self):
        with self.assertRaises(UserError):
            self.fetch("essai_fichier", "Autre")

    def test_row_limit(self):
        self.connection.row_limit = 1
        data = self.fetch("essai_fichier", "Budget")
        self.assertEqual(len(data["rows"]), 1)
        self.assertTrue(data["truncated"])

    def test_csv_with_semicolons_and_decimal_commas(self):
        csv_body = "Client;Montant;Date\nAlpha;12,5;2026-01-01\nBêta;3;2026-01-02\n".encode("cp1252")
        self.connection.write({"file_data": b64(csv_body), "file_name": "export.csv"})
        self.assertEqual(self.Connection.with_user(self.reader).bf_list_tables("essai_fichier"), ["Data"])
        data = self.fetch("essai_fichier", "Data")
        self.assertEqual(data["types"], ["Text", "Numeric", "Date"])
        self.assertEqual(data["rows"], [["Alpha", 12.5, 46023], ["Bêta", 3, 46024]])
        with self.assertRaises(UserError):
            self.fetch("essai_fichier", "Budget")

    def test_new_upload_is_served_at_once(self):
        self.fetch("essai_fichier", "Budget")
        self.connection.write({"file_data": b64(xlsx({"Budget": [["X"], [1]]})), "file_name": "b.xlsx"})
        self.assertEqual(self.fetch("essai_fichier", "Budget")["header"], ["X"])

    def test_bad_files_are_refused_at_upload(self):
        with self.assertRaises(ValidationError):
            self.connection.write({"file_data": b64(b"PK\x03\x04 pas un classeur"), "file_name": "x.xlsx"})
        with self.assertRaises(ValidationError):
            self.connection.write({"file_data": b64(self.workbook), "file_name": "vieux.xls"})
        with patch.object(bf_bi_connection, "MAX_FILE", 100), self.assertRaises(ValidationError):
            self.connection.write({"file_data": b64(self.workbook), "file_name": "b.xlsx"})

    def test_zip_bomb_is_refused(self):
        with patch.object(bf_bi_tables, "MAX_UNZIPPED", 1000), self.assertRaises(ValidationError):
            self.connection.write({"file_data": b64(self.workbook), "file_name": "b.xlsx"})
        # Avant de décompresser quoi que ce soit : le nombre d'entrées (que la mesure ne compte
        # pas) et les tailles déclarées refusent déjà.
        with patch.object(bf_bi_tables, "MAX_ZIP_ENTRIES", 3), self.assertRaises(ValidationError):
            self.connection.write({"file_data": b64(self.workbook), "file_name": "b.xlsx"})
        with patch.object(bf_bi_tables, "MAX_UNZIPPED", 1000), self.assertRaises(bf_bi_tables.SourceFormatError):
            bf_bi_tables._check_zip(self.workbook)

    def test_unzipped_size_is_measured_not_trusted(self):
        # Une archive peut déclarer de petites tailles : le dépôt décompresse et compte.
        with patch.object(bf_bi_tables, "_check_zip", lambda body: None), \
                patch.object(bf_bi_tables, "MAX_UNZIPPED", 1000), self.assertRaises(ValidationError):
            self.connection.write({"file_data": b64(self.workbook), "file_name": "b.xlsx"})

    def test_a_rewritten_attachment_is_measured_at_read_time(self):
        # Un administrateur peut réécrire la pièce jointe sans passer par la connexion (ni sa
        # contrainte) : la lecture mesure de nouveau.
        attachment = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "bf.bi.connection"), ("res_id", "=", self.connection.id), ("res_field", "=", "file_data")])
        self.assertTrue(attachment)
        attachment.write({"datas": b64(xlsx({"Budget": [["Gros"], ["x" * 5000]]}))})
        self.connection.invalidate_recordset()
        # Taille déclarée NON consultée : seule la mesure en décompressant peut refuser.
        with patch.object(bf_bi_tables, "_check_zip", lambda body: None), \
                patch.object(bf_bi_tables, "MAX_UNZIPPED", 3000), self.assertRaises(UserError):
            self.fetch("essai_fichier", "Budget")

    def test_forged_sheet_dimensions_are_ignored(self):
        import time
        body = xlsx({"Budget": [["Client"], ["Alpha"]]})
        # Trop grande, elle ne fait pas parcourir un million de lignes ; trop petite (fréquent
        # dans les exports), openpyxl s'arrêterait à la dernière ligne déclarée et perdrait Alpha.
        for declared in (b"A1:XFD1048576", b"A1:A1"):
            with self.subTest(declared=declared):
                forged = sheet_xml(body, b'<dimension ref="A1:A2"/>', b'<dimension ref="%s"/>' % declared)
                self.connection.write({"file_data": b64(forged), "file_name": "b.xlsx"})
                debut = time.monotonic()
                data = self.fetch("essai_fichier", "Budget")
                self.assertLess(time.monotonic() - debut, 5)
                self.assertEqual(data["header"], ["Client"])
                self.assertEqual(data["rows"], [["Alpha"]])

    def test_values_computed_by_excel_are_read(self):
        # Une cellule à formule porte la formule et la valeur qu'Excel a calculée : on lit la
        # valeur (openpyxl n'écrit pas de valeur calculée, on la pose dans le XML).
        body = sheet_xml(xlsx({"Budget": [["Total"], ["=1+1"]]}), b"<f>1+1</f><v></v>", b"<f>1+1</f><v>2</v>")
        self.connection.write({"file_data": b64(body), "file_name": "b.xlsx"})
        data = self.fetch("essai_fichier", "Budget")
        self.assertEqual((data["types"], data["rows"]), (["Numeric"], [[2]]))

    def test_columns_and_scanned_rows_are_capped(self):
        wide = ["c%d" % i for i in range(bf_bi_tables.MAX_COLUMNS + 50)]
        self.connection.write({"file_data": b64(xlsx({"Budget": [wide, list(range(len(wide)))]})), "file_name": "b.xlsx"})
        self.assertEqual(len(self.fetch("essai_fichier", "Budget")["header"]), bf_bi_tables.MAX_COLUMNS)
        # Au-delà du plafond, rien n'est lu, même pas pour juger qu'une ligne est vide.
        far = [["Client"], [None, None, None, "loin"], ["Alpha"]]
        self.connection.write({"file_data": b64(xlsx({"Budget": far})), "file_name": "b.xlsx"})
        with patch.object(bf_bi_tables, "MAX_COLUMNS", 3):
            self.assertEqual(self.fetch("essai_fichier", "Budget")["rows"], [["Alpha"]])
        bf_bi_connection._CACHE.clear()
        rows = [["Client"]] + [[None]] * 5 + [["Alpha"], ["Bêta"]]
        self.connection.write({"file_data": b64(xlsx({"Budget": rows})), "file_name": "b.xlsx"})
        with patch.object(bf_bi_tables, "MAX_SCANNED_ROWS", 4):
            self.assertEqual(self.fetch("essai_fichier", "Budget")["rows"], [])
        bf_bi_connection._CACHE.clear()
        self.assertEqual(self.fetch("essai_fichier", "Budget")["rows"], [["Alpha"], ["Bêta"]])

    def test_the_file_is_admin_only(self):
        with self.assertRaises(AccessError):
            self.connection.with_user(self.designer).read(["file_data"])


# ----------------------------------------------------------------------
# API web
# ----------------------------------------------------------------------

def answer(payload, status=200, ctype="application/json"):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return status, body, ctype


@tagged("post_install", "-at_install")
class TestBfBiWebSource(_Base):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.connection = cls.Connection.create({
            "name": "API", "code": "essai_web", "kind": "web", "web_url": "https://api.banc.test/v1/",
            "user_ids": [(6, 0, [cls.reader.id])],
            "table_ids": [(0, 0, {"name": "Clients", "request": "/clients?actifs=1", "records_path": "result.records"}),
                          (0, 0, {"name": "Grille", "request": "values", "records_path": "values"}),
                          (0, 0, {"name": "Export", "request": "export.csv", "fmt": "csv"})],
        })

    def setUp(self):
        super().setUp()
        self.answers = {
            "https://api.banc.test/v1/clients?actifs=1": answer({"result": {"records": [
                {"nom": "Alpha", "depuis": "2026-01-01", "vu": "2026-01-01T12:00:00Z", "budget": 10,
                 "actif": True, "contact": {"nom": "Ana", "ville": {"nom": "Sherbrooke"}}, "tags": ["a", "b"]},
                {"nom": "Bêta", "depuis": None, "vu": "2026-01-02T00:00:00+00:00", "budget": 2.5,
                 "actif": False, "contact": {"nom": "Bo"}, "tags": [], "extra": "x"},
            ]}}),
            "https://api.banc.test/v1/values": answer({"values": [["Mois", "Total"], ["2026-01", "12"], ["2026-02", "15"]]}),
            "https://api.banc.test/v1/export.csv": answer(b"a,b\n1,x\n2,y\n", ctype="text/csv"),
        }
        self.calls = []

        def fake(url, params=None, headers=None, secret_bound=False, max_bytes=None):
            self.calls.append({"url": url, "params": params, "headers": headers, "secret_bound": secret_bound})
            return self.answers.get(url, answer({"détail": "chemin interne /srv/x"}, status=404))

        patcher = patch(HTTP, side_effect=fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_records_are_flattened_and_typed(self):
        data = self.fetch("essai_web", "Clients")
        self.assertEqual(data["header"], ["nom", "depuis", "vu", "budget", "actif", "contact.nom",
                                          "contact.ville.nom", "tags", "extra"])
        self.assertEqual(data["types"], ["Text", "Date", "DateTime", "Numeric", "Bool", "Text", "Text", "Text", "Text"])
        self.assertEqual(data["rows"][0][:2], ["Alpha", 46023])
        self.assertAlmostEqual(data["rows"][0][2], 46023.5)
        self.assertEqual(data["rows"][0][5:], ["Ana", "Sherbrooke", "a, b", None])
        self.assertEqual(data["rows"][1][1], None)
        self.assertEqual(data["rows"][1][8], "x")

    def test_very_wide_answers_are_capped(self):
        self.answers["https://api.banc.test/v1/values"] = answer(
            {"values": [["c%d" % i for i in range(bf_bi_tables.MAX_COLUMNS + 100)], list(range(bf_bi_tables.MAX_COLUMNS + 100))]})
        data = self.fetch("essai_web", "Grille")
        self.assertEqual(len(data["header"]), bf_bi_tables.MAX_COLUMNS)
        self.assertEqual(len(data["rows"][0]), bf_bi_tables.MAX_COLUMNS)

    def test_list_of_lists_takes_its_first_row_as_header(self):
        data = self.fetch("essai_web", "Grille")
        self.assertEqual(data["header"], ["Mois", "Total"])
        self.assertEqual(data["types"], ["Text", "Numeric"])
        self.assertEqual(data["rows"], [["2026-01", 12], ["2026-02", 15]])

    def test_csv_table(self):
        self.assertEqual(self.fetch("essai_web", "Export")["rows"], [[1, "x"], [2, "y"]])

    def test_decimal_comma_only_in_a_semicolon_csv(self):
        # Hors du CSV à point-virgule (convention française), « 12,5 » n'est pas un nombre.
        self.answers["https://api.banc.test/v1/export.csv"] = answer(
            b"Client\tMontant\nAlpha\t12,5\nBeta\t3\n", ctype="text/csv")
        data = self.fetch("essai_web", "Export")
        self.assertEqual(data["types"], ["Text", "Text"])
        self.assertEqual(data["rows"], [["Alpha", "12,5"], ["Beta", "3"]])

    def test_nan_and_infinity_become_empty_cells(self):
        # json les admet ; rendus tels quels, ils casseraient le JSON envoyé au navigateur.
        self.answers["https://api.banc.test/v1/values"] = answer(
            b'{"values": [{"v": 1.5}, {"v": NaN}, {"v": -Infinity}]}')
        data = self.fetch("essai_web", "Grille")
        self.assertEqual((data["types"], data["rows"]), (["Numeric"], [[1.5], [None], [None]]))
        json.dumps(data, allow_nan=False)

    def test_a_column_is_typed_only_if_every_value_is(self):
        self.answers["https://api.banc.test/v1/values"] = answer(
            {"values": [{"oui": True, "n": 1}, {"oui": "peut-être", "n": "beaucoup"}]})
        data = self.fetch("essai_web", "Grille")
        self.assertEqual(data["types"], ["Text", "Text"])
        self.assertEqual(data["rows"], [["True", "1"], ["peut-être", "beaucoup"]])

    def test_tables_are_the_defined_ones_only(self):
        self.assertEqual(self.Connection.with_user(self.reader).bf_list_tables("essai_web"),
                         ["Clients", "Grille", "Export"])
        with self.assertRaises(UserError):
            self.fetch("essai_web", "../../admin")
        self.assertEqual(self.calls, [], "aucun appel pour une table non définie")

    def test_authentication_modes(self):
        connection = self.connection.sudo()
        cases = [
            ({"web_auth": "bearer"}, lambda c: c["headers"]["Authorization"] == "Bearer " + SECRET),
            ({"web_auth": "header", "web_auth_name": "X-API-Key"}, lambda c: c["headers"]["X-API-Key"] == SECRET),
            ({"web_auth": "query", "web_auth_name": "api_key"}, lambda c: c["params"] == {"api_key": SECRET}),
            ({"web_auth": "basic", "web_username": "u"},
             lambda c: c["headers"]["Authorization"] == "Basic " + base64.b64encode(b"u:" + SECRET.encode()).decode()),
        ]
        for vals, check in cases:
            with self.subTest(auth=vals["web_auth"]):
                connection.write(dict(vals, web_secret=SECRET))
                self.calls.clear()
                data = self.fetch("essai_web", "Grille")
                self.assertTrue(check(self.calls[0]))
                self.assertTrue(self.calls[0]["secret_bound"], "un secret interdit la redirection vers un autre hôte")
                self.assertNotIn(SECRET, json.dumps(data))
        connection.write({"web_auth": "none"})
        self.calls.clear()
        self.fetch("essai_web", "Grille")
        self.assertFalse(self.calls[0]["secret_bound"])
        self.assertNotIn("Authorization", self.calls[0]["headers"])

    def test_missing_secret_is_said_before_calling(self):
        self.connection.sudo().write({"web_auth": "bearer", "web_secret": False})
        with self.assertRaises(UserError):
            self.fetch("essai_web", "Grille")
        self.assertEqual(self.calls, [])

    def test_errors_never_pass_the_body_on(self):
        self.connection.sudo().table_ids[0].request = "/inconnu"
        with self.assertRaises(UserError) as err:
            self.fetch("essai_web", "Clients")
        self.assertIn("404", str(err.exception))
        self.assertNotIn("/srv/x", str(err.exception))

    def test_wrong_records_path_says_so(self):
        self.connection.sudo().table_ids[0].records_path = "result.nope"
        with self.assertRaises(UserError) as err:
            self.fetch("essai_web", "Clients")
        self.assertIn("records path", str(err.exception))

    def test_table_change_is_served_at_once(self):
        self.fetch("essai_web", "Clients")
        self.connection.sudo().table_ids[0].write({"request": "values", "records_path": "values"})
        self.assertEqual(self.fetch("essai_web", "Clients")["header"], ["Mois", "Total"])

    def test_unexpected_failures_reach_the_reader_as_a_plain_message(self):
        self.answers["https://api.banc.test/v1/values"] = answer(b"[" * 100000 + b"]" * 100000)
        with self.assertRaises(UserError):
            self.fetch("essai_web", "Grille")

    def test_errors_of_odoo_own_database_are_not_swallowed(self):
        # Le mécanisme de reprise d'Odoo (conflit de mise à jour) doit les recevoir telles quelles.
        Connection = type(self.Connection)
        with patch.object(Connection, "_web_fetch", side_effect=psycopg2.errors.SerializationFailure("conflit")), \
                self.assertRaises(psycopg2.errors.SerializationFailure):
            self.fetch("essai_web", "Grille")

    def test_key_names_that_would_change_the_request_are_refused(self):
        connection = self.connection.sudo()
        for name in ("Host", "content-length", "Transfer-Encoding", "X Key", "X-Key:1"):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                connection.write({"web_auth": "header", "web_auth_name": name, "web_secret": SECRET})
        connection.write({"web_auth": "header", "web_auth_name": "X-API-Key", "web_secret": SECRET})
        with self.assertRaises(ValidationError):
            connection.write({"web_auth": "query", "web_auth_name": "api key&x=1"})
        connection.write({"web_auth": "query", "web_auth_name": "api_key"})

    def test_paths_cannot_leave_the_base_address(self):
        for request in ("https://ailleurs.test/x", "//ailleurs.test/x", "a b", "\\\\ailleurs"):
            with self.subTest(request=request), self.assertRaises(ValidationError):
                self.connection.sudo().table_ids[0].request = request

    def test_tables_and_secret_are_admin_only(self):
        with self.assertRaises(AccessError):
            self.env["bf.bi.connection.table"].with_user(self.designer).search([])
        self.connection.sudo().web_secret = SECRET
        with self.assertRaises(AccessError):
            self.connection.with_user(self.designer).read(["web_secret"])
        self.assertNotIn(SECRET, repr(self.Connection.with_user(self.reader).bf_list_usable()))

    def test_closed_connection_calls_nothing(self):
        self.connection.user_ids = [(5, 0, 0)]
        with self.assertRaises(AccessError):
            self.fetch("essai_web", "Clients")
        self.assertEqual(self.calls, [])

    def test_admin_test_runs_every_table(self):
        result = self.connection.with_user(self.admin).action_test_connection()
        self.assertEqual(result["params"]["type"], "success")
        self.assertEqual(len(self.calls), 3)
        with self.assertRaises(AccessError):
            self.connection.with_user(self.reader).action_test_connection()


@tagged("post_install", "-at_install")
class TestBfBiGuardOnEveryKind(_Base):
    """Sans remplacer la sortie réseau : la garde refuse une adresse interne, quel que soit le type."""

    def setUp(self):
        super().setUp()
        # Le bloqueur du cadre d'essai d'Odoo répondrait avant la garde : on le retire.
        unblock = patch.object(requests.sessions.Session, "send", odoo_common._super_send)
        unblock.start()
        self.addCleanup(unblock.stop)

    def test_web_and_grist_to_an_internal_address_are_refused(self):
        web = self.Connection.create({
            "name": "Interne", "code": "essai_interne", "kind": "web", "web_url": "http://169.254.169.254/latest",
            "user_ids": [(6, 0, [self.reader.id])], "table_ids": [(0, 0, {"name": "Meta", "request": "meta-data"})]})
        grist = self.Connection.create({
            "name": "Grist interne", "code": "essai_grist_interne", "kind": "grist",
            "grist_url": "http://127.0.0.1:8069", "grist_doc_id": "d", "grist_api_key": "k",
            "user_ids": [(6, 0, [self.reader.id])]})
        for code, table in (("essai_interne", "Meta"), ("essai_grist_interne", "T")):
            with self.subTest(code=code), self.assertRaises(UserError) as err:
                self.fetch(code, table)
            self.assertIn("not allowed", str(err.exception))
        self.assertTrue(web and grist)

    def test_database_to_an_internal_address_is_refused(self):
        self.Connection.create({
            "name": "Base interne", "code": "essai_pg_interne", "kind": "postgresql", "db_host": "127.0.0.1",
            "db_name": "x", "db_user": "x", "db_password": "x", "user_ids": [(6, 0, [self.reader.id])],
            "table_ids": [(0, 0, {"name": "T", "request": "SELECT 1"})]})
        with self.assertRaises(UserError) as err:
            self.fetch("essai_pg_interne", "T")
        self.assertIn("not allowed", str(err.exception))


# ----------------------------------------------------------------------
# PostgreSQL : contre le vrai serveur de la base d'essai
# ----------------------------------------------------------------------

@tagged("post_install", "-at_install")
class TestBfBiPostgresSource(_Base):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.host = config.get("db_host")
        if not cls.host or cls.host.startswith("/"):
            raise unittest.SkipTest("serveur PostgreSQL par socket local : rien à joindre en TCP")
        cls.connection = cls.Connection.create({
            "name": "Base", "code": "essai_pg", "kind": "postgresql", "db_host": cls.host,
            "db_port": int(config.get("db_port") or 5432), "db_name": cls.env.cr.dbname,
            "db_user": config.get("db_user"), "db_password": config.get("db_password") or "",
            "db_sslmode": "prefer", "user_ids": [(6, 0, [cls.reader.id])],
            "table_ids": [
                (0, 0, {"name": "Constantes", "request": "SELECT 1 AS n, DATE '2026-01-01' AS d, "
                                                          "'a;b' AS t, 2.5::numeric AS m, true AS v;"}),
                (0, 0, {"name": "Serie", "request": "SELECT generate_series(1, 10) AS n"}),
            ],
        })

    def setUp(self):
        super().setUp()
        allow = patch.dict(config.options, {"bf_bi_allowed_private_hosts": self.host})
        allow.start()
        self.addCleanup(allow.stop)

    def table(self, name, request):
        return self.env["bf.bi.connection.table"].create(
            {"connection_id": self.connection.id, "name": name, "request": request})

    def test_reads_typed_rows(self):
        data = self.fetch("essai_pg", "Constantes")
        self.assertEqual(data["header"], ["n", "d", "t", "m", "v"])
        self.assertEqual(data["types"], ["Numeric", "Date", "Text", "Numeric", "Bool"])
        self.assertEqual(data["rows"], [[1, 46023, "a;b", 2.5, True]])

    def test_row_limit_is_applied_at_the_source(self):
        self.connection.row_limit = 3
        data = self.fetch("essai_pg", "Serie")
        self.assertEqual([r[0] for r in data["rows"]], [1, 2, 3])
        self.assertTrue(data["truncated"])
        # La base ne calcule que les lignes demandées (plafond + 1) : la 5e, qui divise par
        # zéro, n'est jamais évaluée. Tout lire puis couper la ferait tomber.
        self.table("Piege", "SELECT n, 1 / (5 - n) AS v FROM generate_series(1, 6) n")
        self.assertEqual(self.fetch("essai_pg", "Piege")["rows"], [[1, 0], [2, 0], [3, 0]])

    def test_session_is_read_only(self):
        self.table("Seq", "SELECT nextval('res_partner_id_seq')")
        self.table("Efface", "WITH d AS (DELETE FROM res_country WHERE false RETURNING id) SELECT * FROM d")
        for name in ("Seq", "Efface"):
            with self.subTest(name=name), self.assertRaises(UserError):
                self.fetch("essai_pg", name)

    def test_only_one_statement(self):
        for request in ("SELECT 1; DELETE FROM res_country", "SELECT 1; -- ok\n; SELECT 2", ""):
            with self.subTest(request=request), self.assertRaises(ValidationError):
                self.table("Deux", request)
        # Un identifiant qui finit par E n'ouvre pas une chaîne E'…' (PostgreSQL non plus).
        with self.assertRaises(ValidationError):
            self.table("Ident", "SELECT x_E'\\'; DELETE FROM res_country; --'")
        # Un « ; » dans une chaîne, un commentaire ou une balise $ n'est pas une fin de requête.
        self.table("Chaines", "SELECT ';' AS a, $x$;$x$ AS b /* ; */ -- ;\n")
        self.assertEqual(self.fetch("essai_pg", "Chaines")["rows"], [[";", ";"]])
        # La contrainte ne joue qu'à l'écriture de la requête (une connexion passée du web à la
        # base ne la rejoue pas) : la lecture contrôle de nouveau, avant de se connecter.
        contournee = self.table("Contournee", "SELECT 1")
        self.env.cr.execute("UPDATE bf_bi_connection_table SET request = %s WHERE id = %s",
                            ("SELECT 1; SELECT 2", contournee.id))
        contournee.invalidate_recordset()
        with patch.object(bf_bi_connection, "_pg_connect") as connect, self.assertRaises(UserError):
            self.fetch("essai_pg", "Contournee")
        connect.assert_not_called()

    def test_statement_timeout(self):
        self.table("Lente", "SELECT pg_sleep(5)")
        with patch.object(bf_bi_connection, "DB_TIMEOUT_MS", 300), self.assertRaises(UserError):
            self.fetch("essai_pg", "Lente")

    def test_a_server_that_never_answers_is_cut_client_side(self):
        # statement_timeout est appliqué par le serveur distant ; s'il ne joue pas le jeu,
        # le minuteur côté client coupe le socket.
        import time
        self.table("Dort", "SELECT pg_sleep(30)")
        with patch.object(bf_bi_connection, "DB_TIMEOUT_MS", 600000), \
                patch.object(bf_bi_connection, "DB_TOTAL_TIMEOUT", 1), self.assertRaises(UserError):
            debut = time.monotonic()
            try:
                self.fetch("essai_pg", "Dort")
            finally:
                self.assertLess(time.monotonic() - debut, 8)

    def test_a_server_with_non_standard_strings_is_refused(self):
        real_connect = psycopg2.connect

        class NonStandard:
            def __init__(self, db):
                self._db = db

            def get_parameter_status(self, name):
                return "off" if name == "standard_conforming_strings" else self._db.get_parameter_status(name)

            def __getattr__(self, name):
                return getattr(self._db, name)

        with patch.object(bf_bi_connection, "_pg_connect", lambda **kw: NonStandard(real_connect(**kw))), \
                self.assertRaises(UserError):
            self.fetch("essai_pg", "Constantes")

    def test_failures_stay_vague_for_readers_and_precise_for_the_admin(self):
        self.connection.sudo().db_user = "inexistant_bi_banc"
        with self.assertRaises(UserError) as err:
            self.fetch("essai_pg", "Constantes")
        self.assertNotIn("inexistant_bi_banc", str(err.exception))
        with self.assertRaises(UserError) as admin:
            self.connection.with_user(self.admin).action_test_connection()
        self.assertIn("inexistant_bi_banc", str(admin.exception))

    def test_without_the_server_allowlist_the_internal_database_is_refused(self):
        with patch.dict(config.options, {"bf_bi_allowed_private_hosts": ""}), self.assertRaises(UserError) as err:
            self.fetch("essai_pg", "Constantes")
        self.assertIn("not allowed", str(err.exception))

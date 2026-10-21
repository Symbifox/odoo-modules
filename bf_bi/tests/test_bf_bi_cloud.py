"""Connecteurs nuage : fichier Nextcloud, fichier OneDrive ou SharePoint, Google Sheets, OData.

Les appels réseau sont remplacés (bf_bi_network.http_get et http_post) : on vérifie ce que la
connexion DEMANDE (adresse, en-têtes, mode du secret) et ce qu'elle fait de la réponse. La garde
réseau elle-même est essayée dans test_bf_bi_network, sur un vrai petit serveur.
"""
import base64
import datetime
import json
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlparse

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.bf_bi.models import bf_bi_connection, bf_bi_network as network
from odoo.addons.bf_bi.tests.test_bf_bi_sources import SECRET, _Base, answer, b64, xlsx

GET = "odoo.addons.bf_bi.models.bf_bi_network.http_get"
POST = "odoo.addons.bf_bi.models.bf_bi_network.http_post"
D = datetime.datetime


def ec_pem():
    """Clé privée valide, mais elliptique : Google signe en RS256."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    return ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


def classeur():
    return xlsx({"Factures": [["Date", "Client", "Montant"], [D(2026, 1, 10), "Alpha", 100], [D(2026, 2, 5), "Bêta", 30]]})


class _Cloud(_Base):

    def setUp(self):
        super().setUp()
        bf_bi_connection._TOKENS.clear()
        self.gets, self.posts = [], []
        self.answers, self.tokens = {}, {}

        def fake_get(url, params=None, headers=None, secret_bound=False, max_bytes=None):
            self.gets.append({"url": url, "params": params, "headers": headers, "secret_bound": secret_bound,
                              "max_bytes": max_bytes})
            for prefix, response in self.answers.items():
                if url.startswith(prefix):
                    return response(url, params) if callable(response) else response
            return answer({"error": "détail interne /srv/x"}, status=404)

        def fake_post(url, data, headers=None, max_bytes=None):
            self.posts.append({"url": url, "data": data})
            return self.tokens.get(url, answer({"error": "invalid_client", "secret": "détail"}, status=401))

        for target, fake in ((GET, fake_get), (POST, fake_post)):
            patcher = patch(target, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)


@tagged("post_install", "-at_install")
class TestBfBiNextcloud(_Cloud):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.connection = cls.Connection.create({
            "name": "Nextcloud", "code": "essai_nc", "kind": "nextcloud", "nc_url": "https://cloud.banc.test/",
            "nc_user": "compta bi", "nc_password": SECRET, "nc_path": "/Finances/Budget 2026.xlsx",
            "user_ids": [(6, 0, [cls.reader.id])]})

    def test_reads_the_workbook_by_webdav(self):
        self.answers["https://cloud.banc.test/remote.php/dav/files/compta%20bi/Finances/Budget%202026.xlsx"] = \
            (200, classeur(), "application/octet-stream")
        self.assertEqual(self.Connection.with_user(self.reader).bf_list_tables("essai_nc"), ["Factures"])
        data = self.fetch("essai_nc", "Factures")
        self.assertEqual(data["types"], ["Date", "Text", "Numeric"])
        self.assertEqual(data["rows"][1][1:], ["Bêta", 30])
        call = self.gets[-1]
        self.assertEqual(call["headers"]["Authorization"],
                         "Basic " + base64.b64encode(("compta bi:" + SECRET).encode()).decode())
        self.assertIs(call["secret_bound"], True)  # le mot de passe ne suit aucune redirection
        self.assertEqual(call["max_bytes"], bf_bi_connection.MAX_FILE)

    def test_a_csv_file(self):
        self.connection.nc_path = "export.csv"
        self.answers["https://cloud.banc.test/remote.php/dav/files/compta%20bi/export.csv"] = \
            (200, b"a;b\n1;12,5\n", "text/csv")
        self.assertEqual(self.fetch("essai_nc", "Data")["rows"], [[1, 12.5]])

    def test_errors_and_paths(self):
        with self.assertRaises(UserError) as err:
            self.fetch("essai_nc", "Factures")
        self.assertIn("404", str(err.exception))
        self.assertNotIn("/srv/x", str(err.exception))
        for path in ("/../autre/x.xlsx", "a/../../b.xlsx", "vieux.xls", " ../autre/x.xlsx", "a/.. "):
            with self.subTest(path=path), self.assertRaises(ValidationError):
                self.connection.nc_path = path
        with self.assertRaises(ValidationError):
            self.connection.nc_url = "ftp://cloud.banc.test"

    def test_incomplete_and_closed(self):
        self.connection.sudo().nc_password = False
        with self.assertRaises(UserError):
            self.fetch("essai_nc", "Factures")
        self.assertEqual(self.gets, [], "rien n'est appelé sans le mot de passe")


@tagged("post_install", "-at_install")
class TestBfBiMicrosoft(_Cloud):
    SHARE = "https://banc.sharepoint.com/:x:/s/Finances/EabcDEF?e=xyz"
    TOKEN_URL = "https://login.microsoftonline.com/banc.onmicrosoft.com/oauth2/v2.0/token"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.connection = cls.Connection.create({
            "name": "SharePoint", "code": "essai_ms", "kind": "microsoft", "ms_tenant": "banc.onmicrosoft.com",
            "ms_client_id": "11111111-2222-3333-4444-555555555555", "ms_client_secret": SECRET,
            "ms_share_url": cls.SHARE, "user_ids": [(6, 0, [cls.reader.id])]})

    def setUp(self):
        super().setUp()
        share = "u!" + base64.urlsafe_b64encode(self.SHARE.encode()).decode().rstrip("=")
        self.content = "https://graph.microsoft.com/v1.0/shares/%s/driveItem/content" % share
        self.tokens[self.TOKEN_URL] = answer({"access_token": "jeton-graph", "expires_in": 3599})
        self.answers[self.content] = (200, classeur(), "application/octet-stream")

    def test_client_credentials_then_the_shared_file(self):
        data = self.fetch("essai_ms", "Factures")
        self.assertEqual(data["rows"][0][1:], ["Alpha", 100])
        self.assertEqual(self.posts[0]["data"], {
            "client_id": "11111111-2222-3333-4444-555555555555", "client_secret": SECRET,
            "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"})
        call = self.gets[-1]
        self.assertEqual(call["url"], self.content)
        self.assertEqual(call["headers"]["Authorization"], "Bearer jeton-graph")
        # Graph renvoie vers une adresse de téléchargement déjà signée : suivie, mais sans le jeton.
        self.assertEqual(call["secret_bound"], network.STRIP)
        self.assertNotIn("=", call["url"].split("/shares/")[1].split("/")[0], "base64url sans remplissage")

    def test_the_token_is_kept_until_it_expires(self):
        self.fetch("essai_ms", "Factures")
        bf_bi_connection._CACHE.clear()
        self.Connection.with_user(self.reader).bf_list_tables("essai_ms")
        self.assertEqual(len(self.posts), 1)
        # Une minute avant son échéance, le jeton est refait.
        later = bf_bi_connection.time.monotonic() + 3599 - 59
        with patch.object(bf_bi_connection.time, "monotonic", return_value=later):
            bf_bi_connection._CACHE.clear()
            self.fetch("essai_ms", "Factures")
        self.assertEqual(len(self.posts), 2)
        self.connection.ms_client_secret = "autre"  # une connexion changée refait son jeton
        bf_bi_connection._CACHE.clear()
        self.fetch("essai_ms", "Factures")
        self.assertEqual(len(self.posts), 3)

    def test_an_old_xls_workbook_says_so(self):
        self.answers[self.content] = (200, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64, "application/octet-stream")
        with self.assertRaises(UserError) as err:
            self.fetch("essai_ms", "Factures")
        self.assertIn(".xls", str(err.exception))

    def test_a_refused_secret_says_so_without_the_answer(self):
        self.tokens.clear()
        with self.assertRaises(UserError) as err:
            self.fetch("essai_ms", "Factures")
        self.assertIn("401", str(err.exception))
        for leak in ("invalid_client", "détail", SECRET):
            self.assertNotIn(leak, str(err.exception))
        self.assertEqual(self.gets, [], "Graph n'est pas appelé sans jeton")

    def test_a_shared_csv_file(self):
        self.answers[self.content] = (200, b"a,b\n1,x\n", "application/octet-stream")
        self.assertEqual(self.Connection.with_user(self.reader).bf_list_tables("essai_ms"), ["Data"])
        self.assertEqual(self.fetch("essai_ms", "Data")["rows"], [[1, "x"]])

    def test_identifiers_are_checked(self):
        for field, value in (("ms_client_id", "pas-un-guid"), ("ms_tenant", "banc/../x"), ("ms_share_url", "javascript:x")):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.connection[field] = value


@tagged("post_install", "-at_install")
class TestBfBiGoogleSheets(_Cloud):
    ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = cls.private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()).decode()
        cls.key = {"type": "service_account", "client_email": "bi@banc.iam.gserviceaccount.com",
                   "private_key": pem, "token_uri": "http://169.254.169.254/token"}
        cls.connection = cls.Connection.create({
            "name": "Google", "code": "essai_gs", "kind": "google",
            "gs_spreadsheet": "https://docs.google.com/spreadsheets/d/%s/edit#gid=0" % cls.ID,
            "gs_service_account": json.dumps(cls.key), "user_ids": [(6, 0, [cls.reader.id])]})

    def setUp(self):
        super().setUp()
        base = "https://sheets.googleapis.com/v4/spreadsheets/" + self.ID
        self.tokens["https://oauth2.googleapis.com/token"] = answer({"access_token": "jeton-google", "expires_in": 3600})

        def sheets(url, params):
            if url == base and params.get("fields") == "sheets.properties.title":
                return answer({"sheets": [{"properties": {"title": "Factures"}}, {"properties": {"title": "Notes d'été"}}]})
            if url == base:
                self.assertEqual(params["ranges"], "'Factures'!2:%d" % (min(self.connection.row_limit, 50) + 1))
                fmt = lambda kind: {"effectiveFormat": {"numberFormat": {"type": kind}}} if kind else {}
                return answer({"sheets": [{"data": [{"rowData": [{"values": [fmt(k) for k in line]}
                                                                 for line in self.formats]}]}]})
            self.assertEqual(unquote(url[len(base):]), "/values/'Factures'!1:7")
            self.assertEqual((params["valueRenderOption"], params["dateTimeRenderOption"]),
                             ("UNFORMATTED_VALUE", "SERIAL_NUMBER"))
            # 46032.75 : une date saisie avec une heure tombe le jour même, pas le lendemain.
            return answer({"values": [["Date", "Client", "Montant", "Saisi"], [46032.75, "Alpha", 100, 46032.5],
                                      [46058.0, "Bêta", 30.5, 46058.25]]})
        self.answers[base] = sheets

    formats = [["DATE", None, "CURRENCY", "DATE_TIME"]]

    def test_service_account_token_then_values(self):
        self.connection.row_limit = 5
        self.assertEqual(self.Connection.with_user(self.reader).bf_list_tables("essai_gs"), ["Factures", "Notes d'été"])
        data = self.fetch("essai_gs", "Factures")
        self.assertEqual(data["types"], ["Date", "Text", "Numeric", "DateTime"])  # dates par leur format
        self.assertEqual(data["rows"], [[46032, "Alpha", 100, 46032.5], [46058, "Bêta", 30.5, 46058.25]])
        self.assertIsInstance(data["rows"][1][0], int)
        # L'adresse de jeton est celle de Google, jamais celle que porte la clé.
        self.assertEqual({p["url"] for p in self.posts}, {"https://oauth2.googleapis.com/token"})
        import jwt
        claims = jwt.decode(self.posts[0]["data"]["assertion"], self.private.public_key(), algorithms=["RS256"],
                            audience="https://oauth2.googleapis.com/token")
        self.assertEqual((claims["iss"], claims["scope"]),
                         ("bi@banc.iam.gserviceaccount.com", "https://www.googleapis.com/auth/spreadsheets.readonly"))
        self.assertTrue(all(g["headers"]["Authorization"] == "Bearer jeton-google" and g["secret_bound"] is True
                            for g in self.gets))

    def test_a_date_column_is_recognized_below_an_empty_first_cell(self):
        self.connection.row_limit = 5
        self.formats = [[None, None, "CURRENCY", None], ["DATE", "TEXT", "NUMBER", "DATE_TIME"]]
        self.assertEqual(self.fetch("essai_gs", "Factures")["types"], ["Date", "Text", "Numeric", "DateTime"])

    def test_the_key_is_pasted_but_never_shown_again(self):
        connection = self.connection
        self.assertEqual((connection.gs_key_input, connection.gs_client_email),
                         (False, "bi@banc.iam.gserviceaccount.com"))
        other = dict(self.key, client_email="autre@banc.iam.gserviceaccount.com")
        # Par l'écran : la réponse d'« Enregistrer » ne renvoie pas la clé collée.
        saved = connection.web_save({"gs_key_input": json.dumps(other)}, {"gs_key_input": {}, "gs_client_email": {}})
        self.assertEqual((saved[0]["gs_key_input"], saved[0]["gs_client_email"]), (False, other["client_email"]))
        self.assertEqual(json.loads(connection.gs_service_account)["client_email"], other["client_email"])
        self.assertEqual(connection.gs_key_input, False)
        # Des espaces seuls ne remplacent pas la clé.
        connection.gs_key_input = "  \n "
        self.assertEqual(connection.gs_client_email, other["client_email"])
        created = self.Connection.create({"name": "G2", "code": "essai_gs2", "kind": "google",
                                          "gs_spreadsheet": self.ID, "gs_key_input": json.dumps(self.key)})
        self.assertEqual((created.gs_key_input, created.gs_client_email), (False, self.key["client_email"]))
        self.assertEqual(json.loads(created.gs_service_account), self.key)
        with self.assertRaises(ValidationError):
            connection.gs_key_input = "pas une clé"
        for field in ("gs_service_account", "gs_key_input", "gs_client_email"):
            self.assertEqual(self.Connection._fields[field].groups, "base.group_system", field)
        view = self.env.ref("bf_bi.bf_bi_connection_view_form").arch
        self.assertNotIn('name="gs_service_account"', view, "la clé elle-même n'est jamais à l'écran")

    def test_unknown_tab_is_refused(self):
        with self.assertRaises(UserError) as err:
            self.fetch("essai_gs", "Autre")
        self.assertIn("this sheet does not exist", str(err.exception))
        # Seule la liste des onglets est lue : aucune plage n'est demandée pour un onglet inconnu.
        self.assertEqual([g["params"].get("fields") for g in self.gets], ["sheets.properties.title"])

    def test_spreadsheet_and_key_are_checked(self):
        self.connection.gs_spreadsheet = self.ID
        self.assertEqual(self.connection._gs_id(), self.ID)
        self.connection.gs_spreadsheet = "https://docs.google.com/spreadsheets/u/1/d/%s/edit" % self.ID
        self.assertEqual(self.connection._gs_id(), self.ID)
        with self.assertRaises(ValidationError):
            self.connection.gs_spreadsheet = "https://example.com/pas-une-feuille"
        for key in ("pas du json", json.dumps(dict(self.key, type="authorized_user")),
                    json.dumps(dict(self.key, client_email="")),
                    json.dumps(dict(self.key, private_key="-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----")),
                    json.dumps(dict(self.key, private_key=ec_pem()))):
            with self.subTest(key=key[:30]), self.assertRaises(ValidationError):
                self.connection.gs_service_account = key

    def test_the_key_is_admin_only(self):
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            self.connection.with_user(self.designer).read(["gs_service_account"])


@tagged("post_install", "-at_install")
class TestBfBiOData(_Cloud):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.connection = cls.Connection.create({
            "name": "OData", "code": "essai_odata", "kind": "web", "web_url": "https://odata.banc.test/svc",
            "web_auth": "query", "web_auth_name": "api_key", "web_secret": SECRET,
            "user_ids": [(6, 0, [cls.reader.id])],
            "table_ids": [(0, 0, {"name": "Clients", "request": "Customers?$select=Nom,Solde", "fmt": "odata"})]})

    def page(self, start, count, following):
        body = {"@odata.context": "x", "value": [{"Nom": "C%d" % i, "Solde": i} for i in range(start, start + count)]}
        if following:
            body["@odata.nextLink"] = following
        return answer(body)

    def test_follows_next_links_up_to_the_row_limit(self):
        self.connection.row_limit = 7
        self.answers["https://odata.banc.test/svc/Customers?$select"] = self.page(0, 3, "Customers?$skiptoken=3")
        # Même origine écrite autrement (port par défaut explicite) : suivie.
        self.answers["https://odata.banc.test/svc/Customers?$skiptoken=3"] = self.page(
            3, 3, "https://odata.banc.test:443/svc/Customers?$skiptoken=6")
        self.answers["https://odata.banc.test:443/svc/Customers?$skiptoken=6"] = self.page(6, 3, "Customers?$skiptoken=9")
        data = self.fetch("essai_odata", "Clients")
        self.assertEqual(data["header"], ["Nom", "Solde"])
        self.assertEqual([r[1] for r in data["rows"]], [0, 1, 2, 3, 4, 5, 6])
        self.assertTrue(data["truncated"])
        self.assertEqual(len(self.gets), 3, "on s'arrête dès qu'on a plus que le plafond")
        self.assertTrue(all(g["params"] == {"api_key": SECRET} for g in self.gets), "la clé suit chaque page")
        self.assertTrue(all(g["headers"]["Accept"].startswith("application/json;odata") for g in self.gets))
        self.assertTrue(all(g["max_bytes"] <= network.MAX_BYTES for g in self.gets))
        self.assertLess(self.gets[2]["max_bytes"], self.gets[0]["max_bytes"], "le plafond d'octets est partagé")

    def test_a_next_link_never_leaves_the_origin(self):
        self.answers["https://odata.banc.test/svc/Customers?$select"] = self.page(0, 2, "https://autre.test/vol?k=1")
        with self.assertRaises(UserError) as err:
            self.fetch("essai_odata", "Clients")
        self.assertIn("not allowed", str(err.exception))
        self.assertEqual(len(self.gets), 1)
        self.answers["https://odata.banc.test/svc/Customers?$select"] = self.page(0, 2, "http://odata.banc.test/svc/x")
        bf_bi_connection._CACHE.clear()
        with self.assertRaises(UserError):
            self.fetch("essai_odata", "Clients")

    def test_a_backslash_link_is_refused(self):
        # urlparse y lirait odata.banc.test ; la connexion joindrait autre.test, clé comprise.
        self.answers["https://odata.banc.test/svc/Customers?$select"] = self.page(
            0, 2, "https://autre.test\\@odata.banc.test/svc/Customers?$skiptoken=2")
        with self.assertRaises(UserError) as err:
            self.fetch("essai_odata", "Clients")
        self.assertIn("not allowed", str(err.exception))
        self.assertEqual(len(self.gets), 1)

    def test_the_base_origin_is_read_like_the_connection(self):
        # La première page part vers autre.test (lecture d'urllib3) : une page suivante sur
        # odata.banc.test change d'hôte, même si urlparse lit odata.banc.test dans la base.
        self.connection.web_url = "https://autre.test\\@odata.banc.test/svc"
        self.answers["https://autre.test\\@odata.banc.test/svc/Customers?$select"] = self.page(
            0, 2, "https://odata.banc.test/svc/Customers?$skiptoken=2")
        with self.assertRaises(UserError) as err:
            self.fetch("essai_odata", "Clients")
        self.assertIn("not allowed", str(err.exception))
        self.assertEqual(len(self.gets), 1)

    def test_all_pages_share_one_deadline(self):
        self.answers["https://odata.banc.test/svc/Customers"] = lambda url, params: self.page(
            0, 1, "Customers?$skiptoken=%d" % (len(self.gets) + 1))
        with patch.object(bf_bi_connection, "ODATA_TOTAL_TIMEOUT", -1):
            data = self.fetch("essai_odata", "Clients")
        self.assertEqual((len(data["rows"]), data["truncated"], len(self.gets)), (1, True, 1))

    def test_pages_are_bounded(self):
        self.answers["https://odata.banc.test/svc/Customers"] = lambda url, params: self.page(
            0, 1, "Customers?$skiptoken=%d" % (len(self.gets) + 1))
        data = self.fetch("essai_odata", "Clients")
        self.assertEqual(len(data["rows"]), bf_bi_connection.ODATA_MAX_PAGES)
        self.assertTrue(data["truncated"], "il restait des pages : la table est signalée incomplète")
        self.assertEqual(len(self.gets), bf_bi_connection.ODATA_MAX_PAGES)

    def test_a_link_back_to_the_same_page_stops(self):
        self.answers["https://odata.banc.test/svc/Customers?$select"] = lambda url, params: self.page(0, 2, url)
        data = self.fetch("essai_odata", "Clients")
        self.assertEqual((len(data["rows"]), data["truncated"], len(self.gets)), (2, True, 1))

    def test_version_3_next_links_are_followed(self):
        self.answers["https://odata.banc.test/svc/Customers?$select"] = answer(
            {"value": [{"Nom": "A", "Solde": 1}], "odata.nextLink": "Customers?$skiptoken=1"})
        self.answers["https://odata.banc.test/svc/Customers?$skiptoken=1"] = self.page(1, 1, None)
        data = self.fetch("essai_odata", "Clients")
        self.assertEqual(([r[1] for r in data["rows"]], data["truncated"]), ([1, 1], False))

    def test_an_answer_without_value_is_refused(self):
        self.answers["https://odata.banc.test/svc/Customers"] = answer({"d": {"results": []}})
        with self.assertRaises(UserError) as err:
            self.fetch("essai_odata", "Clients")
        self.assertIn("records path", str(err.exception))

"""Connexions de données externes des tableaux de bord.

Une source externe n'a pas la sécurité d'Odoo : aucune règle d'enregistrement ne la
filtre. La portée se décide donc sur la connexion elle-même :
- une connexion n'est ouverte à PERSONNE tant qu'on ne lui donne pas de groupes ou de
  personnes ;
- chaque lecture se fait au nom de la personne qui ouvre le tableau de bord (dans son
  navigateur), et le serveur vérifie qu'elle y a droit AVANT d'appeler la source ;
- les secrets (clé, jeton, mot de passe) ne sont lisibles que des administrateurs, jamais
  envoyés aux lecteurs ;
- toute sortie réseau passe par la garde de bf_bi_network : pas d'adresse interne.

Types : Grist, fichier déposé (Excel ou CSV), API web (JSON, CSV ou OData), PostgreSQL,
fichier Nextcloud, fichier OneDrive ou SharePoint (Microsoft Graph), Google Sheets. Pour
l'API web et les bases, les tables sont définies par l'administrateur (chemin ou requête
enregistrée) : le lecteur ne choisit qu'un nom de table, jamais une adresse ni du SQL.
"""
import base64
import binascii
import datetime
import json
import logging
import math
import os
import re
import socket
import time
from urllib.parse import quote, urlparse

import psycopg2
import requests

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from . import bf_bi_external as external
from . import bf_bi_network as network
from . import bf_bi_tables as tables

_logger = logging.getLogger(__name__)

CACHE_MAX = 50  # tables gardées au plus, par processus
CACHE_TTL = 60  # secondes : un tableau de bord ouvert par dix personnes n'appelle la source qu'une fois
# Un fichier déposé ne change qu'avec la connexion (write_date est dans la clé) : il se garde plus longtemps.
CACHE_TTL_FILE = 3600
MAX_FILE = 25 * 1024 * 1024
DB_TIMEOUT_MS = 15000
DB_TOTAL_TIMEOUT = 30  # secondes : au-delà, le socket de la base distante est coupé côté client
# En-têtes qu'une clé ne peut pas porter : ils changeraient la requête elle-même.
FORBIDDEN_HEADERS = {"host", "content-length", "transfer-encoding", "connection", "cookie", "upgrade", "te",
                     "trailer", "keep-alive", "expect", "proxy-authorization", "proxy-connection"}
CSV_TABLE = "Data"
ODATA_MAX_PAGES = 50
ODATA_TOTAL_TIMEOUT = 60  # secondes pour toutes les pages : au-delà, la table est rendue incomplète
GS_FORMAT_ROWS = 50  # lignes lues pour reconnaître les colonnes de dates d’une feuille Google
GRAPH = "https://graph.microsoft.com/v1.0"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"  # fixé ici : jamais lu de la clé de compte de service
GOOGLE_SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
GOOGLE_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_CACHE = {}
_TOKENS = {}  # jetons d'accès Microsoft et Google, par connexion, jusqu'à leur échéance


def _pg_connect(**params):
    """Connexion à la base distante (point de patch des essais : psycopg2.connect sert aussi à Odoo)."""
    return psycopg2.connect(**params)


class RemoteDatabaseError(Exception):
    """Erreur de la base DISTANTE. Celles de la base d'Odoo remontent telles quelles :
    son mécanisme de reprise (conflit de mise à jour) en a besoin."""



class BfBiConnection(models.Model):
    _name = "bf.bi.connection"
    _description = "BI data connection"
    _order = "name"

    name = fields.Char(string="Name", required=True)
    code = fields.Char(
        string="Code", required=True, copy=False,
        help="Short name used in formulas: =BF.SOURCE(\"code\", \"Table\"). Lowercase letters, digits and _ only.")
    kind = fields.Selection([
        ("grist", "Grist"),
        ("file", "Excel or CSV file"),
        ("web", "Web API (JSON, CSV or OData)"),
        ("postgresql", "PostgreSQL"),
        ("nextcloud", "Nextcloud file"),
        ("microsoft", "OneDrive or SharePoint file"),
        ("google", "Google Sheets"),
    ], string="Type", required=True, default="file")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", string="Company", default=lambda self: self.env.company)
    group_ids = fields.Many2many(
        "res.groups", "bf_bi_connection_group_rel", "connection_id", "group_id", string="Authorized groups",
        help="Members of these groups see this source's data in dashboards. Empty and with no authorized person: the connection is open to nobody.")
    user_ids = fields.Many2many(
        "res.users", "bf_bi_connection_user_rel", "connection_id", "user_id", string="Authorized people")
    is_open = fields.Boolean(string="Open to someone", compute="_compute_is_open")
    row_limit = fields.Integer(string="Maximum rows", default=5000, required=True)
    table_ids = fields.One2many("bf.bi.connection.table", "connection_id", string="Tables")
    customer_ids = fields.One2many("bf.bi.connection.customer", "connection_id", string="Customers")
    # Grist
    grist_url = fields.Char(string="Grist server address", help="e.g. https://grist.example.com")
    grist_doc_id = fields.Char(string="Grist document ID")
    grist_api_key = fields.Char(string="Grist API key", groups="base.group_system", copy=False)
    # Fichier déposé
    file_data = fields.Binary(string="File", attachment=True, copy=False)
    file_name = fields.Char(string="File name", copy=False)
    # API web
    web_url = fields.Char(
        string="Base address", help="e.g. https://api.example.com/v1. Each table adds its own path to it.")
    web_auth = fields.Selection([
        ("none", "None"),
        ("bearer", "Bearer token"),
        ("header", "Key in a header"),
        ("query", "Key in the address"),
        ("basic", "User name and password"),
    ], string="Authentication", default="none")
    web_auth_name = fields.Char(
        string="Header or parameter name", help="e.g. X-API-Key (header) or api_key (address parameter).")
    web_username = fields.Char(string="User name")
    web_secret = fields.Char(string="Key, token or password", groups="base.group_system", copy=False)
    # Base de données
    db_host = fields.Char(string="Server")
    db_port = fields.Integer(string="Port", default=5432)
    db_name = fields.Char(string="Database")
    db_user = fields.Char(string="User")
    db_password = fields.Char(string="Password", groups="base.group_system", copy=False)
    db_sslmode = fields.Selection([
        ("require", "Encrypted (required)"),
        ("verify-full", "Encrypted, certificate verified"),
        ("prefer", "Encrypted if possible"),
        ("disable", "Not encrypted"),
    ], string="Encryption", default="require")
    # Nextcloud
    nc_url = fields.Char(string="Nextcloud address", help="e.g. https://cloud.example.com")
    nc_user = fields.Char(string="Nextcloud user")
    nc_password = fields.Char(
        string="App password", groups="base.group_system", copy=False,
        help="Created in Nextcloud, Personal settings > Security > Devices and sessions. Never the account password.")
    nc_path = fields.Char(string="File path", help="Path in the user's files, e.g. /Finances/Budget 2026.xlsx")
    # Microsoft (OneDrive, SharePoint)
    ms_tenant = fields.Char(string="Directory (tenant) ID", help="Microsoft Entra: the directory ID, or its domain.")
    ms_client_id = fields.Char(string="Application (client) ID")
    ms_client_secret = fields.Char(string="Client secret", groups="base.group_system", copy=False)
    ms_share_url = fields.Char(
        string="File sharing link", help="The link from Share > Copy link, for an .xlsx or .csv file.")
    # Google Sheets
    gs_spreadsheet = fields.Char(string="Spreadsheet", help="Its address or its ID.")
    gs_service_account = fields.Text(
        string="Service account key (JSON)", groups="base.group_system", copy=False,
        help="Share the spreadsheet with the service account's address, as a viewer.")
    # La clé privée ne se relit pas à l'écran (capture, partage d'écran) : on la colle, on voit le compte.
    gs_key_input = fields.Text(
        string="Service account key (JSON)", compute="_compute_gs_key_input", inverse="_inverse_gs_key_input",
        groups="base.group_system",
        help="Paste the JSON key of a Google Cloud service account. It is kept but never shown again; paste a new "
             "one to replace it. Share the spreadsheet with the service account's address, as a viewer.")
    gs_client_email = fields.Char(string="Service account", compute="_compute_gs_client_email",
                                  groups="base.group_system")
    note = fields.Text(string="Notes")

    _sql_constraints = [
        ("code_unique", "unique(code)", "This connection code already exists."),
    ]

    @api.depends("group_ids", "user_ids")
    def _compute_is_open(self):
        for connection in self:
            connection.is_open = bool(connection.group_ids or connection.user_ids)

    @api.constrains("code")
    def _check_code(self):
        for connection in self:
            if not re.fullmatch(r"[a-z0-9_]{1,40}", connection.code or ""):
                raise ValidationError(_("The code only takes lowercase letters, digits and _ (40 at most)."))

    @api.constrains("grist_url", "web_url", "nc_url", "ms_share_url")
    def _check_urls(self):
        for connection in self:
            for value in (connection.grist_url, connection.web_url, connection.nc_url, connection.ms_share_url):
                if not value:
                    continue
                url = urlparse(value)
                if url.scheme not in ("http", "https") or not url.hostname:
                    raise ValidationError(_("The address must start with https:// (or http://)."))

    @api.constrains("row_limit")
    def _check_row_limit(self):
        for connection in self:
            if not 1 <= connection.row_limit <= 50000:
                raise ValidationError(_("The row limit goes from 1 to 50,000."))

    @api.constrains("web_auth", "web_auth_name")
    def _check_web_auth_name(self):
        for connection in self.filtered(lambda c: c.web_auth in ("header", "query") and c.web_auth_name):
            name = connection.web_auth_name
            if connection.web_auth == "header" and (
                    not re.fullmatch(r"[A-Za-z0-9-]{1,64}", name) or name.lower() in FORBIDDEN_HEADERS):
                raise ValidationError(_("This header name cannot carry a key."))
            if connection.web_auth == "query" and not re.fullmatch(r"[A-Za-z0-9_.\-]{1,64}", name):
                raise ValidationError(_("This parameter name is not valid."))

    @api.constrains("nc_path", "ms_tenant", "ms_client_id", "gs_spreadsheet")
    def _check_cloud_fields(self):
        for connection in self:
            if connection.nc_path and connection.nc_path.lower().endswith(".xls"):
                raise ValidationError(_("Old .xls workbooks are not read: save the file as .xlsx."))
            if connection.nc_path and ".." in [part.strip() for part in re.split(r"[/\\]", connection.nc_path)]:
                raise ValidationError(_("The file path stays in the user's files: no “..”."))
            if connection.ms_tenant and not re.fullmatch(r"[A-Za-z0-9.-]{1,100}", connection.ms_tenant):
                raise ValidationError(_("The directory ID is a GUID or a domain name."))
            if connection.ms_client_id and not _GUID.fullmatch(connection.ms_client_id):
                raise ValidationError(_("The application ID is a GUID."))
            if connection.gs_spreadsheet and not connection._gs_id():
                raise ValidationError(_("This is not a Google Sheets address or ID."))

    def _compute_gs_key_input(self):
        self.gs_key_input = False

    def _inverse_gs_key_input(self):
        for connection in self.filtered(lambda c: (c.gs_key_input or "").strip()):
            connection.gs_service_account = connection.gs_key_input.strip()
        # Odoo garde la valeur écrite d'un champ calculé dans le cache de la transaction : sans
        # cela, la réponse d'« Enregistrer » renverrait la clé collée à l'écran.
        self.invalidate_recordset(["gs_key_input"])

    @api.depends("gs_service_account")
    def _compute_gs_client_email(self):
        for connection in self:
            try:
                connection.gs_client_email = connection._gs_key()[0] if connection.gs_service_account else False
            except ValueError:
                connection.gs_client_email = False

    @api.constrains("gs_service_account")
    def _check_service_account(self):
        for connection in self.filtered("gs_service_account"):
            try:
                connection._gs_key()
            except ValueError:
                raise ValidationError(_("This is not a Google service account key (JSON).")) from None

    @api.constrains("db_port")
    def _check_db_port(self):
        for connection in self.filtered(lambda c: c.kind == "postgresql"):
            if not 1 <= connection.db_port <= 65535:
                raise ValidationError(_("The port goes from 1 to 65535."))

    @api.constrains("file_data", "file_name")
    def _check_file(self):
        for connection in self.filtered("file_data"):
            body = connection._file_body()
            if len(body) > MAX_FILE:
                raise ValidationError(_("The file is too big (25 MB at most)."))
            try:
                connection._file_tables()
            except tables.SourceFormatError as exc:
                raise ValidationError(connection._format_message(exc)) from None

    def write(self, vals):
        # Vider le cache de ces connexions : une portée ou une clé changée vaut tout de
        # suite dans ce processus. Les autres processus changent de clé avec write_date.
        self._bf_clear_cache()
        return super().write(vals)

    def unlink(self):
        self._bf_clear_cache()
        return super().unlink()

    def _bf_clear_cache(self):
        dbname = self.env.cr.dbname
        for key in [k for k in list(_CACHE) if k[0] == dbname and k[1] in self.ids]:
            _CACHE.pop(key, None)
        for key in [k for k in list(_TOKENS) if k[0] == dbname and k[1] in self.ids]:
            _TOKENS.pop(key, None)

    # ------------------------------------------------------------------
    # Portée
    # ------------------------------------------------------------------

    def _bf_can_use(self, user):
        self.ensure_one()
        if not user._is_internal():
            return False
        # Sociétés ACTIVES de la session (pas toutes celles permises à la personne). Lues
        # HORS superutilisateur : `self` est en sudo, et en sudo Odoo ne confronte plus les
        # sociétés demandées par le contexte (que le navigateur fournit) à celles de la
        # personne. Une société forgée lève alors AccessError, comme pour tout autre appel.
        if self.company_id and self.company_id not in self.env(su=False).companies:
            return False
        return user in self.user_ids or bool(self.group_ids & user.groups_id)

    @api.model
    def _bf_get_for_use(self, code):
        """La connexion `code`, si la personne courante y a droit. Sinon AccessError.

        Même message que la connexion existe ou non : on ne révèle pas les codes.
        """
        connection = self.sudo().search([("code", "=", code)], limit=1)
        if not connection or not connection._bf_can_use(self.env.user):
            raise AccessError(_("Source “%s”: access denied.", code))
        return connection

    # ------------------------------------------------------------------
    # Appels depuis le tableur et l'éditeur
    # ------------------------------------------------------------------

    @api.model
    def bf_list_usable(self):
        """Connexions que la personne peut utiliser (sans aucun secret)."""
        connections = self.sudo().search([])
        return [
            {"code": c.code, "name": c.name, "kind": c.kind}
            for c in connections if c._bf_can_use(self.env.user)
        ]

    @api.model
    def bf_list_tables(self, code):
        connection = self._bf_get_for_use(code)
        return connection._bf_guarded(connection._bf_tables)

    @api.model
    def bf_fetch_table(self, code, table):
        """Table `table` de la source `code` : {"header": [...], "rows": [[...]], "truncated": bool}.

        Appelée par la fonction =BF.SOURCE dans le navigateur de la personne qui regarde.
        """
        connection = self._bf_get_for_use(code)
        return connection._bf_read(table)

    def _bf_read(self, table):
        """La table `table`, par le cache. L'appelant a vérifié le droit de la personne
        (_bf_get_for_use) : le tableur comme les mesures externes passent par ici."""
        self.ensure_one()
        connection = self
        if not isinstance(table, str) or not table:
            raise UserError(_("Missing table name."))
        defined = connection._bf_defined_table(table)
        key = (self.env.cr.dbname, connection.id, str(connection.write_date), table,
               str(defined.write_date) if defined else "")
        ttl = CACHE_TTL_FILE if connection.kind == "file" else CACHE_TTL
        hit = _CACHE.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        data = connection._bf_guarded(connection._bf_fetch, table)
        maintenant = time.monotonic()
        for vieille in [k for k, (t, _d) in list(_CACHE.items()) if maintenant - t >= CACHE_TTL_FILE]:
            _CACHE.pop(vieille, None)
        # Plafond dur : au-delà, on retire les plus anciennes (la mémoire du processus est bornée).
        while len(_CACHE) >= CACHE_MAX:
            _CACHE.pop(min(list(_CACHE), key=lambda k: _CACHE.get(k, (0,))[0]), None)
        _CACHE[key] = (maintenant, data)
        return data

    def _bf_customer_map(self):
        """{clé de valeur client : id de contact} de la table de correspondance."""
        self.ensure_one()
        # Des ids seulement : un search_read en sudo calculerait le nom du contact et le
        # laisserait au cache de la transaction, où le rapport le lirait sans contrôle d'accès.
        rows = self.env["bf.bi.connection.customer"].sudo().search_fetch(
            [("connection_id", "=", self.id), ("partner_id", "!=", False)], ["key", "partner_id"])
        return {row.key: row.partner_id.id for row in rows}

    def action_collect_customers(self):
        """Relève les valeurs client des mesures externes de la connexion et rapproche les nouvelles."""
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only administrators can fill the customer correspondence."))
        measures = self.env["bf.bi.measure"].sudo().search(
            [("kind", "=", "external"), ("connection_code", "=", self.code), ("customer_column", "!=", False)])
        if not measures:
            raise UserError(_("No external measure of this connection has a customer column."))
        seen = {}
        for measure in measures:
            data = self._bf_guarded(self._bf_fetch, measure.source_table, None, detail=True)
            if measure.customer_column not in data["header"]:
                raise UserError(_("Measure “%(code)s”: the table has no column “%(column)s”.",
                                  code=measure.code, column=measure.customer_column))
            index = data["header"].index(measure.customer_column)
            for row in data["rows"]:
                key = external.customer_key(row[index])
                if key and key not in seen:
                    value = row[index]
                    seen[key] = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value).strip()
        Customer = self.env["bf.bi.connection.customer"]
        known = set(Customer.search([("connection_id", "=", self.id)]).mapped("key"))
        new = {key: name for key, name in seen.items() if key not in known}
        suggestions = Customer._bf_suggest(new, self.company_id)
        Customer.create([{"connection_id": self.id, "name": name, "partner_id": suggestions.get(key, False),
                          "suggested": key in suggestions} for key, name in new.items()])
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("%(new)s new customer value(s), %(found)s matched to a contact: check them. "
                             "Values without a contact count under no customer.",
                             new=len(new), found=len(suggestions)),
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},  # la liste montre les nouvelles
            },
        }

    def action_test_connection(self):
        self.ensure_one()
        # Méthode publique : sans ceci, n'importe quel employé listerait les tables de
        # toutes les connexions, même fermées, en passant leurs ids.
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only administrators can test a data connection."))
        names = self._bf_guarded(self._bf_tables, detail=True)
        if self.kind in ("web", "postgresql"):
            if not names:
                raise UserError(_("Add at least one table to test this connection."))
            for name in names:
                self._bf_guarded(self._bf_fetch, name, 1, detail=True)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("Connection successful: %(n)s table(s) (%(names)s).",
                             n=len(names), names=", ".join(names[:10])),
            },
        }

    # ------------------------------------------------------------------
    # Aiguillage par type
    # ------------------------------------------------------------------

    def _bf_defined_table(self, name):
        """La table définie par l'administrateur (API web, base), ou rien."""
        if self.kind not in ("web", "postgresql"):
            return self.env["bf.bi.connection.table"]
        return self.sudo().table_ids.filtered(lambda t: t.name == name)[:1]

    def _bf_tables(self):
        self.ensure_one()
        if self.kind == "grist":
            return self._grist_tables()
        if self.kind == "file":
            return self._file_tables()
        if self.kind in ("nextcloud", "microsoft"):
            body, workbook = self._remote_file()
            return self._body_tables(body, workbook)
        if self.kind == "google":
            return self._gs_tables()
        return self.sudo().table_ids.mapped("name")

    def _bf_fetch(self, table, limit=None):
        self.ensure_one()
        limit = limit or self.sudo().row_limit
        if self.kind == "grist":
            return self._grist_fetch(table, limit)
        if self.kind == "file":
            return self._file_fetch(table, limit)
        if self.kind in ("nextcloud", "microsoft"):
            body, workbook = self._remote_file()
            return self._body_fetch(body, workbook, table, limit)
        if self.kind == "google":
            return self._gs_fetch(table, limit)
        defined = self._bf_defined_table(table)
        if not defined:
            raise UserError(_("Source “%(code)s”: no table named “%(table)s”.", code=self.code, table=table))
        if self.kind == "web":
            return self._web_fetch(defined, limit)
        return self._pg_fetch(defined, limit)

    def _format_message(self, exc):
        # Des _() littéraux : l'extracteur des traductions ne ramasse que ceux-là.
        reasons = {
            "path": _("the records path does not lead to a list"),
            "not JSON": _("the answer is not JSON"),
            "xlsx": _("the file is not a valid Excel workbook (.xlsx)"),
            "xls": _("old Excel workbooks (.xls) are not read: save it as .xlsx"),
            "too big": _("the file is too big once unpacked"),
            "sheet": _("this sheet does not exist"),
            "csv": _("the CSV file cannot be read"),
            "encoding": _("the text encoding is not recognized"),
            "sql": _("the query must be one single statement"),
        }
        reason = reasons.get(str(exc)) or _("the data cannot be read")
        return _("Source “%(code)s”: %(reason)s.", code=self.code or "", reason=reason)

    def _bf_guarded(self, method, *args, detail=False):
        """Appelle `method` et traduit ses erreurs en messages sûrs.

        Le lecteur ne voit jamais le détail (adresse interne, message du serveur distant) ;
        `detail` le donne à l'administrateur qui teste sa connexion, pour qu'il la corrige.
        """
        try:
            return method(*args)
        except (UserError, AccessError):
            raise
        except network.BlockedAddress:
            raise UserError(_("Source “%s”: this address is not allowed (private or internal network).",
                              self.code)) from None
        except network.TooLarge:
            raise UserError(_("Source “%s”: the answer is too large.", self.code)) from None
        except tables.SourceFormatError as exc:
            raise UserError(self._format_message(exc)) from None
        except (requests.RequestException, socket.error) as exc:
            _logger.warning("bf_bi : source injoignable pour la connexion %s : %s", self.code, type(exc).__name__)
            raise UserError(_("Source “%s” unreachable.", self.code)) from None
        except RemoteDatabaseError as exc:
            _logger.warning("bf_bi : base distante en erreur pour la connexion %s", self.code)
            message = _("Source “%s”: the database refused the connection or the query.", self.code)
            if detail:
                message = "%s\n%s" % (message, (str(exc).strip().splitlines() or [""])[0][:300])
            raise UserError(message) from None
        except psycopg2.Error:
            raise
        except Exception as exc:
            # Réponse d'une forme inattendue (JSON trop profond, valeur absurde…) : le lecteur
            # reçoit un message neutre, le détail reste au journal du serveur.
            _logger.warning("bf_bi : source illisible pour la connexion %s", self.code, exc_info=True)
            message = self._format_message(tables.SourceFormatError(""))
            if detail:
                message = "%s\n%s" % (message, (str(exc).strip().splitlines() or [""])[0][:300])
            raise UserError(message) from None

    # ------------------------------------------------------------------
    # Grist
    # ------------------------------------------------------------------

    def _grist_get(self, path, params=None):
        self.ensure_one()
        conn = self.sudo()
        if not (conn.grist_url and conn.grist_doc_id and conn.grist_api_key):
            raise UserError(_("Connection “%s” is incomplete: address, document and key are required.", conn.name))
        url = "%s/api/docs/%s%s" % (conn.grist_url.rstrip("/"), quote(conn.grist_doc_id, safe=""), path)
        status, body, _ctype = network.http_get(
            url, params=params, headers={"Authorization": "Bearer %s" % conn.grist_api_key}, secret_bound=True)
        if status != 200:
            # Le corps de la réponse n'est pas renvoyé : il peut porter des détails du serveur.
            raise UserError(_("Source “%(code)s”: Grist answered %(status)s.", code=conn.code, status=status))
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise tables.SourceFormatError("not JSON") from None

    def _grist_tables(self):
        data = self._grist_get("/tables")
        return [t["id"] for t in data.get("tables", [])]

    def _grist_fetch(self, table, limit):
        path = "/tables/%s" % quote(table, safe="")
        columns = [
            c for c in self._grist_get(path + "/columns").get("columns", [])
            if not c["id"].startswith("gristHelper_") and c["id"] != "manualSort"
        ]
        records = self._grist_get(path + "/records", params={"limit": limit + 1}).get("records", [])
        truncated = len(records) > limit
        records = records[:limit]
        header = [c["fields"].get("label") or c["id"] for c in columns]
        rows = [
            [self._grist_cell(r["fields"].get(c["id"]), c["fields"].get("type") or "") for c in columns]
            for r in records
        ]
        return {"header": header, "types": [c["fields"].get("type") or "" for c in columns],
                "rows": rows, "truncated": truncated}

    @staticmethod
    def _grist_cell(value, grist_type):
        """Valeur Grist → valeur de tableur (nombre, texte, booléen, date en serial)."""
        if value is None:
            return None
        if isinstance(value, list):
            # Valeurs encodées de Grist : ["L", ...] liste, ["E", ...] erreur, ["R", table, id] référence.
            code, rest = (value[0], value[1:]) if value else ("", [])
            if code == "L":
                return ", ".join(str(v) for v in rest)
            if code == "E":
                return "#ERREUR"
            return str(rest[-1]) if rest else ""
        if grist_type in ("Date",) or grist_type.startswith("DateTime"):
            if isinstance(value, (int, float)):
                serial = tables.to_serial(datetime.datetime.fromtimestamp(value, tz=datetime.timezone.utc))
                return round(serial) if grist_type == "Date" else serial
        return value

    # ------------------------------------------------------------------
    # Fichier déposé (Excel ou CSV)
    # ------------------------------------------------------------------

    def _file_body(self):
        data = self.sudo().file_data
        if not data:
            raise UserError(_("Connection “%s” has no file yet.", self.name))
        try:
            return base64.b64decode(data)
        except (binascii.Error, ValueError):
            raise tables.SourceFormatError("xlsx") from None

    def _file_is_workbook(self):
        name = (self.sudo().file_name or "").lower()
        if name.endswith((".xlsx", ".xlsm")):
            return True
        if name.endswith((".csv", ".txt")):
            return False
        if name.endswith(".xls"):
            raise ValidationError(_("Old .xls workbooks are not read: save the file as .xlsx."))
        return self._file_body()[:2] == b"PK"

    def _file_tables(self):
        return self._body_tables(self._file_body(), self._file_is_workbook())

    def _file_fetch(self, table, limit):
        return self._body_fetch(self._file_body(), self._file_is_workbook(), table, limit)

    @staticmethod
    def _body_tables(body, workbook):
        if workbook:
            return tables.xlsx_sheets(body)
        tables.from_csv(body, 1)  # lisible ?
        return [CSV_TABLE]

    @staticmethod
    def _body_fetch(body, workbook, table, limit):
        if workbook:
            return tables.from_xlsx(body, table, limit)
        if table != CSV_TABLE:
            raise tables.SourceFormatError("sheet")
        return tables.from_csv(body, limit)

    # ------------------------------------------------------------------
    # Fichier distant : Nextcloud, OneDrive ou SharePoint
    # ------------------------------------------------------------------

    def _remote_file(self):
        """(corps, classeur ?) du fichier distant, plafonné à la taille d'un fichier déposé."""
        conn = self.sudo()
        if conn.kind == "nextcloud":
            if not (conn.nc_url and conn.nc_user and conn.nc_password and conn.nc_path):
                raise UserError(_("Connection “%s” is incomplete: address, user, app password and file path "
                                  "are required.", conn.name))
            url = "%s/remote.php/dav/files/%s/%s" % (
                conn.nc_url.rstrip("/"), quote(conn.nc_user, safe=""), quote(conn.nc_path.strip().lstrip("/")))
            pair = "%s:%s" % (conn.nc_user, conn.nc_password)
            status, body, _ctype = network.http_get(
                url, headers={"Authorization": "Basic %s" % base64.b64encode(pair.encode()).decode()},
                secret_bound=True, max_bytes=MAX_FILE)
            name = conn.nc_path.lower()
        else:
            if not conn.ms_share_url:
                raise UserError(_("Connection “%s” is incomplete: the file sharing link is required.", conn.name))
            # Lien de partage → identifiant de partage de Graph : « u! » + base64url sans remplissage.
            share = "u!" + base64.urlsafe_b64encode(conn.ms_share_url.strip().encode()).decode().rstrip("=")
            status, body, _ctype = network.http_get(
                "%s/shares/%s/driveItem/content" % (GRAPH, share),
                headers={"Authorization": "Bearer %s" % conn._ms_token()},
                secret_bound=network.STRIP, max_bytes=MAX_FILE)
            name = ""
        if status != 200:
            raise UserError(_("Source “%(code)s”: the server answered %(status)s.", code=conn.code, status=status))
        if body[:4] == b"\xd0\xcf\x11\xe0":  # en-tête OLE : ancien classeur .xls
            raise tables.SourceFormatError("xls")
        if name.endswith((".xlsx", ".xlsm")):
            return body, True
        if name.endswith((".csv", ".txt")):
            return body, False
        return body, body[:2] == b"PK"

    def _token(self, provider, fetch):
        """Jeton d'accès gardé jusqu'à une minute de son échéance (une lecture n'en refait pas un)."""
        key = (self.env.cr.dbname, self.id, str(self.write_date), provider)
        hit = _TOKENS.get(key)
        if hit and time.monotonic() < hit[0]:
            return hit[1]
        status, body, _ctype = fetch()
        try:
            data = json.loads(body.decode("utf-8")) if status == 200 else {}
            token, lifetime = str(data["access_token"]), int(data.get("expires_in", 3600))
        except (UnicodeDecodeError, ValueError, KeyError, TypeError):
            # Ni le corps (détail du fournisseur) ni le secret ne sont rendus.
            raise UserError(_("Source “%(code)s”: the identity provider refused the credentials (%(status)s).",
                              code=self.code, status=status)) from None
        for old in [k for k, (until, _t) in list(_TOKENS.items()) if until <= time.monotonic()]:
            _TOKENS.pop(old, None)
        _TOKENS[key] = (time.monotonic() + max(lifetime - 60, 0), token)
        return token

    def _ms_token(self):
        conn = self.sudo()
        if not (conn.ms_tenant and conn.ms_client_id and conn.ms_client_secret):
            raise UserError(_("Connection “%s” is incomplete: directory ID, application ID and client secret "
                              "are required.", conn.name))
        return conn._token("microsoft", lambda: network.http_post(
            "https://login.microsoftonline.com/%s/oauth2/v2.0/token" % quote(conn.ms_tenant, safe=""),
            {"client_id": conn.ms_client_id, "client_secret": conn.ms_client_secret,
             "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"}))

    # ------------------------------------------------------------------
    # Google Sheets (compte de service)
    # ------------------------------------------------------------------

    def _gs_id(self):
        value = (self.gs_spreadsheet or "").strip()
        found = re.search(r"/spreadsheets/(?:u/\d+/)?d/([A-Za-z0-9_-]{20,})", value)
        if found:
            return found.group(1)
        return value if re.fullmatch(r"[A-Za-z0-9_-]{20,}", value) else None

    def _gs_key(self):
        """(adresse du compte, clé privée chargée) ; ValueError si ce n'est pas une clé de compte de service."""
        from cryptography.exceptions import UnsupportedAlgorithm
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        try:
            data = json.loads(self.sudo().gs_service_account or "")
            if data.get("type") != "service_account" or not data.get("client_email"):
                raise ValueError("not a service account")
            key = serialization.load_pem_private_key(str(data["private_key"]).encode(), password=None)
        except (TypeError, KeyError, AttributeError, UnsupportedAlgorithm, json.JSONDecodeError):
            raise ValueError("not a service account") from None
        if not isinstance(key, rsa.RSAPrivateKey):  # Google signe en RS256
            raise ValueError("not an RSA key")
        return str(data["client_email"]), key

    def _gs_token(self):
        import jwt  # PyJWT, facultatif : sans lui, seul Google Sheets échoue
        conn = self.sudo()
        if not conn.gs_service_account:
            raise UserError(_("Connection “%s” is incomplete: the service account key is required.", conn.name))
        try:
            email, key = conn._gs_key()
        except ValueError:
            raise UserError(_("Connection “%s”: the service account key cannot be read.", conn.name)) from None

        def fetch():  # signé seulement quand le jeton gardé ne sert plus
            now = int(time.time())
            assertion = jwt.encode({"iss": email, "scope": GOOGLE_SCOPE, "aud": GOOGLE_TOKEN, "iat": now,
                                    "exp": now + 3600}, key, algorithm="RS256")
            return network.http_post(
                GOOGLE_TOKEN, {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
        return conn._token("google", fetch)

    def _gs_get(self, path, params=None):
        conn = self.sudo()
        sheet_id = conn._gs_id()
        if not sheet_id:
            raise UserError(_("Connection “%s” is incomplete: the spreadsheet is required.", conn.name))
        status, body, _ctype = network.http_get(
            "%s/%s%s" % (GOOGLE_SHEETS, sheet_id, path), params=params,
            headers={"Authorization": "Bearer %s" % conn._gs_token()}, secret_bound=True)
        if status != 200:
            raise UserError(_("Source “%(code)s”: Google answered %(status)s.", code=conn.code, status=status))
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise tables.SourceFormatError("not JSON") from None

    def _gs_tables(self):
        data = self._gs_get("", {"fields": "sheets.properties.title"})
        return [s["properties"]["title"] for s in data.get("sheets", [])]

    def _gs_fetch(self, table, limit):
        if table not in self._gs_tables():
            raise tables.SourceFormatError("sheet")
        quoted = "'%s'" % table.replace("'", "''")
        # Valeurs brutes, dates en serial (même origine que le tableur : 1899-12-30), et pas plus
        # de lignes que le plafond (+ l'en-tête, + une pour savoir si la table est tronquée).
        values = self._gs_get("/values/%s" % quote("%s!1:%d" % (quoted, limit + 2), safe=""), {
            "valueRenderOption": "UNFORMATTED_VALUE", "dateTimeRenderOption": "SERIAL_NUMBER",
            "majorDimension": "ROWS"}).get("values", [])
        if not values:
            return {"header": [], "types": [], "rows": [], "truncated": False}
        header = ["" if h is None else str(h) for h in values[0]]
        data = tables.typed(header, values[1:], limit)
        # Une date n'est qu'un nombre formaté en date : le premier format de nombre de chaque colonne,
        # sur les premières lignes de données (une case vide en tête n'en porte pas), dit lesquelles.
        formats = self._gs_get("", {"ranges": "%s!2:%d" % (quoted, min(limit, GS_FORMAT_ROWS) + 1),
                                    "fields": "sheets.data.rowData.values.effectiveFormat.numberFormat.type"})
        try:
            grid = formats["sheets"][0]["data"][0]["rowData"]
        except (KeyError, IndexError, TypeError):
            grid = []
        kinds = {}
        for line in grid if isinstance(grid, list) else []:
            for index, cell in enumerate((line or {}).get("values") or []):
                kind = (((cell or {}).get("effectiveFormat") or {}).get("numberFormat") or {}).get("type")
                if kind and index not in kinds:
                    kinds[index] = kind
        for index, kind in kinds.items():
            if index < len(data["types"]) and data["types"][index] == "Numeric" and kind in ("DATE", "DATE_TIME"):
                data["types"][index] = "Date" if kind == "DATE" else "DateTime"
                if kind == "DATE":
                    for row in data["rows"]:
                        if isinstance(row[index], float):
                            row[index] = math.floor(row[index])
        return data

    # ------------------------------------------------------------------
    # API web (JSON ou CSV)
    # ------------------------------------------------------------------

    def _web_fetch(self, defined, limit):
        conn = self.sudo()
        if not conn.web_url:
            raise UserError(_("Connection “%s” is incomplete: the base address is required.", conn.name))
        request = (defined.request or "").strip()
        url = conn.web_url.rstrip("/") + ("/" + request.lstrip("/") if request else "")
        headers = {"Accept": "application/json, text/csv;q=0.9, */*;q=0.5", "User-Agent": "Symbifox-BI"}
        params = None
        auth = conn.web_auth or "none"
        if auth != "none" and not conn.web_secret:
            raise UserError(_("Connection “%s” is incomplete: the key, token or password is missing.", conn.name))
        if auth == "bearer":
            headers["Authorization"] = "Bearer %s" % conn.web_secret
        elif auth == "basic":
            pair = "%s:%s" % (conn.web_username or "", conn.web_secret)
            headers["Authorization"] = "Basic %s" % base64.b64encode(pair.encode()).decode()
        elif auth in ("header", "query"):
            if not conn.web_auth_name:
                raise UserError(_("Connection “%s” is incomplete: the header or parameter name is missing.", conn.name))
            if auth == "header":
                headers[conn.web_auth_name] = conn.web_secret
            else:
                params = {conn.web_auth_name: conn.web_secret}
        if defined.fmt == "odata":
            return conn._odata_fetch(url, params, headers, auth != "none", limit)
        status, body, ctype = network.http_get(url, params=params, headers=headers, secret_bound=auth != "none")
        if status != 200:
            raise UserError(_("Source “%(code)s”: the server answered %(status)s.", code=conn.code, status=status))
        if defined.fmt == "csv":
            return tables.from_csv(body, limit)
        return tables.from_json(body, defined.records_path, limit)

    def _odata_fetch(self, url, params, headers, secret_bound, limit):
        """OData v4 : la liste « value », page après page par « @odata.nextLink », jusqu'au plafond.

        Une page suivante ne quitte jamais l'origine de l'adresse de base (le secret la suit) ; le
        total des pages tient dans le plafond d'octets d'une réponse.
        """
        headers = dict(headers, Accept="application/json;odata.metadata=minimal")
        base = network.origin(self.web_url)
        records, budget = [], network.MAX_BYTES
        deadline = time.monotonic() + ODATA_TOTAL_TIMEOUT
        for _page in range(ODATA_MAX_PAGES):
            status, body, _ctype = network.http_get(url, params=params, headers=headers, secret_bound=secret_bound,
                                                    max_bytes=budget)
            if status != 200:
                raise UserError(_("Source “%(code)s”: the server answered %(status)s.", code=self.code, status=status))
            budget -= len(body)
            try:
                data = json.loads(body.decode("utf-8-sig"))
            except (UnicodeDecodeError, ValueError):
                raise tables.SourceFormatError("not JSON") from None
            if not isinstance(data, dict) or not isinstance(data.get("value"), list):
                raise tables.SourceFormatError("path")
            records.extend(data["value"])
            # « odata.nextLink » : JSON léger des versions 2 et 3.
            following = data.get("@odata.nextLink") or data.get("odata.nextLink")
            if len(records) > limit or not following:
                break
            link = requests.compat.urljoin(url, str(following))
            if link == url:
                break
            if time.monotonic() > deadline:
                break  # il reste des pages : la table est signalée incomplète plus bas
            target = network.origin(link)
            if (target.scheme, target.hostname, network._port(target)) != (base.scheme, base.hostname, network._port(base)):
                raise network.BlockedAddress(target.hostname or "")
            # Les paramètres d'authentification restent ; la page suivante porte déjà les siens.
            url = link
            params = {k: v for k, v in (params or {}).items() if k == self.web_auth_name} or None
        data = tables.from_json(json.dumps({"value": records[:limit + 1]}).encode(), "value", limit)
        # Plafond de pages ou échéance atteints, ou lien qui ramène à la même page : il reste des lignes.
        data["truncated"] = data["truncated"] or bool(following)
        return data

    # ------------------------------------------------------------------
    # PostgreSQL
    # ------------------------------------------------------------------

    def _pg_fetch(self, defined, limit):
        conn = self.sudo()
        if not (conn.db_host and conn.db_name and conn.db_user):
            raise UserError(_("Connection “%s” is incomplete: server, database and user are required.", conn.name))
        statement = tables.single_statement(defined.request)
        port = conn.db_port or 5432
        address = network.vet(conn.db_host, port)
        # host garde le nom (vérification du certificat), hostaddr fixe l'adresse vérifiée :
        # pas de seconde résolution DNS entre la garde et la connexion.
        try:
            db = _pg_connect(
                host=conn.db_host, hostaddr=address, port=port, dbname=conn.db_name, user=conn.db_user,
                password=conn.db_password or "", sslmode=conn.db_sslmode or "require", connect_timeout=10,
                # Jamais d'identifiants tirés des fichiers de libpq sur le serveur d'Odoo.
                passfile=os.devnull, application_name="symbifox-bi")
        except psycopg2.Error as exc:
            raise RemoteDatabaseError(str(exc)) from None
        # Le délai de la base (statement_timeout) est appliqué par le serveur DISTANT : un
        # serveur qui ne répond jamais retiendrait le travailleur. Le minuteur coupe le socket.
        try:
            sock = socket.socket(fileno=os.dup(db.fileno()))
        except OSError:
            db.close()
            raise
        timer = network.cut_after(DB_TOTAL_TIMEOUT, [sock])
        try:
            if db.get_parameter_status("standard_conforming_strings") != "on":
                # Le contrôle « une seule requête » lit les chaînes selon la norme.
                raise RemoteDatabaseError("standard_conforming_strings is off on this server")
            with db.cursor() as cr:
                # Lecture seule et délai maximal, posés dans la transaction même (un regroupeur
                # de connexions refuserait des options de démarrage).
                cr.execute("SET TRANSACTION READ ONLY")
                cr.execute("SET LOCAL statement_timeout = %s", (DB_TIMEOUT_MS,))
            # Curseur nommé = DECLARE CURSOR : une seule requête de lecture, lue par morceaux.
            with db.cursor(name="bf_bi_source") as cr:
                cr.itersize = min(limit + 1, 2000)
                cr.execute(statement)
                rows = cr.fetchmany(limit + 1)
                header = [d.name for d in cr.description] if cr.description else []
            db.rollback()
        except psycopg2.Error as exc:
            raise RemoteDatabaseError(str(exc)) from None
        finally:
            timer.cancel()
            sock.close()
            db.close()
        return tables.from_sql(header, rows, limit)


class BfBiConnectionTable(models.Model):
    _name = "bf.bi.connection.table"
    _description = "BI connection table"
    _order = "sequence, id"

    connection_id = fields.Many2one("bf.bi.connection", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(
        string="Table", required=True, help="Name used in formulas: =BF.SOURCE(\"code\", \"Table\").")
    request = fields.Text(
        string="Path or query",
        help="Web API: the path added to the base address, e.g. /customers?status=active. "
             "Database: one SELECT query.")
    records_path = fields.Char(
        string="Records path",
        help="JSON only: where the list of records sits in the answer, e.g. result.records. Empty: the answer itself.")
    fmt = fields.Selection([("json", "JSON"), ("csv", "CSV"), ("odata", "OData")], string="Format", default="json")

    _sql_constraints = [
        ("name_unique", "unique(connection_id, name)", "This table name already exists in the connection."),
    ]

    # Une table changée vaut tout de suite dans ce processus ; les autres processus
    # changent de clé de cache avec le write_date de la table.
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.connection_id._bf_clear_cache()
        return records

    def write(self, vals):
        self.connection_id._bf_clear_cache()
        result = super().write(vals)
        self.connection_id._bf_clear_cache()
        return result

    def unlink(self):
        self.connection_id._bf_clear_cache()
        return super().unlink()

    @api.constrains("request")
    def _check_request(self):
        for table in self:
            kind = table.connection_id.kind
            request = (table.request or "").strip()
            if kind == "web" and (re.search(r"[\s\\]", request) or "://" in request or request.startswith("//")):
                raise ValidationError(_("The path is added to the base address: no spaces, no other address."))
            if kind == "postgresql":
                if not request:
                    raise ValidationError(_("Write the query of this table."))
                try:
                    tables.single_statement(request)
                except tables.SourceFormatError:
                    raise ValidationError(_("The query must be one single statement.")) from None


class BfBiConnectionCustomer(models.Model):
    """Correspondance d'une valeur client de la source avec un contact d'Odoo.

    Les mesures externes de la connexion suivent le filtre Client du tableau de bord par
    cette table. Odoo la pré-remplit (action_collect_customers) ; l'administrateur vérifie.
    """
    _name = "bf.bi.connection.customer"
    _description = "BI connection customer correspondence"
    _order = "partner_id desc, name"

    _check_company_auto = True

    connection_id = fields.Many2one("bf.bi.connection", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="connection_id.company_id")
    name = fields.Char(string="Value in the source", required=True)
    key = fields.Char(compute="_compute_key", store=True, index=True)
    # Un contact de la société de la connexion (ou sans société) : sinon ses lecteurs verraient
    # « Client n° 77 » et sa somme pour un contact qu'ils ne peuvent pas lire.
    partner_id = fields.Many2one("res.partner", string="Contact", ondelete="set null", check_company=True,
                                 help="Empty: rows with this value count under no customer.")
    suggested = fields.Boolean(string="Suggested by Odoo", help="Matched automatically: check it.")

    _sql_constraints = [
        ("key_unique", "unique(connection_id, key)", "This value already has a correspondence in the connection."),
    ]

    @api.depends("name")
    def _compute_key(self):
        for row in self:
            row.key = external.customer_key(row.name)

    @api.constrains("key")
    def _check_key(self):
        for row in self:
            if not row.key:
                raise ValidationError(_("Write the value as it appears in the source."))

    def write(self, vals):
        if "partner_id" in vals and "suggested" not in vals:
            vals = dict(vals, suggested=False)  # choisi par une personne : plus une suggestion
        return super().write(vals)

    @api.model
    def _bf_suggest(self, values, company=None):
        """{clé : id de contact} pour les valeurs `values` ({clé : texte}) qui désignent UN contact.

        Par référence, NEQ, numéro de taxe ou courriel d'abord, puis par nom réduit (sans
        accents, ponctuation ni forme juridique). Deux contacts possibles : pas de suggestion.
        Seulement les contacts de la société de la connexion (et les contacts sans société).
        """
        if not values:
            return {}
        domain = [("parent_id", "=", False)]
        if company:
            domain.append(("company_id", "in", [company.id, False]))
        partners = self.env["res.partner"].sudo().search_read(domain, ["name", "ref", "company_registry", "vat", "email"])
        index = {}

        def add(kind, value, partner_id):
            if value:
                index.setdefault((kind, value), set()).add(partner_id)

        def ident(value):
            # Une référence, un NEQ ou un numéro de taxe porte des chiffres : un nom n'en est pas un.
            text = re.sub(r"[^0-9A-Z]", "", str(value).upper())
            return text if re.search(r"[0-9]", text) else None

        for p in partners:
            add("ref", ident(p["ref"]) if p["ref"] else None, p["id"])
            add("ref", ident(p["company_registry"]) if p["company_registry"] else None, p["id"])
            add("ref", ident(p["vat"]) if p["vat"] else None, p["id"])
            add("email", (p["email"] or "").strip().casefold() or None, p["id"])
            add("name", external.normalized_name(p["name"]), p["id"])
        found = {}
        for key, text in values.items():
            for probe in (("ref", ident(text)), ("email", text.strip().casefold()), ("name", external.normalized_name(text))):
                ids = index.get(probe)
                if ids:
                    if len(ids) == 1:
                        found[key] = next(iter(ids))
                    break
        return found

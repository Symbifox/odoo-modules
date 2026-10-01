"""Connexions de données externes des tableaux de bord.

Une source externe n'a pas la sécurité d'Odoo : aucune règle d'enregistrement ne la
filtre. La portée se décide donc sur la connexion elle-même :
- une connexion n'est ouverte à PERSONNE tant qu'on ne lui donne pas de groupes ou de
  personnes ;
- chaque lecture se fait au nom de la personne qui ouvre le tableau de bord (dans son
  navigateur), et le serveur vérifie qu'elle y a droit AVANT d'appeler la source ;
- la clé d'API n'est lisible que des administrateurs, jamais envoyée aux lecteurs.
"""
import datetime
import logging
import re
import time
from urllib.parse import quote, urlparse

import requests

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)

TIMEOUT = 15
CACHE_MAX = 50  # tables gardées au plus, par processus
CACHE_TTL = 60  # secondes : un tableau de bord ouvert par dix personnes n'appelle Grist qu'une fois
_CACHE = {}

# Serial de tableur : jours depuis le 1899-12-30.
_SPREADSHEET_EPOCH = datetime.datetime(1899, 12, 30, tzinfo=datetime.timezone.utc)


def _to_serial(seconds):
    moment = datetime.datetime.fromtimestamp(seconds, tz=datetime.timezone.utc)
    return (moment - _SPREADSHEET_EPOCH).total_seconds() / 86400


class BfBiConnection(models.Model):
    _name = "bf.bi.connection"
    _description = "BI data connection"
    _order = "name"

    name = fields.Char(string="Name", required=True)
    code = fields.Char(
        string="Code", required=True, copy=False,
        help="Short name used in formulas: =BF.SOURCE(\"code\", \"Table\"). Lowercase letters, digits and _ only.")
    kind = fields.Selection([("grist", "Grist")], string="Type", required=True, default="grist")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", string="Company", default=lambda self: self.env.company)
    group_ids = fields.Many2many(
        "res.groups", "bf_bi_connection_group_rel", "connection_id", "group_id", string="Authorized groups",
        help="Members of these groups see this source's data in dashboards. Empty and with no authorized person: the connection is open to nobody.")
    user_ids = fields.Many2many(
        "res.users", "bf_bi_connection_user_rel", "connection_id", "user_id", string="Authorized people")
    is_open = fields.Boolean(string="Open to someone", compute="_compute_is_open")
    row_limit = fields.Integer(string="Maximum rows", default=5000, required=True)
    grist_url = fields.Char(string="Grist server address", help="e.g. https://grist.example.com")
    grist_doc_id = fields.Char(string="Grist document ID")
    grist_api_key = fields.Char(string="Grist API key", groups="base.group_system", copy=False)
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

    @api.constrains("grist_url")
    def _check_grist_url(self):
        for connection in self.filtered("grist_url"):
            url = urlparse(connection.grist_url)
            if url.scheme not in ("http", "https") or not url.netloc:
                raise ValidationError(_("The Grist address must start with https:// (or http://)."))

    @api.constrains("row_limit")
    def _check_row_limit(self):
        for connection in self:
            if not 1 <= connection.row_limit <= 50000:
                raise ValidationError(_("The row limit goes from 1 to 50,000."))

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
        for key in [k for k in _CACHE if k[0] == dbname and k[1] in self.ids]:
            _CACHE.pop(key, None)

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
        return connection._grist_tables()

    @api.model
    def bf_fetch_table(self, code, table):
        """Table `table` de la source `code` : {"header": [...], "rows": [[...]], "truncated": bool}.

        Appelée par la fonction =BF.SOURCE dans le navigateur de la personne qui regarde.
        """
        connection = self._bf_get_for_use(code)
        if not isinstance(table, str) or not table:
            raise UserError(_("Missing table name."))
        key = (self.env.cr.dbname, connection.id, str(connection.write_date), table)
        hit = _CACHE.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_TTL:
            return hit[1]
        data = connection._grist_fetch(table)
        maintenant = time.monotonic()
        for vieille in [k for k, (t, _d) in _CACHE.items() if maintenant - t >= CACHE_TTL]:
            _CACHE.pop(vieille, None)
        # Plafond dur : au-delà, on retire les plus anciennes (la mémoire du processus est bornée).
        while len(_CACHE) >= CACHE_MAX:
            _CACHE.pop(min(_CACHE, key=lambda k: _CACHE[k][0]), None)
        _CACHE[key] = (maintenant, data)
        return data

    def action_test_connection(self):
        self.ensure_one()
        # Méthode publique : sans ceci, n'importe quel employé listerait les tables de
        # toutes les connexions, même fermées, en passant leurs ids.
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only administrators can test a data connection."))
        tables = self._grist_tables()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("Connection successful: %(n)s table(s) (%(names)s).",
                             n=len(tables), names=", ".join(tables[:10])),
            },
        }

    # ------------------------------------------------------------------
    # Grist
    # ------------------------------------------------------------------

    def _grist_get(self, path, params=None):
        self.ensure_one()
        conn = self.sudo()
        if not (conn.grist_url and conn.grist_doc_id and conn.grist_api_key):
            raise UserError(_("Connection “%s” is incomplete: address, document and key are required.", conn.name))
        url = "%s/api/docs/%s%s" % (conn.grist_url.rstrip("/"), quote(conn.grist_doc_id, safe=""), path)
        try:
            response = requests.get(
                url, params=params, timeout=TIMEOUT,
                headers={"Authorization": "Bearer %s" % conn.grist_api_key})
        except requests.RequestException as exc:
            _logger.warning("bf_bi : Grist injoignable pour la connexion %s : %s", conn.code, type(exc).__name__)
            raise UserError(_("Source “%s” unreachable.", conn.code)) from None
        if response.status_code != 200:
            # Le corps de la réponse n'est pas renvoyé : il peut porter des détails du serveur.
            raise UserError(_("Source “%(code)s”: Grist answered %(status)s.",
                              code=conn.code, status=response.status_code))
        return response.json()

    def _grist_tables(self):
        data = self._grist_get("/tables")
        return [t["id"] for t in data.get("tables", [])]

    def _grist_fetch(self, table):
        path = "/tables/%s" % quote(table, safe="")
        columns = [
            c for c in self._grist_get(path + "/columns").get("columns", [])
            if not c["id"].startswith("gristHelper_") and c["id"] != "manualSort"
        ]
        limit = self.sudo().row_limit
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
                serial = _to_serial(value)
                return round(serial) if grist_type == "Date" else serial
        return value

"""Mesures nommées : une valeur définie une fois, réutilisée dans tous les tableaux de bord.

Toujours calculée AU NOM de la personne qui demande (jamais en sudo) : ses règles
d'enregistrement s'appliquent, comme pour un tableau croisé. C'est le socle des alertes,
des abonnements et des questions à Gen, qui calculeront au nom de l'abonné.
"""
import ast
import math
import operator
import re

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.osv import expression
from odoo.tools.safe_eval import datetime as safe_datetime, safe_eval

from . import bf_bi_external as external

AGGREGATORS = [("sum", "Sum"), ("avg", "Average"), ("min", "Minimum"), ("max", "Maximum"), ("count", "Count")]
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
MAX_DEPTH = 10


def _check_domain_terms(domain):
    """Un domaine : une liste d'opérateurs '&', '|', '!' et de triplets (champ, opérateur, valeur)."""
    if not isinstance(domain, list):
        raise ValueError("not a list")
    for term in domain:
        if term in ("&", "|", "!"):
            continue
        if (not isinstance(term, (list, tuple)) or len(term) != 3 or not isinstance(term[0], str)
                or term[1] not in expression.TERM_OPERATORS):
            raise ValueError("bad term")


class _NoValue(Exception):
    """Interne : une division par zéro rend la mesure sans valeur."""


class BfBiMeasure(models.Model):
    _name = "bf.bi.measure"
    _description = "BI named measure"
    _order = "name"

    name = fields.Char(string="Name", required=True, translate=True)
    code = fields.Char(
        string="Code", required=True, copy=False,
        help="Short name used in formulas: =BF.MEASURE(\"code\"). Lowercase letters, digits and _ only.")
    description = fields.Text(string="Description", translate=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", string="Company", default=lambda self: self.env.company)
    kind = fields.Selection(
        [("aggregate", "Aggregate"), ("formula", "Formula"), ("external", "External source")],
        string="Kind", required=True, default="aggregate")
    comparison = fields.Selection(
        [("percentage", "In percent"), ("difference", "In points")], string="Compare with previous period",
        required=True, default="percentage",
        help="How an indicator tile shows the change. In points for a value that can be negative (NPS) "
             "or that is already a rate (margin rate): a percentage of a percentage reads badly.")
    unit = fields.Selection(
        [("number", "Number"), ("currency", "Amount"), ("percent", "Percentage"), ("hours", "Hours")],
        string="Unit", required=True, default="number")
    # Agrégat
    model_id = fields.Many2one("ir.model", string="Model", ondelete="cascade")
    # Noms stockés : calculer une mesure ne doit pas relire ir.model, que seuls les
    # administrateurs peuvent lire en Odoo 18 (le lecteur d'un tableau de bord non).
    model_name = fields.Char(related="model_id.model", string="Model name", store=True)
    field_name = fields.Char(related="field_id.name", store=True)
    # Champs que suivent les filtres Période et Client du tableau de bord. Une mesure externe
    # annonce des champs fictifs, que bf_bi_external sait appliquer à ses lignes.
    date_field_name = fields.Char(compute="_compute_filter_fields", store=True, compute_sudo=True)
    date_field_type = fields.Selection(
        [("date", "Date"), ("datetime", "Date and time")], compute="_compute_filter_fields", store=True,
        compute_sudo=True)
    partner_field_name = fields.Char(compute="_compute_filter_fields", store=True, compute_sudo=True)
    field_id = fields.Many2one(
        "ir.model.fields", string="Field", ondelete="cascade",
        domain="[('model_id', '=', model_id), ('ttype', 'in', ('integer', 'float', 'monetary'))]",
        help="Empty: count the records.")
    aggregator = fields.Selection(AGGREGATORS, string="Aggregation", default="sum")
    domain = fields.Char(
        string="Filter", default="[]",
        help="Records to keep. May use uid: \"My timesheets\" then gives each person their own.")
    date_field_id = fields.Many2one(
        "ir.model.fields", string="Date field", ondelete="set null",
        domain="[('model_id', '=', model_id), ('ttype', 'in', ('date', 'datetime'))]",
        help="Field the dashboard's Period filter applies to.")
    partner_field_id = fields.Many2one(
        "ir.model.fields", string="Customer field", ondelete="set null",
        domain="[('model_id', '=', model_id), ('ttype', '=', 'many2one'), ('relation', '=', 'res.partner')]",
        help="Field the dashboard's Customer filter applies to.")
    # Formule
    expression = fields.Char(
        string="Formula", help="Other measures' codes with + - * / and parentheses, e.g. revenue - cost.")
    # Source externe : un code et des noms, pas de lien vers la connexion, que seuls les
    # administrateurs lisent (elle porte ses secrets).
    connection_code = fields.Char(
        string="Data connection", help="Code of the data connection, as in =BF.SOURCE(\"code\", \"Table\").")
    source_table = fields.Char(string="Table", help="Table of the connection: a sheet, or a table the administrator defined.")
    value_column = fields.Char(string="Column to aggregate", help="Empty: count the rows.")
    date_column = fields.Char(string="Date column", help="Column the dashboard's Period filter applies to.")
    customer_column = fields.Char(
        string="Customer column",
        help="Column the dashboard's Customer filter applies to, through the connection's customer correspondence.")

    _sql_constraints = [("code_unique", "unique(code)", "This measure code already exists.")]

    @api.constrains("code")
    def _check_code(self):
        for measure in self:
            # Commence par une lettre : sinon une formule ne pourrait pas y renvoyer.
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", measure.code or ""):
                raise ValidationError(_("The code starts with a lowercase letter, then lowercase letters, "
                                        "digits and _ (40 at most)."))

    @api.depends("kind", "date_field_id.name", "date_field_id.ttype", "partner_field_id.name",
                 "date_column", "customer_column")
    def _compute_filter_fields(self):
        for measure in self:
            if measure.kind == "external":
                measure.date_field_name = external.DATE_FIELD if measure.date_column else False
                measure.date_field_type = "date" if measure.date_column else False
                measure.partner_field_name = external.CUSTOMER_FIELD if measure.customer_column else False
            else:
                measure.date_field_name = measure.date_field_id.name or False
                measure.date_field_type = measure.date_field_id.ttype or False
                measure.partner_field_name = measure.partner_field_id.name or False

    @api.constrains("kind", "model_id", "expression", "domain", "connection_code", "source_table",
                    "value_column", "date_column", "customer_column", "aggregator")
    def _check_definition(self):
        for measure in self:
            if measure.kind == "aggregate":
                if not measure.model_id:
                    raise ValidationError(_("An aggregate measure needs a model."))
                measure._eval_domain()
            elif measure.kind == "external":
                if not (measure.connection_code and measure.source_table):
                    raise ValidationError(_("An external measure needs a data connection and a table."))
                if not self.env.su and not self.env.user.has_group("base.group_system"):
                    # Un concepteur ne bâtit que sur une connexion dont il voit lui-même les données.
                    try:
                        measure._bf_external_connection()
                    except AccessError:
                        raise ValidationError(_("Measure “%s”: you cannot use this data connection.",
                                                measure.code)) from None
            else:
                measure._parse_formula()
                measure._leaves()  # cycles et codes inconnus

    # ------------------------------------------------------------------
    # Définition
    # ------------------------------------------------------------------

    def _eval_context(self):
        # 🔴 AUCUN enregistrement ni environnement dans ce contexte : safe_eval laisse appeler
        # leurs méthodes (user.sudo().write(...)), et le filtre d'une mesure est écrit par un
        # concepteur, puis évalué au nom de chaque lecteur. Un id, des dates, rien d'autre.
        today = fields.Date.context_today(self)
        return {
            "uid": self.env.uid,
            "context_today": lambda: today,
            # Modules encapsulés d'Odoo : safe_eval refuse un module brut.
            "datetime": safe_datetime,
            "relativedelta": relativedelta,
        }

    def _eval_domain(self):
        self.ensure_one()
        try:
            domain = safe_eval(self.domain or "[]", self._eval_context())
            if self.model_name:
                # Compiler sans exécuter : un champ inconnu est refusé dès la définition.
                self.env[self.model_name].sudo()._search(domain)
        except Exception:
            raise ValidationError(_("Measure “%s”: the filter is not a valid domain.", self.code)) from None
        return domain

    def _parse_formula(self):
        self.ensure_one()
        try:
            tree = ast.parse(self.expression or "", mode="eval")
        except SyntaxError:
            raise ValidationError(_("Measure “%s”: the formula cannot be read.", self.code)) from None
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.USub, ast.UAdd, ast.Name,
                                     ast.Load, ast.Constant, *_OPS)):
                raise ValidationError(_("Measure “%s”: only measure codes, numbers, + - * / and parentheses "
                                        "are allowed.", self.code))
            if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
                raise ValidationError(_("Measure “%s”: only numbers are allowed as constants.", self.code))
        return tree

    def _find(self, code):
        measure = self.search([("code", "=", code)], limit=1)
        if not measure:
            raise UserError(_("Unknown measure “%s”.", code))
        return measure

    def _leaves(self, _path=(), _memo=None):
        """Mesures d'agrégat dont celle-ci dépend (elle-même si c'est un agrégat)."""
        self.ensure_one()
        memo = {} if _memo is None else _memo
        if self.code in _path:
            raise ValidationError(_("Measure “%s” refers to itself.", self.code))
        if len(_path) > MAX_DEPTH:
            raise ValidationError(_("Measure “%s”: formulas are nested too deeply.", self.code))
        if self.code in memo:
            return memo[self.code]
        if self.kind in ("aggregate", "external"):
            memo[self.code] = self
            return self
        leaves = self.browse()
        names = {n.id for n in ast.walk(self._parse_formula()) if isinstance(n, ast.Name)}
        for code in sorted(names):
            leaves |= self._find(code)._leaves(_path + (self.code,), memo)
        memo[self.code] = leaves
        return leaves

    # ------------------------------------------------------------------
    # Calcul, au nom de la personne qui demande
    # ------------------------------------------------------------------

    def _aggregate(self, extra_domain):
        self.ensure_one()
        Model = self.env[self.model_name]  # PAS de sudo : les règles de la personne s'appliquent
        domain = expression.AND([self._eval_domain(), extra_domain or []])
        if not self.field_name or self.aggregator == "count":
            return float(Model.search_count(domain))
        spec = "%s:%s" % (self.field_name, self.aggregator)
        groups = Model.read_group(domain, [spec], [])
        value = groups[0].get(self.field_name) if groups else None
        if value is None or (self.aggregator in ("avg", "min", "max") and not groups[0].get("__count")):
            # Moyenne, min ou max sur aucune ligne : pas de valeur (0 serait faux).
            return None if self.aggregator in ("avg", "min", "max") else 0.0
        return float(value)

    # ------------------------------------------------------------------
    # Source externe
    # ------------------------------------------------------------------

    def _bf_external_connection(self):
        """La connexion de la mesure, si la personne courante peut s'en servir. Sinon AccessError."""
        self.ensure_one()
        return self.env["bf.bi.connection"]._bf_get_for_use(self.connection_code)

    def _bf_external_rows(self):
        """[(date, contact, valeur)] des lignes de la table, lues au nom de la personne courante.

        Un visuel du rapport recalcule une mesure des dizaines de fois : les lignes converties
        (date, clé client, valeur) sont gardées pour la transaction, liées à l'objet même que
        sert le cache des tables ; seuls le droit, la correspondance et le filtre se refont.
        """
        self.ensure_one()
        connection = self._bf_external_connection()
        data = connection._bf_read(self.source_table)
        memo_key = ("bf_bi_external_rows", self.id, self.value_column, self.date_column, self.customer_column)
        memo = self.env.cr.cache.get(memo_key)
        if memo and memo[0] is data:
            projected = memo[1]
        else:
            projected = self._bf_external_project(connection, data)
            self.env.cr.cache[memo_key] = (data, projected)
        if not self.customer_column:
            return projected
        mapping = connection._bf_customer_map()
        return [(day, mapping.get(key), value) for day, key, value in projected]

    def _bf_external_project(self, connection, data):
        """[(date, clé client, valeur)] de `data`, colonnes vérifiées."""
        if data["truncated"]:
            # Une somme sur une partie des lignes serait fausse sans le dire.
            raise UserError(_("Measure “%(code)s”: the table has more rows than the connection's maximum "
                              "(%(limit)s); the administrator can raise it.", code=self.code,
                              limit=connection.sudo().row_limit))
        if not data["rows"]:
            # Une requête « 30 derniers jours » sans ligne, une API qui rend [] : rien à compter,
            # pas une erreur (sans ligne, les colonnes et leurs types ne se lisent pas).
            return []
        header = [str(h).strip() for h in data["header"]]
        types = data["types"]

        def column(name, accepts, refusal):
            if not name:
                return None
            name = name.strip()
            if name not in header:
                raise UserError(_("Measure “%(code)s”: the table has no column “%(column)s”.",
                                  code=self.code, column=name))
            index = header.index(name)
            # Une colonne entièrement vide est lue « Text » : elle ne vaut rien, elle n'est pas fausse.
            filled = any(row[index] is not None and row[index] != "" for row in data["rows"])
            if accepts and filled and not accepts(types[index]):
                raise UserError(refusal)
            return index

        value = column(self.value_column, lambda kind: kind in external.NUMBER_TYPES,
                       _("Measure “%(code)s”: column “%(column)s” does not hold numbers only.",
                         code=self.code, column=self.value_column))
        day = column(self.date_column, external.is_date_type,
                     _("Measure “%(code)s”: column “%(column)s” does not hold dates only.",
                       code=self.code, column=self.date_column))
        customer = column(self.customer_column, None, None)
        return [(
            external.serial_to_date(row[day]) if day is not None else None,
            external.customer_key(row[customer]) if customer is not None else None,
            row[value] if value is not None else None,
        ) for row in data["rows"]]

    def _bf_external_filter(self, extra_domain):
        def children(ids):
            return self.env["res.partner"].sudo().with_context(active_test=False)._search([("id", "child_of", ids)])
        try:
            return external.compile_filter(extra_domain or [], children)
        except external.InvalidFilter:
            raise UserError(_("Invalid filters.")) from None

    def _bf_external_fold(self, values):
        if self.aggregator not in ("sum", "avg", "min", "max"):
            raise UserError(_("Measure “%s”: choose an aggregation.", self.code))
        try:
            return external.fold(values, self.aggregator)
        except external.TooLarge:
            raise UserError(_("Measure “%s”: the result is too large.", self.code)) from None

    def _bf_external_aggregate(self, extra_domain):
        keep = self._bf_external_filter(extra_domain)
        rows = [r for r in self._bf_external_rows() if keep(r[0], r[1])]
        if not self.value_column or self.aggregator == "count":
            return float(len(rows))
        return self._bf_external_fold([r[2] for r in rows])

    def _bf_external_groups(self, extra_domain, limit):
        """[(id de contact, valeur)] des contacts qui ont des lignes, les plus forts d'abord."""
        keep = self._bf_external_filter(extra_domain)
        groups = {}
        for day, partner, value in self._bf_external_rows():
            if partner is not None and keep(day, partner):
                groups.setdefault(partner, []).append(value)
        count = not self.value_column or self.aggregator == "count"
        scored = [(p, float(len(v)) if count else self._bf_external_fold(v)) for p, v in groups.items()]
        scored.sort(key=lambda pv: (pv[1] is None, -(pv[1] or 0)))
        return scored[:limit]

    def _compute(self, leaf_domains, _path=(), _memo=None):
        self.ensure_one()
        # Mémo par appel : une sous-mesure partagée n'est calculée qu'une fois (sinon le
        # coût double à chaque niveau d'un graphe de formules).
        memo = {} if _memo is None else _memo
        if self.code in memo:
            return memo[self.code]
        if self.kind == "aggregate":
            memo[self.code] = self._aggregate(leaf_domains.get(self.code))
            return memo[self.code]
        if self.kind == "external":
            memo[self.code] = self._bf_external_aggregate(leaf_domains.get(self.code))
            return memo[self.code]
        values = {}
        for node in ast.walk(self._parse_formula()):
            if isinstance(node, ast.Name) and node.id not in values:
                if node.id in _path:
                    raise UserError(_("Measure “%s” refers to itself.", self.code))
                if len(_path) > MAX_DEPTH:
                    raise UserError(_("Measure “%s”: formulas are nested too deeply.", self.code))
                values[node.id] = self._find(node.id)._compute(leaf_domains, _path + (self.code,), memo)

        def ev(node):
            if isinstance(node, ast.Expression):
                return ev(node.body)
            if isinstance(node, ast.Constant):
                return float(node.value)
            if isinstance(node, ast.Name):
                if values[node.id] is None:
                    raise UserError(_("Measure “%s”: no value for “%s”.", self.code, node.id))
                return values[node.id]
            if isinstance(node, ast.UnaryOp):
                return -ev(node.operand) if isinstance(node.op, ast.USub) else ev(node.operand)
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Div) and right == 0:
                raise _NoValue()
            return _OPS[type(node.op)](left, right)

        try:
            result = ev(self._parse_formula())
        except _NoValue:
            # Division par zéro = rien à mesurer (un taux de marge sans revenus sur la période,
            # surtout la période précédente à même durée en début d'année) : « pas de valeur »,
            # comme une moyenne sur rien, et non une erreur dans la tuile.
            memo[self.code] = None
            return None
        if not math.isfinite(result):
            raise UserError(_("Measure “%s”: the result is too large.", self.code))
        memo[self.code] = result
        return result

    @staticmethod
    def _check_leaf_domains(leaf_domains):
        """Les domaines viennent du navigateur : ils ne font que RESTREINDRE (les droits de la
        personne s'appliquent de toute façon), mais on refuse ce qui n'en est pas un."""
        if not isinstance(leaf_domains, dict):
            raise UserError(_("Invalid filters."))
        for domain in leaf_domains.values():
            try:
                _check_domain_terms(domain)
            except ValueError:
                raise UserError(_("Invalid filters.")) from None

    @api.model
    def bf_describe(self, code):
        """Ce que le tableur doit savoir pour appliquer les filtres du tableau de bord."""
        measure = self._find(code)
        return {
            "code": measure.code,
            "name": measure.name,
            "unit": measure.unit,
            "leaves": [
                {
                    "code": leaf.code,
                    "date_field": leaf.date_field_name or False,
                    "date_type": leaf.date_field_type or False,
                    "partner_field": leaf.partner_field_name or False,
                }
                for leaf in measure._leaves()
            ],
        }

    @api.model
    def bf_value(self, code, leaf_domains=None):
        leaf_domains = leaf_domains or {}
        self._check_leaf_domains(leaf_domains)
        value = self._find(code)._compute(leaf_domains)
        # « Pas de valeur » : False, que XML-RPC sait transporter (pas None).
        return False if value is None else value

    @api.model
    def bf_list(self):
        # has_date : la mesure suit la période ; sinon, la comparer à la période précédente
        # n'a pas de sens (même valeur, « 0 % » trompeur).
        # Une mesure cassée (formule qui vise une mesure archivée, ou d'une société que la
        # personne ne voit pas) est laissée de côté : sinon l'éditeur ne s'ouvrirait plus
        # pour AUCUN concepteur. Elle se répare depuis sa fiche, qui dit ce qui manque.
        result = []
        for m in self.search([]):
            try:
                has_date = all(leaf.date_field_name for leaf in m._leaves())
            except UserError:
                continue
            result.append({"code": m.code, "name": m.name, "unit": m.unit, "comparison": m.comparison,
                           "has_date": has_date})
        return result

    def action_test(self):
        self.ensure_one()
        value = self._compute({})
        message = (_("No value for you, without filters (nothing to measure, or a division by zero).")
                   if value is None else _("Value for you, without filters: %s", round(value, 2)))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"type": "info", "message": message},
        }

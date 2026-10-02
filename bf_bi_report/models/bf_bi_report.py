"""Rapports BI à la Power BI, sur les mesures nommées de bf_bi.

Le moteur ne lit aucun modèle par lui-même : une valeur de visuel est toujours une mesure
nommée, calculée par `bf.bi.measure._compute` sous les droits de la personne qui regarde.
Découper une mesure (par mois, trimestre, année ou client), c'est ajouter un terme au filtre
de chacune de ses mesures de base. La surface neuve reste donc mince : valider la demande,
bâtir des filtres, appeler le calcul existant.
"""
import ast
import datetime
import json
import re

import pytz
from dateutil.relativedelta import relativedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.osv import expression

from .templates import TEMPLATE_KEYS, TEMPLATES, template_codes

CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
DATE_RE = re.compile(r"^(19|20|21)\d\d-\d\d-\d\d$")
DIMENSIONS = ("month", "quarter", "year", "customer")
TIME_DIMENSIONS = ("month", "quarter", "year")
VISUAL_TYPES = ("kpi", "column", "bar", "line", "donut", "table", "gauge", "waterfall", "heatmap")
SINGLE_MEASURE_TYPES = ("gauge", "waterfall", "heatmap")
RICH_KINDS = ("waterfall", "heatmap")  # calculs à part dans bf_query
HEAT_COLUMNS = ("month", "quarter")
MAX_RICH_LIMIT = 15  # cascade et carte thermique : chaque ligne coûte plusieurs calculs
THEMES = ("default", "company")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
PRESETS = ("month", "quarter", "year", "last_year")
MAX_MEASURES = 8
MAX_LIMIT = 50
# Plafonds d'un appel : clients candidats, puis calculs (mesures × compartiments). Un appel
# reste ainsi dans quelques centaines de calculs, quoi que demande le navigateur.
MAX_MEMBERS = 50
MAX_COMPUTATIONS = 450
MAX_CUSTOMERS = 200
MAX_VISUALS = 24
MAX_PAGES = 20
MAX_PAGE_CUSTOMERS = 50
PAGE_PRESETS = PRESETS  # une page peut fixer sa période, quoi que dise le segment
MAX_LAYOUT_BYTES = 64 * 1024  # une page de 24 visuels tient en quelques kilo-octets
VISUAL_KEYS = {"id", "type", "title", "measures", "dimension", "limit", "target", "x", "y", "w", "h", "max", "columns"}
VERSIONS_KEPT = 30
GRID_COLUMNS = 12
GRID_ROWS = 60
TREND_MONTHS = 12


def _constant(node):
    """La valeur d'une expression faite seulement de nombres (2, -1, 2 * 3, 1 / 4), sinon None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _constant(node.operand)
        return None if value is None else (-value if isinstance(node.op, ast.USub) else value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
        left, right = _constant(node.left), _constant(node.right)
        if left is None or right is None or (isinstance(node.op, ast.Div) and not right):
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        return left / right
    return None


def is_additive(measure, _memo=None, _path=()):
    """Vrai si les valeurs de la mesure s'additionnent d'un client ou d'un mois à l'autre.

    Une somme ou un compte, ou une formule qui n'en fait que des sommes, des différences et des
    multiples par une constante (marge = revenus - coûts, double = contacts * 2, x * -1). Un
    taux, une moyenne, un minimum ou un maximum ne s'additionnent pas : une cascade en serait
    fausse. Mémorisé par code : une formule qui cite plusieurs fois les mêmes mesures, sur
    plusieurs niveaux, ne coûte qu'une visite par mesure.
    """
    memo = {} if _memo is None else _memo
    if measure.code in memo:
        return memo[measure.code]
    if measure.code in _path or len(_path) > 10:
        return False
    if measure.kind == "aggregate":
        memo[measure.code] = measure.aggregator in ("sum", "count")
        return memo[measure.code]
    try:
        tree = measure._parse_formula()
    except Exception:  # noqa: BLE001 — une formule illisible n'est simplement pas additive
        memo[measure.code] = False
        return False
    path = _path + (measure.code,)

    def additive(node):
        if isinstance(node, ast.Expression):
            return additive(node.body)
        if isinstance(node, ast.Name):
            try:
                return is_additive(measure._find(node.id), memo, path)
            except Exception:  # noqa: BLE001
                return False
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return additive(node.operand)
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, (ast.Add, ast.Sub)):
                return additive(node.left) and additive(node.right)
            if isinstance(node.op, ast.Mult):
                return ((_constant(node.left) is not None and additive(node.right))
                        or (_constant(node.right) is not None and additive(node.left)))
            if isinstance(node.op, ast.Div):
                return bool(_constant(node.right)) and additive(node.left)  # ni une mesure ni zéro
        return False

    memo[measure.code] = additive(tree)
    return memo[measure.code]


def _month_start(day):
    return day.replace(day=1)


def period_bounds(preset, today):
    """Bornes [début, fin] d'une période civile qui contient `today` (ou l'année d'avant)."""
    if preset == "month":
        start = _month_start(today)
        return start, start + relativedelta(months=1, days=-1)
    if preset == "quarter":
        start = datetime.date(today.year, (today.month - 1) // 3 * 3 + 1, 1)
        return start, start + relativedelta(months=3, days=-1)
    if preset == "year":
        return datetime.date(today.year, 1, 1), datetime.date(today.year, 12, 31)
    if preset == "last_year":
        return datetime.date(today.year - 1, 1, 1), datetime.date(today.year - 1, 12, 31)
    raise UserError(_("Unknown period."))


def bucket_bounds(dimension, day):
    """Le mois, le trimestre ou l'année qui contient `day`."""
    if dimension == "month":
        return period_bounds("month", day)
    if dimension == "quarter":
        return period_bounds("quarter", day)
    return period_bounds("year", day)


def previous_period(start, end, today):
    """La période précédente, de même longueur calendaire.

    Une période de mois entiers est précédée par autant de mois. Si la période est en cours
    (elle contient `today`), la précédente est coupée à la MÊME date : du 1er janvier au
    1er octobre contre du 1er janvier au 1er octobre de l'an dernier. L'écart se compte en
    mois et en jours civils, jamais en durée : le 1er mars répond au 1er mars, année
    bissextile ou non, et le 30 avril au 30 mars.
    """
    months = (end.year - start.year) * 12 + end.month - start.month + 1
    p_start = start - relativedelta(months=months)
    p_end = start - relativedelta(days=1)
    if start <= today <= end:
        # Même rang de mois et même quantième, borné à la fin de ce mois : le 31 mai d'un
        # trimestre répond au 28 février, pas au 3 mars.
        rank = (today.year - start.year) * 12 + today.month - start.month
        month = p_start + relativedelta(months=rank)
        last = (month + relativedelta(months=1, days=-1)).day
        p_end = min(month.replace(day=min(today.day, last)), p_end)
    return p_start, p_end


class BfBiReport(models.Model):
    _name = "bf.bi.report"
    _description = "BI report"
    _order = "sequence, name, id"
    _check_company_auto = True

    name = fields.Char(string="Name", required=True, translate=True)
    description = fields.Text(string="Description", translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    theme = fields.Selection(
        [("default", "Symbifox"), ("company", "Company colors")], string="Theme", default="default", required=True,
        help="Company colors start the palette with the colors of the company (Settings › Companies › "
             "Document layout), adjusted so marks stay visible; the rest of the palette follows.")
    company_id = fields.Many2one("res.company", string="Company", required=True, default=lambda self: self.env.company)
    group_ids = fields.Many2many(
        "res.groups", "bf_bi_report_res_groups_rel", "report_id", "group_id", string="Visible to",
        help="People in these groups find the report in the list. A report with no group is visible to "
             "BI designers only, until it is opened to others. This does not hide any figure: every "
             "value follows the access rights of the person looking, wherever it is shown.")
    page_ids = fields.One2many("bf.bi.report.page", "report_id", string="Pages", copy=True)
    page_count = fields.Integer(compute="_compute_page_count", string="Pages count")
    bf_revision = fields.Integer(string="Revision", readonly=True, copy=False, default=0)
    version_ids = fields.One2many("bf.bi.report.version", "report_id", string="Versions", readonly=True)

    @api.depends("page_ids")
    def _compute_page_count(self):
        for report in self:
            report.page_count = len(report.page_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._bf_check_company_choice(vals.get("company_id"))
        return super().create(vals_list)

    def write(self, vals):
        if "company_id" in vals:
            self._bf_check_company_choice(vals["company_id"])
        return super().write(vals)

    def _bf_check_company_choice(self, company_id):
        """Un rapport ne se range que dans une société de la personne (sinon un concepteur de A
        pourrait déposer un rapport chez B)."""
        if self.env.su or not company_id:
            return
        if company_id not in self.env.user.company_ids.ids:
            raise AccessError(_("You can only file a report in one of your companies."))

    @api.model
    def action_create_and_open(self, *_selection):
        """« Nouveau rapport » : une page vide, ouverte dans le concepteur. Un bouton d'en-tête de
        liste passe la sélection (vide ici) en argument."""
        if not self.env.user.has_group("bf_bi.group_bi_designer"):
            raise AccessError(_("Only BI designers can create reports."))
        report = self.create({"name": _("New report"), "page_ids": [Command.create({"name": _("Page 1")})]})
        action = report.action_open()
        action["params"]["design"] = True
        return action

    # ------------------------------------------------------------------
    # Modèles livrés
    # ------------------------------------------------------------------

    @api.model
    def bf_list_templates(self):
        """Les modèles, avec ce qui manque pour ceux qu'on ne peut pas encore créer.

        Réservé aux concepteurs : la réponse dit quels modules sont installés.
        """
        if not self.env.user.has_group("bf_bi.group_bi_designer"):
            raise AccessError(_("Only BI designers can create reports."))
        installed = set(self.env["ir.module.module"].sudo().search(
            [("name", "in", sorted({m for t in TEMPLATES for m in t["modules"]})), ("state", "=", "installed")]).mapped("name"))
        found = set(self.env["bf.bi.measure"].search([("code", "in", sorted({c for t in TEMPLATES for c in template_codes(t)}))])
                    .mapped("code"))
        out = []
        for template in TEMPLATES:
            missing_modules = [m for m in template["modules"] if m not in installed]
            missing_codes = [c for c in template_codes(template) if c not in found]
            out.append({
                "key": template["key"], "name": str(template["name"]), "description": str(template["description"]),
                "available": not missing_modules and not missing_codes,
                "missing_modules": missing_modules, "missing_codes": missing_codes,
            })
        return out

    @api.model
    def bf_create_from_template(self, key):
        """Crée un rapport à partir d'un modèle livré, comme le ferait un concepteur à l'écran."""
        if not self.env.user.has_group("bf_bi.group_bi_designer"):
            raise AccessError(_("Only BI designers can create reports."))
        if key not in TEMPLATE_KEYS:
            raise UserError(_("Unknown template."))
        status = next(t for t in self.bf_list_templates() if t["key"] == key)
        if not status["available"]:
            raise UserError(_("This template needs modules or measures that are not installed: %s.",
                              ", ".join(status["missing_modules"] + status["missing_codes"])))
        template = next(t for t in TEMPLATES if t["key"] == key)
        lang = self.env.lang or "en_US"
        langs = [code for code, _label in self.env["res.lang"].get_installed()]

        def visuals(page):
            out = []
            for visual in page["visuals"]:
                visual = dict(visual, measures=list(visual["measures"]))
                if "title" in visual:
                    visual["title"] = visual["title"]._translate(lang)  # un titre saisi : une seule langue
                out.append(visual)
            return out

        report = self.create({
            "name": template["name"]._translate(lang),
            "description": template["description"]._translate(lang),
            "page_ids": [Command.create({
                "name": page["name"]._translate(lang), "sequence": n, "layout": json.dumps(visuals(page)),
                "drill_dimension": page.get("drill") or False,
            }) for n, page in enumerate(template["pages"], start=1)],
        })
        # Le nom, la description et les pages, dans chaque langue installée.
        report.update_field_translations("name", {code: template["name"]._translate(code) for code in langs})
        report.update_field_translations("description", {code: template["description"]._translate(code) for code in langs})
        for page, source in zip(report.page_ids.sorted("sequence"), template["pages"]):
            page.update_field_translations("name", {code: source["name"]._translate(code) for code in langs})
        return report.action_open()

    def action_open(self):
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "bf_bi_report.report_action",
            "name": self.name,
            "params": {"report_id": self.id},
            "context": {"active_id": self.id},
        }

    # ------------------------------------------------------------------
    # Lecture du rapport par le lecteur
    # ------------------------------------------------------------------

    @api.model
    def bf_get_report(self, report_id):
        report = self.browse(int(report_id)).exists()
        if not report or not report.active:
            raise UserError(_("This report no longer exists."))
        report.check_access("read")
        currency = self.env.company.currency_id
        return {
            "id": report.id,
            "name": report.name,
            "revision": report.bf_revision,
            "can_edit": report._bf_can_edit(),
            "currency": {"symbol": currency.symbol, "position": currency.position},
            "theme": report._bf_theme(),
            "pages": [
                {"id": page.id, "name": page.name, "visuals": page._bf_visuals(), **page._bf_settings()}
                for page in report.page_ids.sorted(lambda p: (p.sequence, p.id))
            ],
        }

    def _bf_theme(self):
        """Le thème et les couleurs de la société du rapport (seulement des #rrggbb : le reste,
        saisi ailleurs, est ignoré). Le navigateur les ajuste pour que les marques restent lisibles."""
        self.ensure_one()
        company = self.company_id.sudo()  # deux couleurs de marque, rien d'autre
        colors = [c for c in (company.primary_color, company.secondary_color) if isinstance(c, str) and HEX_RE.match(c)]
        return {"name": self.theme or "default", "company_colors": colors}

    # ------------------------------------------------------------------
    # Conception
    # ------------------------------------------------------------------

    def _bf_can_edit(self):
        self.ensure_one()
        if not self.env.user.has_group("bf_bi.group_bi_designer"):
            return False
        try:
            self.check_access("write")
        except AccessError:
            return False
        return True

    def _bf_check_editable(self):
        self.ensure_one()
        if not self.env.user.has_group("bf_bi.group_bi_designer"):
            raise AccessError(_("Only BI designers can change a report."))
        self.check_access("write")

    @api.model
    def bf_designer_fields(self):
        """Le volet Champs : les mesures nommées que la personne voit, et ce qu'elles savent
        découper (la période si toutes leurs mesures de base ont une date, les clients si toutes
        ont un champ client). Groupées par source, comme les tables d'un modèle Power BI."""
        if not self.env.user.has_group("bf_bi.group_bi_designer"):
            raise AccessError(_("Only BI designers can design reports."))
        out = []
        for measure in self.env["bf.bi.measure"].search([]):
            try:
                leaves = measure._leaves()
            except (UserError, ValidationError):
                continue  # une mesure cassée se répare depuis sa fiche
            if measure.kind == "aggregate" and measure.model_name in self.env:
                # Le nom du modèle dans la langue de la personne (ir.model, lu en sudo pour ce
                # seul libellé : les employés ne lisent pas ir.model, et un nom de modèle n'a rien
                # de sensible).
                group = self.env["ir.model"].sudo()._get(measure.model_name).with_context(lang=self.env.lang).name \
                    or self.env[measure.model_name]._description
            else:
                group = _("Formulas")
            out.append({
                "code": measure.code, "name": measure.name, "unit": measure.unit, "group": group,
                "has_date": all(leaf.date_field_name for leaf in leaves),
                "has_partner": all(leaf.partner_field_name for leaf in leaves),
                "additive": is_additive(measure),
            })
        formulas = _("Formulas")  # hors du lambda : `_()` n'y trouverait pas la langue de la personne
        out.sort(key=lambda m: (m["group"] == formulas, m["group"], m["name"]))
        return out

    @api.model
    def _bf_check_pages(self, pages):
        if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES:
            raise UserError(_("A report has one to %s pages.", MAX_PAGES))
        ids = [p.get("id") for p in pages if isinstance(p, dict) and p.get("id") is not None]
        if len(ids) != len(set(ids)):
            raise UserError(_("Invalid page."))
        for page in pages:
            if not isinstance(page, dict):
                raise UserError(_("Invalid page."))
            pid, name = page.get("id"), page.get("name")
            if pid is not None and not (isinstance(pid, int) and not isinstance(pid, bool)):
                raise UserError(_("Invalid page."))
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                raise UserError(_("A page needs a name of 1 to 80 characters."))
            error = self.env["bf.bi.report.page"]._bf_layout_errors(page.get("visuals"))
            if error:
                raise UserError(_("Page “%(page)s”: %(error)s.", page=name, error=error))
            error = self.env["bf.bi.report.page"]._bf_settings_errors(page)
            if error:
                raise UserError(_("Page “%(page)s”: %(error)s.", page=name, error=error))

    def bf_save(self, pages, revision, name=None, theme=None):
        """Enregistre le rapport complet (pages et visuels) si `revision` est la courante.

        Renvoie {"status": "saved", "revision": n, "page_ids": [...]} ou, si quelqu'un a
        enregistré entre-temps, {"status": "conflict", "revision": n, "author": nom} sans rien
        écrire. Même mécanique que les tableaux de bord de bf_bi : pas de SQL brut, la
        sérialisation de PostgreSQL départage deux enregistrements simultanés.
        """
        self.ensure_one()
        self._bf_check_editable()
        self._bf_check_pages(pages)
        if name is not None and not (isinstance(name, str) and 1 <= len(name.strip()) <= 120):
            raise UserError(_("Invalid report name."))
        if theme is not None and theme not in THEMES:
            raise UserError(_("Unknown theme."))
        if not isinstance(revision, int) or isinstance(revision, bool):
            raise UserError(_("Invalid revision."))
        self.flush_recordset(["bf_revision"])
        self.invalidate_recordset(["bf_revision"])
        current = self.bf_revision or 0
        if current != revision:
            return {"status": "conflict", "revision": current, "author": self.write_uid.name}
        existing = {page.id: page for page in self.page_ids}
        Page = self.env["bf.bi.report.page"]
        unknown = [p["id"] for p in pages if p.get("id") is not None and p["id"] not in existing]
        if unknown and Page.sudo().search_count([("id", "in", unknown)]):
            # Une page d'un AUTRE rapport : refusée. Une page supprimée entre-temps (par la version
            # gardée d'un collègue) est simplement recréée.
            raise UserError(_("A page of another report cannot be moved here."))
        langs = [code for code, _label in self.env["res.lang"].get_installed()]
        kept, page_ids = set(), []
        for sequence, page in enumerate(pages, start=1):
            name_ = page["name"].strip()
            vals = {"name": name_, "sequence": sequence, "layout": json.dumps(page["visuals"]),
                    "drill_dimension": page.get("drill") or False, "filter_preset": page.get("preset") or False,
                    "filter_customers": json.dumps(page.get("customers") or [])}
            if page.get("id") in existing:
                record = existing[page["id"]]
                renamed = record.name != name_  # dans la langue de la personne qui enregistre
                record.write(vals)
            else:
                record = Page.create(dict(vals, report_id=self.id))
                renamed = True
            if renamed:
                # Un nom saisi vaut pour toutes les langues (le champ est traduisible). Un nom
                # inchangé garde ses traductions (celles d'un modèle livré, par exemple).
                record.update_field_translations("name", {code: name_ for code in langs})
            kept.add(record.id)
            page_ids.append(record.id)
        (self.page_ids.filtered(lambda p: p.id not in kept)).unlink()
        self.write({"bf_revision": current + 1, **({"theme": theme} if theme and theme != self.theme else {})})
        if name and name.strip() != self.name:
            # Un nom saisi vaut pour toutes les langues (sinon un collègue verrait l'ancien).
            self.write({"name": name.strip()})
            langs = [code for code, _label in self.env["res.lang"].get_installed()]
            self.update_field_translations("name", {code: name.strip() for code in langs})
        self.env["bf.bi.report.version"].sudo().create({
            "report_id": self.id, "revision": current + 1,
            "content": json.dumps({"name": self.name, "theme": self.theme, "pages": [
                {"name": p["name"].strip(), "visuals": p["visuals"], "drill": p.get("drill") or None,
                 "preset": p.get("preset") or None, "customers": p.get("customers") or []} for p in pages]}),
        })
        old = self.env["bf.bi.report.version"].sudo().search(
            [("report_id", "=", self.id)], order="revision desc", offset=VERSIONS_KEPT)
        old.unlink()
        return {"status": "saved", "revision": current + 1, "page_ids": page_ids}

    def bf_list_versions(self):
        self.ensure_one()
        self._bf_check_editable()
        return [{"id": v.id, "revision": v.revision, "author": v.create_uid.name,
                 "date": fields.Datetime.to_string(v.create_date)}
                for v in self.env["bf.bi.report.version"].search([("report_id", "=", self.id)], order="revision desc")]

    # ------------------------------------------------------------------
    # Requête d'un visuel
    # ------------------------------------------------------------------

    @api.model
    def _bf_today(self):
        return fields.Date.context_today(self)

    @api.model
    def _bf_tz(self):
        name = self.env.context.get("tz") or self.env.user.tz or "UTC"
        try:
            return pytz.timezone(name)
        except pytz.UnknownTimeZoneError:
            return pytz.utc

    def _bf_date_terms(self, leaf, start, end):
        """Termes de filtre d'une mesure de base sur [start, end], jours civils inclus."""
        field = leaf.date_field_name
        if leaf.date_field_type == "datetime":
            # Les jours civils de la personne, convertis en UTC comme Odoo stocke les dates-heures.
            tz = self._bf_tz()
            lo = tz.localize(datetime.datetime.combine(start, datetime.time.min)).astimezone(pytz.utc)
            # Borne haute EXCLUSIVE au lendemain 0 h : une date-heure garde ses microsecondes, et
            # « <= 23:59:59 » perdrait la dernière seconde du jour.
            hi = tz.localize(datetime.datetime.combine(end + relativedelta(days=1), datetime.time.min)).astimezone(pytz.utc)
            return [(field, ">=", fields.Datetime.to_string(lo.replace(tzinfo=None))),
                    (field, "<", fields.Datetime.to_string(hi.replace(tzinfo=None)))]
        return [(field, ">=", fields.Date.to_string(start)), (field, "<=", fields.Date.to_string(end))]

    def _bf_leaf_domains(self, leaves, start, end, customers=None, customer=None, not_customers=None):
        """Le filtre de chaque mesure de base : période, clients du segment, compartiment client.

        Une mesure de base sans champ de date ne suit pas la période, et sans champ partenaire
        ne suit pas les clients : c'est la règle de bf_bi, la même dans le tableur.
        """
        domains = {}
        for leaf in leaves:
            domain = []
            if leaf.date_field_name and start:
                domain = expression.AND([domain, self._bf_date_terms(leaf, start, end)])
            partner = leaf.partner_field_name
            if partner:
                if customers:
                    domain = expression.AND([domain, [(partner, "in", list(customers))]])
                if customer is not None:
                    domain = expression.AND([domain, [(partner, "=", customer)]])
                if not_customers:
                    domain = expression.AND([domain, [(partner, "not in", list(not_customers))]])
            domains[leaf.code] = domain
        return domains

    def _bf_values(self, measures, start, end, **filters):
        budget = self.env.context.get("bf_bi_budget")
        if budget is not None:
            budget[0] += len(measures)
            if budget[0] > MAX_COMPUTATIONS:
                raise UserError(_("This visual asks for too many computations; show fewer measures or rows."))
        values, errors = [], {}
        for measure in measures:
            try:
                domains = self._bf_leaf_domains(measure._leaves(), start, end, **filters)
                values.append(measure._compute(domains))
            except (UserError, AccessError, ValidationError) as error:
                errors[measure.code] = str(error.args[0]) if error.args else _("This measure cannot be computed.")
                values.append(None)
        return values, errors

    @api.model
    def _bf_check_spec(self, spec, filters):
        if not isinstance(spec, dict) or not isinstance(filters, dict):
            raise UserError(_("Invalid visual."))
        codes = spec.get("measures")
        if (not isinstance(codes, list) or not 1 <= len(codes) <= MAX_MEASURES
                or not all(isinstance(c, str) and CODE_RE.match(c) for c in codes)):
            raise UserError(_("A visual needs one to %s named measures.", MAX_MEASURES))
        dimension = spec.get("dimension")
        if dimension not in (None,) + DIMENSIONS:
            raise UserError(_("Unknown dimension."))
        limit = spec.get("limit", 10)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
            raise UserError(_("The number of rows must be between 1 and %s.", MAX_LIMIT))
        period = filters.get("period") or {}
        if not isinstance(period, dict) or period.get("preset") not in PRESETS:
            raise UserError(_("Unknown period."))
        customers = filters.get("customers") or []
        if (not isinstance(customers, list) or len(customers) > MAX_CUSTOMERS
                or not all(isinstance(c, int) and not isinstance(c, bool) for c in customers)):
            raise UserError(_("Invalid customer filter."))
        drill = filters.get("drill")
        if drill is not None and not (isinstance(drill, dict) and drill.get("dim") == "customer"
                                      and isinstance(drill.get("id"), int) and not isinstance(drill.get("id"), bool)):
            raise UserError(_("Invalid drill-through."))
        selection = filters.get("selection")
        if selection is not None:
            if not isinstance(selection, dict) or selection.get("dim") not in DIMENSIONS:
                raise UserError(_("Invalid selection."))
            if selection["dim"] == "customer" and not (isinstance(selection.get("id"), int)
                                                       and not isinstance(selection.get("id"), bool)):
                raise UserError(_("Invalid selection."))
            if selection["dim"] in TIME_DIMENSIONS:
                start = selection.get("start")
                try:
                    if not (isinstance(start, str) and DATE_RE.match(start)):
                        raise ValueError(start)
                    fields.Date.from_string(start)
                except (TypeError, ValueError):
                    raise UserError(_("Invalid selection.")) from None
        return codes, dimension, limit, customers, selection

    def _bf_rich_refusal(self, measures, kind, dimension):
        """Pourquoi ce visuel ne peut pas montrer cette mesure, ou None.

        Une cascade additionne : un taux ou une moyenne y donnerait un total faux. Et un
        découpage par client (ou par période) suppose que TOUTES les sources de la mesure ont un
        champ client (ou date) : une source qui n'en a pas compterait en entier dans chaque
        client, et les écarts ne s'additionneraient plus.
        """
        measure = measures[0]
        if kind == "waterfall" and not is_additive(measure):
            return _("A waterfall adds values up: choose a measure that is a sum or a count, not a rate or an average.")
        leaves = measure._leaves()
        by_customer = kind == "heatmap" or dimension == "customer"
        by_time = kind == "heatmap" or dimension in TIME_DIMENSIONS
        if by_customer and not all(leaf.partner_field_name for leaf in leaves):
            return _("Part of this measure has no customer field: it cannot be split by customer here.")
        if by_time and not all(leaf.date_field_name for leaf in leaves):
            return _("Part of this measure has no date field: it cannot be split by period here.")
        return None

    @api.model
    def _bf_check_kind(self, spec, codes, dimension, limit):
        kind = spec.get("kind")
        if kind not in (None,) + RICH_KINDS:
            raise UserError(_("Unknown visual."))
        columns = spec.get("columns") or "month"
        if kind:
            if len(codes) != 1:
                raise UserError(_("This visual shows one measure."))
            if limit > MAX_RICH_LIMIT:
                raise UserError(_("The number of rows must be between 1 and %s.", MAX_RICH_LIMIT))
        if kind == "waterfall" and dimension is None:
            raise UserError(_("A waterfall needs an axis."))
        if kind == "heatmap" and (dimension != "customer" or columns not in HEAT_COLUMNS):
            raise UserError(_("A heat map shows customers by month or by quarter."))
        return kind, columns

    @api.model
    def bf_query(self, spec, filters):
        """Les valeurs d'un visuel, pour la personne qui regarde.

        spec    : {"measures": [codes], "dimension": None|"month"|"quarter"|"year"|"customer",
                   "limit": n, "trend": bool}
        filters : {"period": {"preset": ...}, "customers": [ids], "selection": None|
                   {"dim": "customer", "id": n}|{"dim": "month"|"quarter"|"year", "start": date}}
        """
        codes, dimension, limit, customers, selection = self._bf_check_spec(spec, filters)
        kind, columns = self._bf_check_kind(spec, codes, dimension, limit)
        if filters.get("drill"):
            # Page d'extraction : tout y est filtré sur ce client, quoi que dise le segment.
            customers = [filters["drill"]["id"]]
        codes = list(dict.fromkeys(codes))  # une mesure en double décalerait les colonnes
        self = self.with_context(bf_bi_budget=[0])
        Measure = self.env["bf.bi.measure"]
        measures = Measure.browse()
        meta, errors = [], {}
        for code in codes:
            try:
                measure = Measure._find(code)
            except UserError as error:
                errors[code] = str(error.args[0])
                continue
            measures |= measure
            meta.append({"code": measure.code, "name": measure.name, "unit": measure.unit,
                         "comparison": measure.comparison})
        today = self._bf_today()
        start, end = period_bounds(filters["period"]["preset"], today)
        customer = None
        if selection and selection["dim"] in TIME_DIMENSIONS:
            # Un compartiment de temps cliqué remplace la période (et son « avant »).
            start, end = bucket_bounds(selection["dim"], fields.Date.from_string(selection["start"]))
        elif selection and selection["dim"] == "customer":
            customer = selection["id"]
        p_start, p_end = previous_period(start, end, today)
        if start <= today <= end:
            # Période en cours : jusqu'à aujourd'hui, comme la période d'avant. Sinon une ligne
            # datée dans le futur (échéance, travail planifié) fausserait la comparaison.
            end = today
        base = {"customers": customers, "customer": customer}
        total, err = self._bf_values(measures, start, end, **base)
        errors.update(err)
        previous, _err = self._bf_values(measures, p_start, p_end, **base)
        result = {
            "period": {"start": fields.Date.to_string(start), "end": fields.Date.to_string(end),
                       "previous_start": fields.Date.to_string(p_start),
                       "previous_end": fields.Date.to_string(p_end)},
            "measures": meta, "total": total, "previous": previous, "rows": [], "trend": None,
            "errors": errors,
        }
        refusal = self._bf_rich_refusal(measures[:1], kind, dimension) if measures and kind else None
        if refusal:
            # Le nom de la mesure reste (titre du visuel) ; le visuel dit pourquoi il est vide.
            result["error"] = errors[measures[0].code] = refusal
        elif measures and kind == "waterfall" and dimension == "customer":
            result["rows"] = self._bf_waterfall_rows(measures, limit, start, end, p_start, p_end, base)
        elif measures and kind == "heatmap":
            result["rows"], result["columns"] = self._bf_heat_rows(measures, limit, columns, start, end, base)
        elif measures and dimension in TIME_DIMENSIONS:
            result["rows"] = self._bf_time_rows(measures, dimension, start, end, base)
        elif measures and dimension == "customer":
            result["rows"] = self._bf_customer_rows(measures, limit, start, end, base)
        if measures and spec.get("trend"):
            result["trend"] = self._bf_trend(measures[:1], end, base)
        return result

    def _bf_time_rows(self, measures, dimension, start, end, base):
        rows, day = [], start
        while day <= end:
            b_start, b_end = bucket_bounds(dimension, day)
            values, _errors = self._bf_values(measures, max(b_start, start), min(b_end, end), **base)
            rows.append({"key": fields.Date.to_string(b_start), "start": fields.Date.to_string(b_start),
                         "end": fields.Date.to_string(b_end), "label": self._bf_bucket_label(dimension, b_start),
                         "values": values})
            day = b_end + relativedelta(days=1)
        return rows

    def _bf_bucket_label(self, dimension, day):
        if dimension == "year":
            return str(day.year)
        if dimension == "quarter":
            return _("Q%(q)s %(y)s", q=(day.month - 1) // 3 + 1, y=day.year)
        return fields.Date.to_string(day)[:7]  # le navigateur l'écrit dans la langue de la personne

    def _bf_customer_rows(self, measures, limit, start, end, base):
        """Les clients qui ont des données, les plus forts d'abord, et « Others » pour le reste.

        Les candidats sont les MAX_MEMBERS plus forts selon la première mesure de base qui a un
        champ client (agrégat trié par la base, pas par ordre alphabétique), puis classés sur
        la valeur exacte de la première mesure. « Others » regroupe le reste, lignes sans
        client comprises : les lignes s'additionnent toujours au total.
        """
        members = self._bf_customer_members(measures, start, end, base)
        scored = []
        for partner_id, label in list(members.items())[:MAX_MEMBERS]:
            values, _errors = self._bf_values(measures, start, end, customers=base["customers"], customer=partner_id)
            scored.append({"key": partner_id, "label": label, "values": values})
        scored.sort(key=lambda row: (row["values"][0] is None, -(row["values"][0] or 0)))
        rows = scored[:limit]
        values, _errors = self._bf_values(measures, start, end, customers=base["customers"], customer=base["customer"],
                                          not_customers=[r["key"] for r in rows] or [0])
        if len(scored) > limit or any(v for v in values):
            rows.append({"key": "others", "label": _("Others"), "values": values})
        return rows

    def _bf_customer_members(self, measures, start, end, base):
        """{id: nom} des clients qui ont des données sur la période, les plus forts d'abord."""
        members = {}
        for leaf in self._bf_all_leaves(measures):
            partner = leaf.partner_field_name
            if not partner or len(members) >= MAX_MEMBERS:
                continue
            Model = self.env[leaf.model_name]  # PAS de sudo : la personne ne voit que ses lignes
            domain = expression.AND([leaf._eval_domain(),
                                     self._bf_leaf_domains(leaf, start, end, customers=base["customers"],
                                                           customer=base["customer"])[leaf.code],
                                     [(partner, "!=", False)]])
            spec = "__count" if not leaf.field_name or leaf.aggregator == "count" else "%s:%s" % (leaf.field_name, leaf.aggregator)
            try:
                groups = Model._read_group(domain, [partner], [spec], limit=MAX_MEMBERS, order="%s DESC" % spec)
            except (AccessError, UserError, ValueError):
                continue  # champ client illisible pour la personne : ce découpage-là est sauté
            for record, _value in groups:
                if record and record.id not in members:
                    try:
                        members[record.id] = record.display_name
                    except AccessError:
                        members[record.id] = _("Customer #%s", record.id)
        return members

    def _bf_waterfall_rows(self, measures, limit, start, end, p_start, p_end, base):
        """Ce qui explique l'écart avec la période d'avant, client par client.

        Les candidats sont les clients forts de l'une OU l'autre période (un client perdu compte
        autant qu'un client gagné), classés sur l'écart absolu. « Others » porte le reste de
        l'écart, lignes sans client comprises : période d'avant + écarts = période courante.
        """
        members = self._bf_customer_members(measures, start, end, base)
        for partner_id, label in self._bf_customer_members(measures, p_start, p_end, base).items():
            members.setdefault(partner_id, label)
        scored = []
        # Toute l'union est classée (au plus deux fois MAX_MEMBERS) : couper à MAX_MEMBERS
        # écarterait d'abord les clients perdus, ajoutés après ceux de la période courante.
        for partner_id, label in members.items():
            current, _errors = self._bf_values(measures, start, end, customers=base["customers"], customer=partner_id)
            before, _errors = self._bf_values(measures, p_start, p_end, customers=base["customers"], customer=partner_id)
            if current[0] is None or before[0] is None:
                continue
            scored.append({"key": partner_id, "label": label, "values": current, "previous": before,
                           "delta": current[0] - before[0]})
        scored.sort(key=lambda row: -abs(row["delta"]))
        rows = [row for row in scored[:limit] if row["delta"]]
        keys = [row["key"] for row in rows] or [0]
        filters = {"customers": base["customers"], "customer": base["customer"], "not_customers": keys}
        current, _errors = self._bf_values(measures, start, end, **filters)
        before, _errors = self._bf_values(measures, p_start, p_end, **filters)
        if current[0] is not None and before[0] is not None and current[0] - before[0]:
            rows.append({"key": "others", "label": _("Others"), "values": current, "previous": before,
                         "delta": current[0] - before[0]})
        return rows

    def _bf_heat_rows(self, measures, limit, columns, start, end, base):
        """Les clients les plus forts sur la période, une cellule par mois ou par trimestre."""
        rows = [row for row in self._bf_customer_rows(measures, limit, start, end, base) if row["key"] != "others"]
        if bucket_bounds(columns, start)[0] < start:
            # Une période plus courte que la colonne (un mois, en trimestres) : la colonne ne
            # serait qu'un morceau de trimestre, et un clic filtrerait sur le trimestre entier.
            columns = "month"
        cols, day = [], start
        while day <= end:
            b_start, b_end = bucket_bounds(columns, day)
            cols.append({"key": fields.Date.to_string(b_start), "start": fields.Date.to_string(max(b_start, start)),
                         "end": fields.Date.to_string(min(b_end, end)), "label": self._bf_bucket_label(columns, b_start),
                         "dim": columns})
            day = b_end + relativedelta(days=1)
        for row in rows:
            row["cells"] = []
            for col in cols:
                values, _errors = self._bf_values(measures, fields.Date.from_string(col["start"]),
                                                  fields.Date.from_string(col["end"]),
                                                  customers=base["customers"], customer=row["key"])
                row["cells"].append(values[0])
        return rows, cols

    def _bf_all_leaves(self, measures):
        leaves = self.env["bf.bi.measure"].browse()
        for measure in measures:
            leaves |= measure._leaves()
        return leaves

    def _bf_trend(self, measures, end, base):
        last = _month_start(end)
        trend = []
        for k in range(TREND_MONTHS - 1, -1, -1):
            m_start = last - relativedelta(months=k)
            m_end = m_start + relativedelta(months=1, days=-1)
            values, _errors = self._bf_values(measures, m_start, m_end, **base)
            trend.append({"key": fields.Date.to_string(m_start), "value": values[0]})
        return trend


class BfBiReportPage(models.Model):
    _name = "bf.bi.report.page"
    _description = "BI report page"
    _order = "sequence, id"

    report_id = fields.Many2one("bf.bi.report", required=True, ondelete="cascade", index=True)
    name = fields.Char(string="Name", required=True, translate=True)
    sequence = fields.Integer(default=10)
    layout = fields.Text(
        string="Visuals", default="[]",
        help="The page's visuals, as the designer saves them: type, named measures, dimension, "
             "and position on a 12-column grid.")
    company_id = fields.Many2one(related="report_id.company_id", store=True)
    drill_dimension = fields.Selection(
        [("customer", "Customer")], string="Drill-through page",
        help="A drill-through page opens filtered on one customer, from a right-click on a customer "
             "anywhere in the report, or from the Detail button of a customer table.")
    filter_preset = fields.Selection(
        [("month", "Month"), ("quarter", "Quarter"), ("year", "This year"), ("last_year", "Last year")],
        string="Fixed period", help="When set, the page always shows this period, whatever the period slicer says.")
    filter_customers = fields.Text(
        string="Fixed customers", default="[]",
        help="When set, the page always shows these customers, whatever the customer slicer says.")

    def _bf_settings(self):
        self.ensure_one()
        try:
            customers = json.loads(self.filter_customers or "[]")
        except ValueError:
            customers = []
        if not isinstance(customers, list) or not all(isinstance(c, int) and not isinstance(c, bool) for c in customers):
            customers = []
        return {"drill": self.drill_dimension or None, "preset": self.filter_preset or None,
                "customers": customers[:MAX_PAGE_CUSTOMERS]}

    @staticmethod
    def _bf_settings_errors(page):
        if page.get("drill") not in (None, False, "customer"):
            return "unknown drill-through"
        if page.get("preset") not in (None, False) + PAGE_PRESETS:
            return "unknown fixed period"
        customers = page.get("customers") or []
        if (not isinstance(customers, list) or len(customers) > MAX_PAGE_CUSTOMERS
                or not all(isinstance(c, int) and not isinstance(c, bool) and 0 < c < 2 ** 31 for c in customers)
                or len(set(customers)) != len(customers)):
            return "bad fixed customers"  # 2**31 : un identifiant PostgreSQL, que le navigateur rend intact
        if page.get("drill") and customers:
            return "a drill-through page follows the customer it is opened on"
        return None

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._bf_check_no_company(vals)
            self._bf_check_report_target(vals.get("report_id"))
        return super().create(vals_list)

    def write(self, vals):
        self._bf_check_no_company(vals)
        if "report_id" in vals:
            self._bf_check_report_target(vals["report_id"])
        return super().write(vals)

    def _bf_check_no_company(self, vals):
        """La société d'une page est celle de son rapport : un champ lié stocké s'écrit quand
        même par RPC, et une page « déménagée » ainsi sortirait de la vue de ses lecteurs."""
        if "company_id" in vals and not self.env.su:
            raise AccessError(_("A page follows the company of its report."))

    def _bf_check_report_target(self, report_id):
        """Une page ne se rattache qu'à un rapport que la personne peut modifier : sinon un
        concepteur de A pourrait glisser une page devant les lecteurs de B (l'écriture ne
        contrôle que l'enregistrement de départ, pas la valeur posée)."""
        if self.env.su or not report_id:
            return
        self.env["bf.bi.report"].browse(report_id).check_access("write")

    def _bf_visuals(self):
        self.ensure_one()
        try:
            visuals = json.loads(self.layout or "[]")
        except ValueError:
            return []
        return visuals if self._bf_layout_errors(visuals) is None else []

    @staticmethod
    def _bf_layout_errors(visuals):
        """None si la mise en page est bonne, sinon ce qui ne va pas."""
        if not isinstance(visuals, list) or len(visuals) > MAX_VISUALS:
            return "not a list of at most %s visuals" % MAX_VISUALS
        try:
            if len(json.dumps(visuals, allow_nan=False)) > MAX_LAYOUT_BYTES:
                return "the page is too large"
        except (TypeError, ValueError):
            return "a value is not valid JSON"  # NaN ou Infinity : illisibles pour le navigateur
        seen = set()
        for v in visuals:
            if not isinstance(v, dict):
                return "a visual is not an object"
            if set(v) - VISUAL_KEYS:
                return "unknown visual setting"
            if not isinstance(v.get("id"), str) or not ID_RE.match(v["id"]) or v["id"] in seen:
                return "bad or repeated visual id"
            seen.add(v["id"])
            if v.get("type") not in VISUAL_TYPES:
                return "unknown visual type"
            codes = v.get("measures")
            # Un visuel peut rester à compléter (sans mesure, sans dimension) : le concepteur
            # l'enregistre en cours de route, le lecteur l'affiche « à compléter ».
            if (not isinstance(codes, list) or not 0 <= len(codes) <= MAX_MEASURES
                    or not all(isinstance(c, str) and CODE_RE.match(c) for c in codes) or len(set(codes)) != len(codes)):
                return "bad or repeated measures"
            if v.get("dimension") not in (None,) + DIMENSIONS:
                return "unknown dimension"
            if v["type"] == "line" and v.get("dimension") == "customer":
                return "a line follows time"
            if v["type"] in SINGLE_MEASURE_TYPES and len(codes) > 1:
                return "this visual shows one measure"
            if v["type"] == "gauge" and v.get("dimension"):
                return "a gauge has no axis"
            if v["type"] == "heatmap" and v.get("dimension") not in (None, "customer"):
                return "a heat map shows customers"
            if "columns" in v and v["columns"] not in HEAT_COLUMNS:
                return "bad columns"
            if "max" in v and not (isinstance(v["max"], (int, float)) and not isinstance(v["max"], bool) and v["max"] > 0):
                return "bad maximum"
            if v["type"] in RICH_KINDS and isinstance(v.get("limit"), int) and v["limit"] > MAX_RICH_LIMIT:
                return "bad limit"
            if "title" in v and not (isinstance(v["title"], str) and len(v["title"]) <= 120):
                return "bad title"
            # NaN et Infinity sont déjà refusés par la sérialisation stricte plus haut.
            if "target" in v and not (isinstance(v["target"], (int, float)) and not isinstance(v["target"], bool)):
                return "bad target"
            if "limit" in v and not (isinstance(v["limit"], int) and not isinstance(v["limit"], bool)
                                     and 1 <= v["limit"] <= MAX_LIMIT):
                return "bad limit"
            for key, lo, hi in (("x", 0, GRID_COLUMNS - 1), ("y", 0, GRID_ROWS), ("w", 1, GRID_COLUMNS), ("h", 1, 12)):
                if not isinstance(v.get(key), int) or isinstance(v.get(key), bool) or not lo <= v[key] <= hi:
                    return "bad position"
            if v["x"] + v["w"] > GRID_COLUMNS:
                return "a visual goes past the grid"
        return None

    @api.constrains("filter_customers", "drill_dimension", "filter_preset")
    def _check_settings(self):
        for page in self:
            try:
                customers = json.loads(page.filter_customers or "[]")
            except ValueError:
                raise ValidationError(_("Page “%s”: the fixed customers are not valid JSON.", page.name)) from None
            error = self._bf_settings_errors({"drill": page.drill_dimension, "preset": page.filter_preset,
                                              "customers": customers})
            if error:
                raise ValidationError(_("Page “%(page)s”: %(error)s.", page=page.name, error=error))

    @api.constrains("layout")
    def _check_layout(self):
        for page in self:
            try:
                visuals = json.loads(page.layout or "[]")
            except ValueError:
                raise ValidationError(_("Page “%s”: the visuals are not valid JSON.", page.name)) from None
            error = self._bf_layout_errors(visuals)
            if error:
                raise ValidationError(_("Page “%(page)s”: %(error)s.", page=page.name, error=error))


class BfBiReportVersion(models.Model):
    _name = "bf.bi.report.version"
    _description = "BI report version"
    _order = "revision desc"

    report_id = fields.Many2one("bf.bi.report", required=True, ondelete="cascade", index=True)
    revision = fields.Integer(string="Revision", readonly=True)
    content = fields.Text(string="Content", readonly=True)
    company_id = fields.Many2one(related="report_id.company_id", store=True)

    def bf_restore(self):
        """Remet cette version en service, comme une nouvelle révision."""
        self.ensure_one()
        report = self.report_id
        report._bf_check_editable()
        content = json.loads(self.content or "{}")
        pages = [{"id": None, "name": p.get("name") or _("Page"), "visuals": p.get("visuals") or [],
                  "drill": p.get("drill"), "preset": p.get("preset"), "customers": p.get("customers") or []}
                 for p in content.get("pages") or []]
        theme = content.get("theme") if content.get("theme") in THEMES else None
        result = report.bf_save(pages, report.bf_revision, theme=theme)
        if result["status"] != "saved":
            raise UserError(_("Someone saved this report meanwhile. Reload it and try again."))
        return result

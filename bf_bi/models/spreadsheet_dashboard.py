import json
import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.translate import LazyTranslate, code_translations

_lt = LazyTranslate(__name__)

# Textes que bf_bi pose lui-même dans un tableau de bord (filtres automatiques, légende des
# tuiles) : stockés dans la langue de la personne qui l'a bâti, ils sont remis dans la langue
# de la personne qui regarde, pour TOUT tableau de bord (pas seulement les modèles livrés).
STANDARD_FILTERS = {"bf_bi_periode": _lt("Period"), "bf_bi_client": _lt("Customer")}
# Courtes : une tuile n'en montre qu'une trentaine de caractères, variation comprise.
# [0] période précédente entière (tableau croisé), [1] même durée (mesure nommée).
STANDARD_BASELINES = (_lt("vs previous period"), _lt("vs matching period"))
# Formulations des versions précédentes, encore enregistrées dans des tableaux de bord :
# reconnues à la lecture et remplacées par la formulation courante (même indice).
LEGACY_BASELINES = ((_lt("vs whole previous period"), 0), (_lt("vs previous period, same length"), 1))
# Une cellule qui appelle une mesure nommée sur la période précédente : BF.MEASURE("code", -1).
MEASURE_PREVIOUS = re.compile(r"BF\.MEASURE\(\s*\"[^\"]*\"\s*,\s*-1\s*\)", re.IGNORECASE)

# Versions gardées par tableau de bord ; au-delà, les plus anciennes sont effacées.
VERSIONS_GARDEES = 30


class SpreadsheetDashboard(models.Model):
    _inherit = "spreadsheet.dashboard"

    bf_revision = fields.Integer(
        string="Revision", default=0, copy=False, readonly=True,
        help="Goes up with every save. A save made on an outdated revision is refused: it would overwrite someone else's work.")
    bf_is_shipped = fields.Boolean(
        string="Shipped by a module", compute="_compute_bf_is_shipped",
        help="Dashboard provided by a module: an update rewrites it. Duplicate it to edit it.")
    bf_version_ids = fields.One2many(
        "bf.bi.dashboard.version", "dashboard_id", string="Versions")

    def _compute_bf_is_shipped(self):
        # Les ir.model.data se lisent en sudo : le concepteur n'a pas accès au modèle.
        livres = set(self.env["ir.model.data"].sudo().search([
            ("model", "=", self._name),
            ("res_id", "in", self.ids),
            ("module", "not in", ("__export__", "__import__", "__custom__")),
        ]).mapped("res_id"))
        for dashboard in self:
            dashboard.bf_is_shipped = dashboard.id in livres

    # ------------------------------------------------------------------
    # Modèles livrés : textes dans la langue de la personne qui regarde
    # ------------------------------------------------------------------

    def _bf_template_module(self):
        """Module qui livre ce tableau de bord, s'il vient d'un pont bf_bi_*."""
        self.ensure_one()
        data = self.env["ir.model.data"].sudo().search(
            [("model", "=", self._name), ("res_id", "=", self.id), ("module", "=like", "bf\\_bi\\_%")], limit=1)
        return data.module or False

    def _bf_translate_template(self, data, module):
        """Traduit les textes d'un modèle livré (libellés de filtres, titres, cellules de texte,
        noms des tableaux croisés et listes) par les catalogues du pont et de bf_bi.

        Les textes du JSON sont en anglais (la source) ; leurs traductions vivent dans le
        fichier terms.py du pont, que l'export des traductions ramasse.
        """
        lang = self.env.lang or "en_US"
        if lang.startswith("en"):
            return data
        table = {}
        for mod in ("bf_bi", module):
            table.update(code_translations.get_python_translations(mod, lang))
        tr = lambda text: table.get(text, text) if isinstance(text, str) else text
        for flt in data.get("globalFilters", []):
            flt["label"] = tr(flt.get("label"))
        for sheet in data.get("sheets", []):
            for cell in sheet.get("cells", {}).values():
                content = cell.get("content")
                if isinstance(content, str) and content and not content.startswith("="):
                    cell["content"] = tr(content)
            for figure in sheet.get("figures", []):
                fig = figure.get("data", {})
                if isinstance(fig.get("title"), dict):
                    fig["title"]["text"] = tr(fig["title"].get("text"))
                if "baselineDescription" in fig:  # ancien nom, que o-spreadsheet 18 ignore
                    fig.setdefault("baselineDescr", fig.pop("baselineDescription"))
                if "baselineDescr" in fig:
                    fig["baselineDescr"] = tr(fig["baselineDescr"])
        for collection in ("pivots", "lists"):
            for definition in data.get(collection, {}).values():
                definition["name"] = tr(definition.get("name"))
        return data

    def _bf_variants(self, terms):
        """{texte dans n'importe quelle langue installée: texte source} pour ces termes."""
        variants = {}
        langs = [code for code, _label in self.env["res.lang"].get_installed()]
        for term in terms:
            source = term._source
            variants[source] = source
            for lang in langs:
                variants[code_translations.get_python_translations("bf_bi", lang).get(source, source)] = source
        return variants

    def _bf_baseline_variants(self):
        """{légende standard, courante ou ancienne, dans toute langue installée: indice}."""
        variants = {}
        pairs = [(t, i) for i, t in enumerate(STANDARD_BASELINES)] + list(LEGACY_BASELINES)
        for term, index in pairs:
            for text in self._bf_variants([term]):
                variants[text] = index
        return variants

    @staticmethod
    def _bf_cell_content(cells_by_sheet, ref, own_sheet):
        """Contenu de la cellule « 'Feuille'!B1 », ou « B1 » sur la feuille de la figure."""
        if not isinstance(ref, str):
            return ""
        sheet, _sep, xc = ref.rpartition("!")
        cells = cells_by_sheet.get(sheet.strip("'") if sheet else own_sheet)
        cell = cells.get(xc) if isinstance(cells, dict) else None
        content = cell.get("content") if isinstance(cell, dict) else None
        return content if isinstance(content, str) else ""

    def _bf_translate_standard(self, data):
        """Remet dans la langue de la personne qui regarde les textes que bf_bi pose lui-même.

        Ne lève JAMAIS : ce passage sert la lecture et l'éditeur de chaque tableau de bord,
        une forme inattendue (enregistrée par une autre version, ou fabriquée) se laisse
        telle quelle plutôt que de rendre le tableau de bord illisible pour tout le monde.
        Seul un texte encore STANDARD est remplacé : un filtre renommé ou une légende saisie
        à la main restent ce qu'on en a fait.
        """
        if not isinstance(data, dict):
            return data
        filters = data.get("globalFilters")
        if isinstance(filters, list):
            filter_variants = self._bf_variants(STANDARD_FILTERS.values())
            for flt in filters:
                if not isinstance(flt, dict) or flt.get("id") not in STANDARD_FILTERS:
                    continue
                term = STANDARD_FILTERS[flt["id"]]
                if filter_variants.get(flt.get("label")) == term._source:
                    flt["label"] = str(term)
        sheets = data.get("sheets")
        if not isinstance(sheets, list):
            return data
        whole, same = 0, 1
        baseline_variants = None
        cells_by_sheet = {sh.get("name"): sh.get("cells") for sh in sheets if isinstance(sh, dict)}
        for sheet in sheets:
            if not isinstance(sheet, dict) or not isinstance(sheet.get("figures"), list):
                continue
            for figure in sheet["figures"]:
                fig = figure.get("data") if isinstance(figure, dict) else None
                if not isinstance(fig, dict):
                    continue
                if "baselineDescription" in fig:  # ancien nom, que o-spreadsheet 18 ignore
                    fig.setdefault("baselineDescr", fig.pop("baselineDescription"))
                legend = fig.get("baselineDescr")
                if not legend or not isinstance(legend, str):
                    continue
                baseline_variants = baseline_variants or self._bf_baseline_variants()
                source = baseline_variants.get(legend)
                if source is None:
                    continue  # légende saisie à la main : intacte
                # Depuis 18.0.1.6.0, BF.MEASURE(…, -1) compare à même durée : une tuile
                # enregistrée avant, qui dit « période entière », dirait faux.
                formula = self._bf_cell_content(cells_by_sheet, fig.get("baseline"), sheet.get("name"))
                if source == whole and MEASURE_PREVIOUS.search(formula):
                    source = same
                fig["baselineDescr"] = str(STANDARD_BASELINES[source])
        return data

    @staticmethod
    def _bf_well_formed(data):
        """La forme qu'attendent le lecteur d'Odoo et la traduction des textes. Un contenu
        fabriqué autrement (une feuille qui n'est pas un objet, une figure sans données)
        rendrait le tableau de bord illisible pour TOUS ses lecteurs, pas seulement pour
        qui l'a enregistré."""
        if not isinstance(data, dict) or not isinstance(data.get("sheets"), list) or not data["sheets"]:
            return False
        for key in ("globalFilters",):
            if key in data and not (isinstance(data[key], list) and all(isinstance(x, dict) for x in data[key])):
                return False
        for key in ("pivots", "lists", "chartOdooMenusReferences"):
            if key in data and not isinstance(data[key], dict):
                return False
        for sheet in data["sheets"]:
            if not isinstance(sheet, dict) or not isinstance(sheet.get("cells", {}), dict):
                return False
            if not all(isinstance(c, dict) for c in sheet.get("cells", {}).values()):
                return False
            figures = sheet.get("figures", [])
            if not isinstance(figures, list):
                return False
            for figure in figures:
                if not isinstance(figure, dict) or not isinstance(figure.get("data", {}), dict):
                    return False
                legend = figure.get("data", {}).get("baselineDescr")
                if legend is not None and not isinstance(legend, str):
                    return False
        return True

    def get_readonly_dashboard(self):
        result = super().get_readonly_dashboard()
        snapshot = result.get("snapshot")
        if isinstance(snapshot, dict):
            module = self._bf_template_module()
            if module:
                snapshot = self._bf_translate_template(snapshot, module)
            result["snapshot"] = self._bf_translate_standard(snapshot)
        return result

    # ------------------------------------------------------------------
    # Contrôles
    # ------------------------------------------------------------------

    def _bf_check_designer(self):
        if not self.env.user.has_group("bf_bi.group_bi_designer"):
            raise AccessError(_("Only a BI designer can edit a dashboard."))

    def _bf_check_editable(self):
        self.ensure_one()
        self._bf_check_designer()
        self.check_access("write")
        if self.bf_is_shipped:
            raise UserError(_(
                "“%s” is shipped by a module: an update would rewrite it. Duplicate it to edit it.", self.name))

    # ------------------------------------------------------------------
    # Ouverture de l'éditeur
    # ------------------------------------------------------------------

    def _bf_editor_action(self, insert=None):
        self.ensure_one()
        params = {"dashboard_id": self.id}
        if insert:
            params["insert"] = insert
        return {
            "type": "ir.actions.client",
            "tag": "bf_bi.dashboard_editor",
            "name": self.name,
            "params": params,
        }

    def action_edit_dashboard(self):
        """Appelée par le bouton « Modifier » du lecteur de tableaux de bord d'Odoo.

        Un tableau de bord livré est d'abord dupliqué : c'est la copie qu'on modifie.
        """
        self.ensure_one()
        self._bf_check_designer()
        dashboard = self
        if self.bf_is_shipped:
            # La copie est un brouillon : visible des seuls concepteurs, comme un nouveau
            # tableau de bord (sinon ses modifications s'afficheraient en direct aux groupes
            # de l'original).
            dashboard = self.copy({"group_ids": [(6, 0, [self.env.ref("bf_bi.group_bi_designer").id])]})
            module = self._bf_template_module()
            if module:
                # La copie appartient au concepteur : elle part dans sa langue.
                data = self._bf_translate_template(json.loads(self.spreadsheet_data), module)
                dashboard.spreadsheet_data = json.dumps(data)
        dashboard._bf_check_editable()
        return dashboard._bf_editor_action()

    @api.model
    def bf_action_new_dashboard(self, name=None, dashboard_group_id=None):
        """Crée un tableau de bord vide, visible des seuls concepteurs, et renvoie son id."""
        self._bf_check_designer()
        if not dashboard_group_id:
            section = self.env["spreadsheet.dashboard.group"].search([], limit=1)
            if not section:
                raise UserError(_("No dashboard section exists yet."))
            dashboard_group_id = section.id
        dashboard = self.create({
            "name": name or _("New dashboard"),
            "dashboard_group_id": dashboard_group_id,
            "group_ids": [(6, 0, [self.env.ref("bf_bi.group_bi_designer").id])],
        })
        return dashboard.id

    @api.model
    def bf_list_editable(self):
        """Tableaux de bord que la personne peut modifier, pour l'assistant d'insertion."""
        self._bf_check_designer()
        dashboards = self.search([])
        dashboards = dashboards.filtered(lambda d: not d.bf_is_shipped)
        return [
            {"id": d.id, "name": d.name, "section": d.dashboard_group_id.name}
            for d in dashboards
            if d.has_access("write")
        ]

    def bf_get_edit_data(self):
        self._bf_check_editable()
        return {
            "name": self.name,
            "data": self._bf_translate_standard(json.loads(self.spreadsheet_data or "{}") or self._empty_spreadsheet_data()),
            "revision": self.bf_revision,
            "default_currency": self.env["res.currency"].get_company_currency_for_spreadsheet(),
            "user_locale": self.env["res.lang"]._get_user_spreadsheet_locale(),
        }

    # ------------------------------------------------------------------
    # Enregistrement
    # ------------------------------------------------------------------

    def bf_save(self, data, revision, name=None):
        """Enregistre l'état complet du tableur si `revision` est la révision courante.

        Renvoie {"status": "saved", "revision": n} ou, si quelqu'un a enregistré
        entre-temps, {"status": "conflict", "revision": n, "author": nom} sans rien écrire.
        """
        self._bf_check_editable()
        if not self._bf_well_formed(data):
            raise UserError(_("Invalid dashboard content."))
        # Pas de SQL brut : Odoo tourne en isolation « repeatable read ». Deux
        # enregistrements simultanés sur la même révision écrivent la même ligne ; le second
        # échoue en sérialisation, Odoo le rejoue, et le rejeu voit la révision avancée :
        # il répond « conflict ». Éprouvé avec douze enregistrements simultanés.
        # Relire en base : une écriture encore en cache (même transaction) compte aussi.
        self.flush_recordset(["bf_revision"])
        self.invalidate_recordset(["bf_revision"])
        courante = self.bf_revision or 0
        if courante != revision:
            self.invalidate_recordset(["bf_revision"])
            return {
                "status": "conflict",
                "revision": courante,
                "author": self.write_uid.name,
            }
        contenu = json.dumps(data)
        self.write({"spreadsheet_data": contenu, "bf_revision": courante + 1})
        if name is not None and not isinstance(name, str):
            raise UserError(_("Invalid dashboard name."))
        if name and name.strip() and name.strip() != self.name:
            # Le nom est traduisible : écrit dans la seule langue de la personne, un collègue
            # dans une autre langue verrait l'ancien nom. Un nom saisi vaut pour toutes.
            nom = name.strip()
            self.write({"name": nom})
            langues = [code for code, _label in self.env["res.lang"].get_installed()]
            self.update_field_translations("name", {code: nom for code in langues})
        self.env["bf.bi.dashboard.version"].sudo().create({
            "dashboard_id": self.id,
            "revision": courante + 1,
            "spreadsheet_data": contenu,
        })
        self._bf_prune_versions()
        return {"status": "saved", "revision": courante + 1}

    def _bf_prune_versions(self):
        Version = self.env["bf.bi.dashboard.version"].sudo()
        for dashboard in self:
            anciennes = Version.search(
                [("dashboard_id", "=", dashboard.id)], order="revision desc", offset=VERSIONS_GARDEES)
            anciennes.unlink()

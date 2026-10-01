import json

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestTemplate(TransactionCase):
    """Le modèle livré tient debout : mesures calculables, formules qui renvoient à des
    mesures qui existent, champs des tableaux croisés, listes et graphiques qui existent."""

    def test_measures_compute(self):
        # En anglais : les messages attendus ne doivent pas dépendre de la langue de la base.
        Measure = self.env["bf.bi.measure"].with_context(lang="en_US")
        for code in ['hour_bank_adjustments']:
            try:
                value = Measure.bf_value(code, {})
            except Exception as exc:  # sur une base vide : division par zéro ou pas de valeur
                self.assertTrue("division" in str(exc) or "no value" in str(exc), (code, str(exc)))
                continue
            # Moyenne sur aucune ligne : pas de valeur (False), pas 0.
            self.assertTrue(value is False or isinstance(value, float), code)

    def test_dashboard_references_exist(self):
        dashboard = self.env.ref("bf_bi_hour_bank.dashboard_hour_bank")
        self.assertTrue(dashboard.bf_is_shipped)
        data = json.loads(dashboard.spreadsheet_data)
        codes = set(self.env["bf.bi.measure"].search([]).mapped("code"))
        for sheet in data["sheets"]:
            for cell in sheet["cells"].values():
                content = cell.get("content", "")
                if content.startswith("=BF.MEASURE("):
                    self.assertIn(content.split('"')[1], codes, content)
        def check(model, field):
            self.assertIn(field.split(":")[0], self.env[model]._fields, "%s.%s" % (model, field))
        for pivot in data["pivots"].values():
            for dim in pivot["rows"] + pivot["columns"]:
                check(pivot["model"], dim["fieldName"])
            for measure in pivot["measures"]:
                if measure["fieldName"] != "__count":
                    check(pivot["model"], measure["fieldName"])
            for matching in pivot["fieldMatching"].values():
                check(pivot["model"], matching["chain"])
        for listing in data["lists"].values():
            for field in listing["columns"]:
                check(listing["model"], field)
        for sheet in data["sheets"]:
            for figure in sheet["figures"]:
                fig = figure["data"]
                if fig["type"].startswith("odoo_"):
                    model = fig["metaData"]["resModel"]
                    for group_by in fig["metaData"]["groupBy"]:
                        check(model, group_by)
                    if fig["metaData"]["measure"] != "__count":
                        check(model, fig["metaData"]["measure"])

    def test_dashboard_readers_can_compute_every_measure(self):
        # Une personne qui n'a QUE les groupes du tableau de bord (et employé) voit chaque
        # tuile calculée : pas de #ERROR pour le public auquel le modèle est destiné.
        dashboard = self.env.ref("bf_bi_hour_bank.dashboard_hour_bank")
        lecteur = self.env["res.users"].create({
            "name": "Lecteur du modèle", "login": "lecteur-bf_bi_hour_bank@essai.invalid",
            "groups_id": [(6, 0, (dashboard.group_ids | self.env.ref("base.group_user")).ids)]})
        data = json.loads(dashboard.spreadsheet_data)
        codes = {c["content"].split('"')[1] for s in data["sheets"] for c in s["cells"].values()
                 if "BF.MEASURE(" in c.get("content", "")}
        Measure = self.env["bf.bi.measure"].with_user(lecteur).with_context(lang="en_US")
        for code in codes:
            try:
                Measure.bf_value(code, {})
            except Exception as exc:
                self.assertNotIsInstance(exc, AccessError, "%s refusée au lecteur du modèle" % code)
                self.assertTrue("division" in str(exc) or "no value" in str(exc), (code, str(exc)))

    def test_tiles_follow_the_measure_comparison(self):
        dashboard = self.env.ref("bf_bi_hour_bank.dashboard_hour_bank")
        data = json.loads(dashboard.spreadsheet_data)
        calc = {xc: c["content"] for s in data["sheets"] if s["name"] == "bf_bi calculs" for xc, c in s["cells"].items()}
        Measure = self.env["bf.bi.measure"]
        for fig in data["sheets"][0]["figures"]:
            d = fig["data"]
            if d["type"] != "scorecard":
                continue
            code = calc[d["keyValue"].split("!")[1]].split('"')[1]
            self.assertEqual(d["baselineMode"], Measure.search([("code", "=", code)]).comparison, code)

    def test_shipped_measures_serve_every_company(self):
        codes = ['hour_bank_adjustments']
        measures = self.env["bf.bi.measure"].search([("code", "in", codes)])
        self.assertEqual(len(measures), len(codes))
        self.assertFalse(measures.company_id)

    def test_dashboard_is_translated_for_french_readers(self):
        dashboard = self.env.ref("bf_bi_hour_bank.dashboard_hour_bank")
        if not self.env["res.lang"]._lang_get("fr_CA"):
            self.skipTest("fr_CA non installée")
        snapshot = dashboard.with_context(lang="fr_CA").get_readonly_dashboard()["snapshot"]
        labels = [f["label"] for f in snapshot["globalFilters"]]
        self.assertIn("Période", labels)

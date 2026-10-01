import json
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


def _data(texte):
    """Un tableur minimal dont la cellule A1 porte `texte`."""
    return {
        "version": 1,
        "sheets": [{"id": "sheet1", "name": "Sheet1", "cells": {"A1": {"content": texte}}}],
    }


@tagged("post_install", "-at_install")
class TestBfBi(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Dashboard = cls.env["spreadsheet.dashboard"]
        cls.section = cls.env["spreadsheet.dashboard.group"].create({"name": "Banc BI"})
        cls.g_designer = cls.env.ref("bf_bi.group_bi_designer")
        cls.g_account = cls.env.ref("base.group_system")
        cls.designer = new_test_user(cls.env, "bi-concepteur", groups="base.group_user,bf_bi.group_bi_designer")
        cls.designer2 = new_test_user(cls.env, "bi-concepteur-2", groups="base.group_user,bf_bi.group_bi_designer")
        cls.employee = new_test_user(cls.env, "bi-employe", groups="base.group_user")
        cls.dashboard = cls.Dashboard.create({
            "name": "Heures",
            "dashboard_group_id": cls.section.id,
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
        })

    def as_(self, user):
        return self.dashboard.with_user(user)

    # -- enregistrement ---------------------------------------------------

    def test_save_increments_revision_and_keeps_a_version(self):
        res = self.as_(self.designer).bf_save(_data("v1"), 0)
        self.assertEqual(res, {"status": "saved", "revision": 1})
        self.assertEqual(json.loads(self.dashboard.spreadsheet_data)["sheets"][0]["cells"]["A1"]["content"], "v1")
        self.assertEqual(self.dashboard.bf_version_ids.mapped("revision"), [1])

    def test_stale_revision_is_refused_without_writing(self):
        self.as_(self.designer).bf_save(_data("de 1"), 0)
        res = self.as_(self.designer2).bf_save(_data("de 2, sur une révision dépassée"), 0)
        self.assertEqual(res["status"], "conflict")
        self.assertEqual(res["revision"], 1)
        self.assertIn("de 1", self.dashboard.spreadsheet_data)
        self.assertEqual(self.dashboard.bf_revision, 1)

    def test_save_renames(self):
        self.as_(self.designer).bf_save(_data("x"), 0, name="  Heures et marge  ")
        self.assertEqual(self.dashboard.name, "Heures et marge")

    def test_rename_in_one_language_renames_for_everyone(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        self.as_(self.designer).with_context(lang="fr_CA").bf_save(_data("x"), 0, name="Heures du trimestre")
        self.assertEqual(self.dashboard.with_context(lang="en_US").name, "Heures du trimestre")
        self.assertEqual(self.dashboard.with_context(lang="fr_CA").name, "Heures du trimestre")

    def test_invalid_content_is_refused(self):
        with self.assertRaises(UserError):
            self.as_(self.designer).bf_save({"pas": "un tableur"}, 0)

    def test_versions_are_pruned(self):
        d = self.as_(self.designer)
        for rev in range(35):
            d.bf_save(_data(str(rev)), rev)
        self.assertEqual(len(self.dashboard.bf_version_ids), 30)
        self.assertEqual(min(self.dashboard.bf_version_ids.mapped("revision")), 6)

    def test_restore_puts_old_content_back_as_new_revision(self):
        d = self.as_(self.designer)
        d.bf_save(_data("ancien"), 0)
        d.bf_save(_data("nouveau"), 1)
        ancienne = self.dashboard.bf_version_ids.filtered(lambda v: v.revision == 1)
        ancienne.with_user(self.designer).action_restore()
        self.assertEqual(self.dashboard.bf_revision, 3)
        self.assertIn("ancien", self.dashboard.spreadsheet_data)

    # -- droits -------------------------------------------------------------

    def test_employee_who_sees_the_dashboard_cannot_edit_it(self):
        self.assertTrue(self.as_(self.employee).has_access("read"))
        with self.assertRaises(AccessError):
            self.as_(self.employee).bf_save(_data("x"), 0)
        with self.assertRaises(AccessError):
            self.as_(self.employee).bf_get_edit_data()
        with self.assertRaises(AccessError):
            self.as_(self.employee).action_edit_dashboard()

    def test_write_access_without_designer_group_is_not_enough(self):
        # Un autre module pourrait donner l'écriture sur les tableaux de bord à un groupe :
        # sans « Concepteur BI », pas d'éditeur.
        groupe = self.env["res.groups"].create({"name": "Banc : écriture des tableaux de bord"})
        self.env["ir.model.access"].create({
            "name": "banc écriture", "model_id": self.env["ir.model"]._get("spreadsheet.dashboard").id,
            "group_id": groupe.id, "perm_read": True, "perm_write": True})
        self.employee.groups_id = [(4, groupe.id)]
        self.assertTrue(self.as_(self.employee).has_access("write"))
        with self.assertRaises(AccessError):
            self.as_(self.employee).bf_get_edit_data()
        with self.assertRaises(AccessError):
            self.as_(self.employee).bf_save(_data("x"), 0)

    def test_readable_but_not_writable_dashboard_gives_no_editor_content(self):
        # Une règle d'écriture seule : le concepteur lit le tableau de bord sans pouvoir le
        # modifier. L'éditeur (qui livre tout le contenu) doit le lui refuser.
        self.env["ir.rule"].create({
            "name": "banc : pas d'écriture sur ce tableau", "model_id": self.env["ir.model"]._get("spreadsheet.dashboard").id,
            # Règle globale (sans groupe) : combinée par ET ; une règle de groupe serait
            # combinée par OU avec celle des employés et n'empêcherait rien.
            "domain_force": "[('id', '!=', %d)]" % self.dashboard.id,
            "perm_read": False, "perm_create": False, "perm_unlink": False, "perm_write": True})
        self.assertTrue(self.as_(self.designer).has_access("read"))
        with self.assertRaises(AccessError):
            self.as_(self.designer).bf_get_edit_data()

    def test_designer_cannot_edit_a_dashboard_hidden_from_him(self):
        self.dashboard.group_ids = [(6, 0, [self.g_account.id])]
        with self.assertRaises(AccessError):
            self.as_(self.designer).bf_save(_data("x"), 0)

    def test_new_dashboard_is_visible_to_designers_only(self):
        new_id = self.Dashboard.with_user(self.designer).bf_action_new_dashboard(name="Brouillon")
        new = self.Dashboard.browse(new_id)
        self.assertEqual(new.group_ids, self.g_designer)
        self.assertFalse(new.with_user(self.employee).has_access("read"))
        self.assertTrue(new.with_user(self.designer2).has_access("write"))

    def test_employee_cannot_create_a_dashboard(self):
        with self.assertRaises(AccessError):
            self.Dashboard.with_user(self.employee).bf_action_new_dashboard(name="Non")

    def test_versions_follow_dashboard_access(self):
        self.as_(self.designer).bf_save(_data("Salaire de la direction : 142 000 $"), 0)
        version = self.dashboard.bf_version_ids
        self.assertTrue(version.with_user(self.designer2).has_access("read"))
        self.dashboard.group_ids = [(6, 0, [self.g_account.id])]
        self.assertFalse(version.with_user(self.designer2).has_access("read"))
        self.assertFalse(self.env["bf.bi.dashboard.version"].with_user(self.designer2).search(
            [("dashboard_id", "=", self.dashboard.id)]))
        # Un employé n'a aucun accès aux versions, même d'un tableau de bord qu'il voit.
        self.dashboard.group_ids = [(6, 0, [self.env.ref("base.group_user").id])]
        with self.assertRaises(AccessError):
            version.with_user(self.employee).read(["spreadsheet_data"])

    def test_nobody_writes_versions_directly(self):
        self.as_(self.designer).bf_save(_data("x"), 0)
        version = self.dashboard.bf_version_ids.with_user(self.designer)
        with self.assertRaises(AccessError):
            version.write({"spreadsheet_data": "{}"})
        with self.assertRaises(AccessError):
            version.unlink()

    # -- tableaux de bord livrés ----------------------------------------------

    def _ship(self, dashboard):
        self.env["ir.model.data"].create({
            "module": "spreadsheet_dashboard", "name": "bf_bi_test_livre_%s" % dashboard.id,
            "model": dashboard._name, "res_id": dashboard.id,
        })
        dashboard.invalidate_recordset(["bf_is_shipped"])

    def test_shipped_dashboard_is_not_saved_in_place(self):
        self._ship(self.dashboard)
        self.assertTrue(self.dashboard.bf_is_shipped)
        with self.assertRaises(UserError):
            self.as_(self.designer).bf_save(_data("x"), 0)

    def test_editing_a_shipped_dashboard_edits_a_copy(self):
        self._ship(self.dashboard)
        action = self.as_(self.designer).action_edit_dashboard()
        copy = self.Dashboard.browse(action["params"]["dashboard_id"])
        self.assertNotEqual(copy, self.dashboard)
        self.assertFalse(copy.bf_is_shipped)
        self.assertEqual(action["tag"], "bf_bi.dashboard_editor")
        ids = [d["id"] for d in self.Dashboard.with_user(self.designer).bf_list_editable()]
        self.assertIn(copy.id, ids)
        self.assertNotIn(self.dashboard.id, ids)

    def test_copy_of_a_shipped_dashboard_is_a_draft_for_designers(self):
        self._ship(self.dashboard)
        action = self.as_(self.designer).action_edit_dashboard()
        copy = self.Dashboard.browse(action["params"]["dashboard_id"])
        self.assertEqual(copy.group_ids, self.g_designer)

    def test_restore_refuses_to_claim_success_on_conflict(self):
        self.as_(self.designer).bf_save(_data("v1"), 0)
        version = self.dashboard.bf_version_ids
        with patch.object(type(self.Dashboard), "bf_save", autospec=True,
                          return_value={"status": "conflict", "revision": 9, "author": "x"}):
            with self.assertRaises(UserError):
                version.with_user(self.designer).action_restore()

    def test_name_must_be_text(self):
        with self.assertRaises(UserError):
            self.as_(self.designer).bf_save(_data("x"), 0, name=["pas", "un", "nom"])

    # -- partage public -------------------------------------------------------

    def test_public_share_is_reserved_to_its_group(self):
        Share = self.env["spreadsheet.dashboard.share"]
        vals = {"dashboard_id": self.dashboard.id}
        with self.assertRaises(AccessError):
            Share.with_user(self.designer).create(vals)
        self.designer.groups_id = [(4, self.env.ref("bf_bi.group_bi_share").id)]
        self.assertTrue(Share.with_user(self.designer).create(vals))


@tagged("post_install", "-at_install")
class TestBfBiMultiCompany(TransactionCase):
    """Une société ne voit ni les tableaux de bord, ni les versions, ni les mesures d'une autre."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.c1 = cls.env.company
        cls.c2 = cls.env["res.company"].create({"name": "Société B banc BI"})
        cls.designer = new_test_user(cls.env, "bi-mc-concepteur", groups="base.group_user,bf_bi.group_bi_designer",
                                     company_id=cls.c1.id, company_ids=[(6, 0, [cls.c1.id])])
        section = cls.env["spreadsheet.dashboard.group"].create({"name": "Banc BI sociétés"})
        cls.d2 = cls.env["spreadsheet.dashboard"].create({
            "name": "Tableau B", "dashboard_group_id": section.id, "company_id": cls.c2.id,
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])]})
        cls.d2.bf_save(_data("chiffres de B"), 0)

    def test_other_company_dashboard_and_versions_are_hidden(self):
        with self.assertRaises(AccessError):
            self.d2.with_user(self.designer).bf_get_edit_data()
        self.assertFalse(self.env["bf.bi.dashboard.version"].with_user(self.designer).search(
            [("dashboard_id", "=", self.d2.id)]))

    def test_other_company_measure_is_hidden(self):
        m = self.env["bf.bi.measure"].create({"name": "B", "code": "essai_mc_b", "kind": "formula",
                                              "expression": "1", "company_id": self.c2.id})
        self.assertFalse(self.env["bf.bi.measure"].with_user(self.designer).search([("id", "=", m.id)]))
        with self.assertRaises(UserError):  # inconnue pour lui
            self.env["bf.bi.measure"].with_user(self.designer).bf_value("essai_mc_b", {})


@tagged("post_install", "-at_install")
class TestBfBiStandardTexts(TransactionCase):
    """Les textes que bf_bi pose (filtres, légende des tuiles) suivent la langue de la personne qui
    regarde, sur TOUT tableau de bord, pas seulement les modèles livrés."""

    def test_standard_labels_follow_the_reader_language(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        section = self.env["spreadsheet.dashboard.group"].create({"name": "Banc textes"})
        data = {"version": 22, "sheets": [{"id": "s", "name": "S", "cells": {}, "figures": [
                    {"id": "f", "x": 0, "y": 0, "width": 272, "height": 130, "tag": "chart",
                     "data": {"type": "scorecard", "title": {"text": "Heures"}, "keyValue": "A1",
                              "baselineDescription": "vs période précédente entière"}}]}],
                "globalFilters": [{"id": "bf_bi_periode", "type": "date", "label": "Période", "rangeType": "fixedPeriod"},
                                  {"id": "bf_bi_client", "type": "relation", "label": "Client", "modelName": "res.partner"},
                                  {"id": "autre", "type": "text", "label": "Mon filtre à moi"}]}
        d = self.env["spreadsheet.dashboard"].create({"name": "Fait main", "dashboard_group_id": section.id,
                                                      "spreadsheet_data": json.dumps(data)})
        snap = d.with_context(lang="en_US").get_readonly_dashboard()["snapshot"]
        self.assertEqual([f["label"] for f in snap["globalFilters"]], ["Period", "Customer", "Mon filtre à moi"])
        # L'ancien nom (ignoré par o-spreadsheet 18) est renommé en « baselineDescr », puis traduit.
        # Ancienne formulation française : reconnue, et remise dans la formulation courante.
        self.assertEqual(snap["sheets"][0]["figures"][0]["data"]["baselineDescr"], "vs previous period")
        self.assertNotIn("baselineDescription", snap["sheets"][0]["figures"][0]["data"])
        self.assertEqual(snap["sheets"][0]["figures"][0]["data"]["title"]["text"], "Heures", "titre saisi : intact")
        self.assertNotIn("baselineDescription", snap["sheets"][0]["figures"][0]["data"])
        snap = d.with_context(lang="fr_CA").get_readonly_dashboard()["snapshot"]
        self.assertEqual(snap["globalFilters"][0]["label"], "Période")

    def test_measure_comparison_defaults_to_percent_and_is_listed(self):
        m = self.env["bf.bi.measure"].create({"name": "c", "code": "essai_comparaison", "kind": "formula", "expression": "1"})
        self.assertEqual(m.comparison, "percentage")
        m.comparison = "difference"
        listed = [x for x in self.env["bf.bi.measure"].bf_list() if x["code"] == "essai_comparaison"]
        self.assertEqual(listed[0]["comparison"], "difference")


    def test_saved_measure_tile_says_same_length(self):
        """Une tuile BF.MEASURE enregistrée avant 1.6.0 (« période entière ») dit « même durée » ;
        une tuile de tableau croisé garde « période entière » ; une légende saisie reste intacte."""
        self.env["res.lang"]._activate_lang("fr_CA")
        section = self.env["spreadsheet.dashboard.group"].create({"name": "Banc légendes"})
        tuile = lambda fid, base, legende: {"id": fid, "x": 0, "y": 0, "width": 272, "height": 130, "tag": "chart",
                                            "data": {"type": "scorecard", "title": {"text": fid}, "keyValue": "A1",
                                                     "baseline": base, "baselineDescription": legende}}
        data = {"version": 22, "sheets": [
            {"id": "s", "name": "Dashboard", "cells": {"C1": {"content": "=PIVOT.VALUE(1,\"unit_amount:sum\")"}},
             "figures": [tuile("mesure", "'bf_bi calculs'!B1", "vs période précédente entière"),
                         tuile("pivot", "C1", "vs période précédente entière"),
                         tuile("main", "'bf_bi calculs'!B1", "Comparé à l'an dernier")]},
            {"id": "c", "name": "bf_bi calculs", "cells": {"B1": {"content": "=IF(OR(BF.MEASURE(\"hours\",-1)=\"\"),\"\",BF.MEASURE(\"hours\",-1))"}}, "figures": []}]}
        d = self.env["spreadsheet.dashboard"].create({"name": "Avant 1.6", "dashboard_group_id": section.id,
                                                      "spreadsheet_data": json.dumps(data)})
        figs = {f["id"]: f["data"]["baselineDescr"] for f in
                d.with_context(lang="fr_CA").get_readonly_dashboard()["snapshot"]["sheets"][0]["figures"]}
        self.assertEqual(figs, {"mesure": "vs période équivalente",
                                "pivot": "vs période précédente",
                                "main": "Comparé à l'an dernier"})

    def _dashboard(self, data, name="Relecture"):
        section = self.env["spreadsheet.dashboard.group"].create({"name": "Banc relecture"})
        return self.env["spreadsheet.dashboard"].create({"name": name, "dashboard_group_id": section.id,
                                                         "spreadsheet_data": json.dumps(data)})

    def test_odd_content_never_breaks_reading(self):
        """Une légende tapée « Client » (le mot d'un FILTRE), une figure sans objet de
        données, une légende qui n'est pas du texte : le tableau de bord se lit quand même."""
        self.env["res.lang"]._activate_lang("fr_CA")
        data = {"version": 22, "sheets": [{"id": "s", "name": "S", "cells": {}, "figures": [
            {"id": "a", "tag": "chart", "data": {"type": "scorecard", "baselineDescr": "Client", "baseline": 3}},
            {"id": "b", "tag": "chart", "data": {"type": "scorecard", "baselineDescr": "Période"}},
            {"id": "c", "tag": "chart", "data": {"type": "scorecard", "baselineDescr": ["x"]}},
            {"id": "d", "tag": "chart", "data": None}, "pas une figure"]}, "pas une feuille"],
            "globalFilters": ["pas un filtre", {"id": "bf_bi_client", "label": None}]}
        # Odoo refuse lui-même de créer un tableau de bord pareil : la forme peut quand même
        # arriver par une autre version ou une écriture directe, d'où l'appel direct.
        d = self._dashboard({"version": 22, "sheets": [{"id": "s", "name": "S", "cells": {}, "figures": []}]})
        for lang in ("fr_CA", "en_US"):
            figs = d.with_context(lang=lang)._bf_translate_standard(json.loads(json.dumps(data)))["sheets"][0]["figures"]
            self.assertEqual(figs[0]["data"]["baselineDescr"], "Client", "légende saisie : intacte")
            self.assertEqual(figs[1]["data"]["baselineDescr"], "Période")

    def test_renamed_filter_keeps_its_name(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        data = {"version": 22, "sheets": [{"id": "s", "name": "S", "cells": {}, "figures": []}],
                "globalFilters": [{"id": "bf_bi_periode", "type": "date", "label": "Exercice", "rangeType": "fixedPeriod"},
                                  {"id": "bf_bi_client", "type": "relation", "label": "Client", "modelName": "res.partner"}]}
        snap = self._dashboard(data).with_context(lang="en_US").get_readonly_dashboard()["snapshot"]
        self.assertEqual([f["label"] for f in snap["globalFilters"]], ["Exercice", "Customer"])

    def test_same_length_legend_only_for_previous_measure_on_its_own_sheet(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        whole = "vs période précédente entière"
        # La figure est sur la DEUXIÈME feuille : « C1 » sans nom de feuille vise la sienne,
        # pas la première.
        data = {"version": 22, "sheets": [
            {"id": "x", "name": "Autre", "cells": {"C1": {"content": "=1"}}, "figures": []},
            {"id": "s", "name": "Dashboard", "cells": {"C1": {"content": "=BF.MEASURE(\"hours\", -1)"},
                                                       "C2": {"content": "=BF.MEASURE(\"target\")"}},
             "figures": [{"id": "rel", "tag": "chart", "data": {"type": "scorecard", "baseline": "C1", "baselineDescr": whole}},
                         {"id": "cible", "tag": "chart", "data": {"type": "scorecard", "baseline": "C2", "baselineDescr": whole}}]}]}
        d = self._dashboard({"version": 22, "sheets": [{"id": "s", "name": "S", "cells": {}, "figures": []}]})
        figs = {f["id"]: f["data"]["baselineDescr"] for f in
                d.with_context(lang="fr_CA")._bf_translate_standard(data)["sheets"][1]["figures"]}
        self.assertEqual(figs, {"rel": "vs période équivalente", "cible": "vs période précédente"})

    def test_save_refuses_content_that_would_break_every_reader(self):
        designer = new_test_user(self.env, "bi-relecture-concepteur", groups="base.group_user,bf_bi.group_bi_designer")
        d = self._dashboard({"version": 22, "sheets": [{"id": "s", "name": "S", "cells": {}, "figures": []}]})
        d.group_ids = [Command.set([self.env.ref("bf_bi.group_bi_designer").id])]
        rev = d.bf_revision
        for bad in ({"sheets": "x"}, {"sheets": []}, {"sheets": ["x"]},
                    {"sheets": [{"cells": {}, "figures": [{"data": "x"}]}]},
                    {"sheets": [{"cells": {}, "figures": [{"data": {"baselineDescr": ["x"]}}]}]},
                    {"sheets": [{"cells": {"A1": "x"}}]}, {"sheets": [{}], "globalFilters": "x"}):
            with self.assertRaises(UserError, msg=str(bad)):
                d.with_user(designer).bf_save(bad, rev)

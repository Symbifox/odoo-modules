"""Couverture du socle de sélection de cible.

Les cibles utilisées sont volontairement `res.partner` (toujours présent et
porteur d'un chatter) : le module ne dépend que de `web` et `mail`, donc la
suite doit tourner sur une base où ni les projets ni les tickets n'existent.
Les gardes de `_get_chatter_target`, qui ont besoin d'un assistant réel, sont
éprouvées côté `bf_bloc_notes`, où les montages existent déjà.
"""

from odoo.exceptions import AccessError
from odoo.models import check_method_name
from odoo.tests import TransactionCase, new_test_user, tagged

from ..models.bf_chatter_target import PRIORITY_MODELS


@tagged("bf_chatter_target", "post_install", "-at_install")
class TestChatterTarget(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Target = cls.env["bf.chatter.target"]
        cls.partner = cls.env["res.partner"].create({
            "name": "ZZZ Cible de chatter",
            "email": "cible@example.invalid",
        })

    # ------------------------------------------------------------------
    # Modèles compatibles
    # ------------------------------------------------------------------
    def test_selection_keeps_only_chatter_models(self):
        selection = dict(self.Target._thread_model_selection())
        self.assertIn("res.partner", selection)
        # `ir.ui.view` existe et n'est pas transient, mais ne porte pas de chatter.
        self.assertNotIn("ir.ui.view", selection)

    def test_selection_excludes_transient_models(self):
        selection = set(dict(self.Target._thread_model_selection()))
        transients = set(
            self.env["ir.model"].sudo()
            .search([("transient", "=", True)]).mapped("model")
        )
        self.assertFalse(selection & transients)

    def test_selection_puts_priority_models_first(self):
        models = [name for name, _label in self.Target._thread_model_selection()]
        partner_index = models.index("res.partner")
        for index, model in enumerate(models):
            if model not in PRIORITY_MODELS:
                self.assertGreater(
                    index, partner_index,
                    f"{model} précède res.partner alors qu'il n'est pas prioritaire",
                )

    def test_selection_only_exposes_models_of_the_registry(self):
        """`ir.model` garde des lignes pour des modules désinstallés : les
        proposer donnerait un Reference cassé au premier clic."""
        for model, _label in self.Target._thread_model_selection():
            self.assertIn(model, self.env, f"{model} absent du registre")

    def test_selection_honours_config_parameter(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_chatter_target.models", "res.partner",
        )
        selection = dict(self.Target._thread_model_selection())
        self.assertEqual(list(selection), ["res.partner"])

    def test_selection_falls_back_to_legacy_bloc_notes_parameter(self):
        """Une base où bf_bloc_notes avait déjà restreint la liste garde sa
        restriction après l'unification, sans qu'on ait à la ressaisir."""
        Param = self.env["ir.config_parameter"].sudo()
        Param.set_param("bf_chatter_target.models", "")
        Param.set_param("bf_bloc_notes.reference_models", "res.partner")
        selection = dict(self.Target._thread_model_selection())
        self.assertEqual(list(selection), ["res.partner"])

    # ------------------------------------------------------------------
    # Résolution d'une référence
    # ------------------------------------------------------------------
    def test_resolve_technical_reference(self):
        self.assertEqual(
            self.Target._resolve(f"res.partner,{self.partner.id}"), self.partner,
        )
        self.assertEqual(
            self.Target._resolve(f"res.partner:{self.partner.id}"), self.partner,
        )

    def test_resolve_alias(self):
        self.assertEqual(
            self.Target._resolve(f"contact:{self.partner.id}"), self.partner,
        )
        self.assertEqual(
            self.Target._resolve(f"partner#{self.partner.id}"), self.partner,
        )

    def test_resolve_rejects_model_without_chatter(self):
        view = self.env["ir.ui.view"].search([], limit=1)
        self.assertTrue(view)
        self.assertIsNone(self.Target._resolve(f"ir.ui.view:{view.id}"))

    def test_resolve_legacy_web_url(self):
        self.assertEqual(
            self.Target._resolve(
                "https://odoo.example.com/web"
                f"#model=res.partner&id={self.partner.id}"
            ),
            self.partner,
        )

    def test_resolve_odoo_18_url_uses_action_path(self):
        action = self.env["ir.actions.act_window"].sudo().search(
            [("res_model", "=", "res.partner"), ("path", "!=", False)], limit=1,
        )
        if not action:
            self.skipTest("Aucune action res.partner avec un chemin d'URL")
        self.assertEqual(
            self.Target._resolve(
                f"https://odoo.example.com/odoo/{action.path}/{self.partner.id}"
            ),
            self.partner,
        )

    def test_resolve_unknown_returns_none(self):
        self.assertIsNone(self.Target._resolve(""))
        self.assertIsNone(self.Target._resolve(None))
        self.assertIsNone(self.Target._resolve("n'importe quoi"))
        self.assertIsNone(self.Target._resolve("res.partner:999999999"))

    def test_resolve_respects_record_rules(self):
        """Un numéro nu ne doit pas laisser deviner une fiche qu'on ne peut pas lire."""
        company = self.env["res.company"].create({"name": "Cloison BFCT"})
        hidden = self.env["res.partner"].create({
            "name": "Fiche cloisonnée", "company_id": company.id,
        })
        user = new_test_user(self.env, login="bfct_reader", groups="base.group_user")
        self.assertNotIn(company, user.company_ids)
        self.assertIsNone(
            self.Target.with_user(user)._resolve(f"res.partner:{hidden.id}"),
        )
        # Contre-épreuve : la même référence se résout pour qui a le droit.
        self.assertEqual(self.Target._resolve(f"res.partner:{hidden.id}"), hidden)

    # ------------------------------------------------------------------
    # Recherche transversale
    # ------------------------------------------------------------------
    def test_search_targets_ignores_short_queries(self):
        self.assertEqual(self.Target.search_targets("a"), [])
        self.assertEqual(self.Target.search_targets(""), [])
        self.assertEqual(self.Target.search_targets(None), [])

    def test_search_targets_finds_by_name(self):
        groups = self.Target.search_targets("ZZZ Cible")
        found = [
            (group["model"], result["id"])
            for group in groups for result in group["results"]
        ]
        self.assertIn(("res.partner", self.partner.id), found)
        group = next(g for g in groups if g["model"] == "res.partner")
        self.assertTrue(group["model_label"])
        self.assertTrue(group["icon"])

    def test_search_targets_puts_exact_reference_first(self):
        groups = self.Target.search_targets(f"res.partner,{self.partner.id}")
        self.assertTrue(groups)
        self.assertEqual(groups[0]["model"], "res.partner")
        self.assertEqual(groups[0]["results"][0]["id"], self.partner.id)

    def test_search_targets_deduplicates_across_groups(self):
        """La référence exacte ne doit pas réapparaître dans la recherche texte."""
        groups = self.Target.search_targets(f"res.partner,{self.partner.id}")
        keys = [
            (group["model"], result["id"])
            for group in groups for result in group["results"]
        ]
        self.assertEqual(len(keys), len(set(keys)))

    def test_search_targets_bounds_the_limit(self):
        self.env["res.partner"].create([
            {"name": f"QQQ Cible en lot {index}"} for index in range(25)
        ])
        groups = self.Target.search_targets("QQQ Cible en lot", limit=10**6)
        self.assertTrue(groups)
        for group in groups:
            self.assertLessEqual(len(group["results"]), 20)

    def test_search_targets_is_rpc_callable(self):
        """Le sélecteur appelle la méthode par `call_kw` : elle doit être publique,
        et le résolveur qui rend un recordset doit rester privé."""
        check_method_name("search_targets")
        with self.assertRaises(AccessError):
            check_method_name("_resolve")

    def test_search_targets_hides_unreadable_records(self):
        company = self.env["res.company"].create({"name": "Cloison BFCT 2"})
        self.env["res.partner"].create({
            "name": "WWW Fiche cloisonnée", "company_id": company.id,
        })
        user = new_test_user(self.env, login="bfct_reader2", groups="base.group_user")
        groups = self.Target.with_user(user).search_targets("WWW Fiche")
        found = [r["name"] for g in groups for r in g["results"]]
        self.assertNotIn("WWW Fiche cloisonnée", found)

    # ------------------------------------------------------------------
    # Mixin
    # ------------------------------------------------------------------
    def test_mixin_selection_matches_the_socle(self):
        Mixin = self.env["bf.chatter.target.mixin"]
        self.assertEqual(
            Mixin._selection_chatter_target(),
            self.Target._thread_model_selection(),
        )

    def test_mixin_resolve_delegates(self):
        Mixin = self.env["bf.chatter.target.mixin"]
        self.assertEqual(
            Mixin._resolve_chatter_target(f"contact:{self.partner.id}"), self.partner,
        )

    def test_truncate(self):
        self.assertEqual(self.Target._truncate("  a   b  "), "a b")
        self.assertEqual(len(self.Target._truncate("x" * 300)), 120)
        self.assertTrue(self.Target._truncate("x" * 300).endswith("…"))

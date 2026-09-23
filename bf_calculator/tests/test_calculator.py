from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

NBSP = "\u00a0"


@tagged("post_install", "-at_install")
class TestCalculator(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.alice = new_test_user(
            cls.env, "calc_alice", groups="base.group_user,base.group_partner_manager",
            lang="fr_CA")
        cls.bob = new_test_user(cls.env, "calc_bob", groups="base.group_user", lang="en_US")
        cls.Entry = cls.env["bf.calculator.entry"]
        # Une base neuve a déjà une taxe de vente par défaut (15 %) : sans la
        # retirer, le profil « Québec par défaut » n'existe pas (voulu : il ne
        # sert que quand la comptabilité n'offre aucune taxe).
        if "account.tax" in cls.env:
            cls.env["account.tax"].search([]).write({"active": False})

    def as_alice(self):
        return self.Entry.with_user(self.alice).with_context(lang="fr_CA")

    def as_bob(self):
        return self.Entry.with_user(self.bob).with_context(lang="en_US")

    # ------------------------------------------------------------------
    # Calcul et mise en forme
    # ------------------------------------------------------------------
    def test_record_with_labels_in_french(self):
        res = self.as_alice().calc_record("Budget stagiaire : 6 (semaines) * 750 $ =")
        self.assertTrue(res["ok"])
        entry = self.Entry.browse(res["entry"]["id"])
        self.assertEqual(entry.user_id, self.alice)
        self.assertEqual(entry.title, "Budget stagiaire")
        self.assertEqual(entry.display_text, "6 semaines × 750 $")
        self.assertEqual(entry.result, 4500)
        self.assertEqual(entry.result_text, f"4{NBSP}500,00{NBSP}$")
        self.assertEqual(entry._chatter_line(),
                         f"Budget stagiaire : 6 semaines × 750 $ = 4{NBSP}500,00{NBSP}$")

    def test_separate_title_wins(self):
        res = self.as_alice().calc_record("Écrit : 2 * 3", title="Saisi à part")
        self.assertEqual(res["entry"]["title"], "Saisi à part")

    def test_english_formatting(self):
        res = self.as_bob().calc_record("6 weeks * 750 $")
        self.assertEqual(res["entry"]["result_text"], "$4,500.00")
        res = self.as_bob().calc_evaluate("1,250 * 2")
        self.assertEqual(res["result_text"], "2,500")

    def test_french_comma_is_decimal(self):
        res = self.as_alice().calc_evaluate("12,5 * 2")
        self.assertEqual(res["value"], 25)
        self.assertEqual(res["result_text"], "25")

    def test_duration_result(self):
        res = self.as_alice().calc_record("1 h 45 + 2 h 30")
        self.assertEqual(res["entry"]["result_text"], "4,25 h (4 h 15)")
        self.assertEqual(res["entry"]["mode"], "hours")

    def test_error_is_returned_not_raised(self):
        res = self.as_alice().calc_record("5 / 0")
        self.assertFalse(res["ok"])
        self.assertTrue(res["error"])
        self.assertFalse(self.as_alice().search([]))

    def test_origin_is_kept(self):
        partner = self.env["res.partner"].create({"name": "Origine"})
        res = self.as_alice().calc_record("1 + 1", res_model="res.partner", res_id=partner.id)
        entry = self.Entry.browse(res["entry"]["id"])
        self.assertEqual((entry.res_model, entry.res_id), ("res.partner", partner.id))
        # Un modèle inconnu n'est pas gardé.
        res = self.as_alice().calc_record("1 + 1", res_model="no.such.model", res_id=1)
        self.assertFalse(self.Entry.browse(res["entry"]["id"]).res_model)

    # ------------------------------------------------------------------
    # Isolement de l'historique
    # ------------------------------------------------------------------
    def test_history_is_private(self):
        mine = self.as_alice().calc_record("1 + 1")["entry"]["id"]
        self.as_bob().calc_record("2 + 2")
        self.assertEqual([e["id"] for e in self.as_alice().calc_history()], [mine])
        self.assertNotIn(mine, [e["id"] for e in self.as_bob().calc_history()])
        self.assertFalse(self.as_bob().search([("id", "=", mine)]))
        with self.assertRaises(AccessError):
            self.as_bob().browse(mine).read(["result"])
        # Supprimer la ligne d'une autre personne ne fait rien… ou échoue.
        try:
            self.as_bob().browse(mine).calc_delete()
        except AccessError:
            pass
        self.assertTrue(self.Entry.browse(mine).exists())

    def test_admin_does_not_see_others_history(self):
        mine = self.as_alice().calc_record("1 + 1")["entry"]["id"]
        admin = self.env.ref("base.user_admin")
        self.assertFalse(self.Entry.with_user(admin).search([("id", "=", mine)]))

    def test_history_search_and_clear(self):
        self.as_alice().calc_record("Loyer : 1200 * 12")
        self.as_alice().calc_record("3 + 4")
        found = self.as_alice().calc_history(search="Loyer")
        self.assertEqual(len(found), 1)
        self.as_bob().calc_record("5 + 5")
        self.as_alice().calc_clear_history()
        self.assertFalse(self.as_alice().calc_history())
        self.assertEqual(len(self.as_bob().calc_history()), 1)

    # ------------------------------------------------------------------
    # Taxes
    # ------------------------------------------------------------------
    def _qc_group_tax(self):
        Tax = self.env["account.tax"]
        company = self.env.company
        group_tps = self.env["account.tax.group"].create({"name": "TPS", "company_id": company.id})
        group_tvq = self.env["account.tax.group"].create({"name": "TVQ", "company_id": company.id})
        tps = Tax.create({"name": "5% TPS", "amount": 5, "amount_type": "percent",
                          "type_tax_use": "none", "tax_group_id": group_tps.id,
                          "company_id": company.id})
        tvq = Tax.create({"name": "9.975% TVQ", "amount": 9.975, "amount_type": "percent",
                          "type_tax_use": "none", "tax_group_id": group_tvq.id,
                          "company_id": company.id})
        tvh = Tax.create({"name": "13% TVH", "amount": 13, "amount_type": "percent",
                          "type_tax_use": "sale", "company_id": company.id})
        group = Tax.create({"name": "14.975% TPS+TVQ", "amount_type": "group",
                            "type_tax_use": "sale", "company_id": company.id,
                            "children_tax_ids": [(6, 0, [tps.id, tvq.id])]})
        return group, tvh

    def test_tax_profiles_read_from_accounting(self):
        if "account.tax" not in self.env:
            self.skipTest("account absent")
        group, tvh = self._qc_group_tax()
        profiles = self.as_alice().calc_tax_profiles()
        self.assertEqual(profiles[0]["key"], f"tax:{group.id}")
        self.assertEqual([c["name"] for c in profiles[0]["components"]], ["TPS", "TVQ"])
        self.assertEqual([c["rate"] for c in profiles[0]["components"]], [5.0, 9.975])
        self.assertIn(f"tax:{tvh.id}", [p["key"] for p in profiles])
        self.assertFalse(any(p["default"] for p in profiles))

    def test_default_profile_without_qc_tax(self):
        self.env["account.tax"].search([]).write({"active": False}) \
            if "account.tax" in self.env else None
        profiles = self.as_alice().calc_tax_profiles()
        self.assertEqual(profiles[0]["key"], "default_qc")
        self.assertEqual([c["rate"] for c in profiles[0]["components"]], [5.0, 9.975])

    def test_reverse_tax_exact(self):
        res = self.as_alice().calc_tax("1 149,75", "default_qc", "reverse")
        self.assertTrue(res["ok"])
        self.assertEqual(res["base"], f"1{NBSP}000,00{NBSP}$")
        self.assertEqual([t["amount"] for t in res["taxes"]],
                         [f"50,00{NBSP}$", f"99,75{NBSP}$"])
        self.assertFalse(res["inexact"])

    def test_reverse_tax_rounding_lands_on_total(self):
        # 100 / 1,14975 = 86,975… → 86,98 refacture à 100,01 ; 86,97 refacture à 100,00.
        res = self.as_alice().calc_tax("100", "default_qc", "reverse", record=True)
        self.assertEqual(res["base"], f"86,97{NBSP}$")
        self.assertEqual(res["total"], f"100,00{NBSP}$")
        self.assertEqual([t["amount"] for t in res["taxes"]], [f"4,35{NBSP}$", f"8,68{NBSP}$"])
        self.assertFalse(res["inexact"])
        entry = self.Entry.browse(res["entry"]["id"])
        self.assertEqual(entry.mode, "tax")
        self.assertAlmostEqual(entry.result, 86.97)

    def test_forward_tax(self):
        res = self.as_alice().calc_tax("=500*2", "default_qc", "forward")
        self.assertEqual(res["total"], f"1{NBSP}149,75{NBSP}$")

    def test_tax_error(self):
        res = self.as_alice().calc_tax("abc +", "default_qc", "reverse")
        self.assertFalse(res["ok"])

    # ------------------------------------------------------------------
    # Pourcentages et colonne
    # ------------------------------------------------------------------
    def test_margin_and_markup(self):
        res = self.as_alice().calc_percent("margin", "60", "100")
        values = {r["label"]: r["value"] for r in res["rows"]}
        self.assertEqual(res["result_text"], f"40{NBSP}%")
        self.assertIn(f"66,67{NBSP}%", values.values())

    def test_other_percent_kinds(self):
        self.assertEqual(self.as_alice().calc_percent("change", "80", "100")["result_text"],
                         f"25{NBSP}%")
        self.assertEqual(self.as_alice().calc_percent("discount", "200", "15")["result_text"],
                         "170")
        self.assertEqual(self.as_alice().calc_percent("portion", "30", "120")["result_text"],
                         f"25{NBSP}%")
        self.assertFalse(self.as_alice().calc_percent("change", "0", "5")["ok"])
        self.assertFalse(self.as_alice().calc_percent("nope", "1", "5")["ok"])

    def test_column(self):
        res = self.as_alice().calc_column("Montant\n1 250,00 $\n250\nTotal", record=True)
        self.assertEqual(res["result_text"], f"1{NBSP}500")
        self.assertEqual(res["skipped"], 2)
        self.assertEqual(self.Entry.browse(res["entry"]["id"]).mode, "column")
        self.assertFalse(self.as_alice().calc_column("rien ici")["ok"])

    # ------------------------------------------------------------------
    # Versement au chatter
    # ------------------------------------------------------------------
    def test_post_to_chatter_as_the_user(self):
        partner = self.env["res.partner"].create({"name": "Client du calcul"})
        entry_id = self.as_alice().calc_record("Budget : 6 (semaines) * 750 $")["entry"]["id"]
        action = self.as_alice().browse(entry_id).with_context(
            bf_calc_res_model="res.partner", bf_calc_res_id=partner.id,
        ).action_open_post_wizard()
        self.assertEqual(action["views"], [(False, "form")])
        wizard = self.env["bf.calculator.post"].with_user(self.alice).with_context(
            action["context"]).create({"comment": "Pour la soumission"})
        self.assertEqual(wizard.target_reference, partner)
        wizard.action_post()
        message = partner.message_ids.filtered(lambda m: "semaines" in (m.body or ""))
        self.assertEqual(len(message), 1)
        self.assertEqual(message.author_id, self.alice.partner_id)
        self.assertEqual(message.subtype_id, self.env.ref("mail.mt_note"))
        self.assertIn("Pour la soumission", message.body)
        # Le corps HTML garde les insécables sous forme d'entité.
        self.assertIn("4&nbsp;500,00&nbsp;$", message.body)
        self.assertEqual(self.Entry.browse(entry_id).message_id, message)

    def test_post_escapes_labels(self):
        partner = self.env["res.partner"].create({"name": "Échappement"})
        # Un descriptif peut porter des chevrons : il doit arriver échappé.
        res = self.as_alice().calc_record("2 (<img src=x onerror=alert()>) * 3")
        self.assertTrue(res["ok"])
        self.assertEqual(res["entry"]["result_text"], "6")
        # <b> survit au nettoyeur HTML de message_post, contrairement à <script> :
        # seul notre échappement peut l'empêcher de mettre le titre en gras.
        titled = self.as_alice().calc_record("2 * 3", title="<b>gras</b>")
        wizard = self.env["bf.calculator.post"].with_user(self.alice).create({
            "entry_ids": [(6, 0, [res["entry"]["id"], titled["entry"]["id"]])],
            "target_reference": f"res.partner,{partner.id}",
        })
        wizard.action_post()
        body = partner.message_ids[:1].body
        self.assertNotIn("<img", body)
        self.assertNotIn("<b>gras", body)
        self.assertIn("&lt;b&gt;gras", body)
        self.assertIn("&lt;img", body)

    def test_cannot_post_someone_elses_entry(self):
        partner = self.env["res.partner"].create({"name": "Cible"})
        bob_entry = self.as_bob().calc_record("1 + 1")["entry"]["id"]
        with self.assertRaises(UserError):
            wizard = self.env["bf.calculator.post"].with_user(self.alice).create({
                "entry_ids": [(6, 0, [bob_entry])],
                "target_reference": f"res.partner,{partner.id}",
            })
            wizard.action_post()
        self.assertFalse(self.Entry.browse(bob_entry).message_id)

    def test_post_respects_target_rights(self):
        # Une personne interne sans droit d'écriture sur la fiche : refus.
        partner = self.env["res.partner"].create({"name": "Verrouillée"})
        entry_id = self.as_alice().calc_record("1 + 1")["entry"]["id"]
        rule = self.env["ir.rule"].create({
            "name": "test : écriture refusée",
            "model_id": self.env.ref("base.model_res_partner").id,
            "domain_force": f"[('id', '!=', {partner.id})]",
            "perm_read": False, "perm_create": False, "perm_unlink": False,
            "perm_write": True,
        })
        self.assertTrue(rule)
        wizard = self.env["bf.calculator.post"].with_user(self.alice).create({
            "entry_ids": [(6, 0, [entry_id])],
            "target_reference": f"res.partner,{partner.id}",
        })
        with self.assertRaises(UserError):
            wizard.action_post()

    # ------------------------------------------------------------------
    # Purge
    # ------------------------------------------------------------------
    def _age(self, entry_id, days):
        self.env.cr.execute(
            "UPDATE bf_calculator_entry SET create_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(days=days), entry_id),
        )
        self.Entry.invalidate_model(["create_date"])

    def test_purge(self):
        old = self.as_alice().calc_record("1 + 1")["entry"]["id"]
        recent = self.as_bob().calc_record("2 + 2")["entry"]["id"]
        self._age(old, 120)
        self._age(recent, 10)
        self.env["ir.config_parameter"].sudo().set_param("bf_calculator.history_days", "90")
        self.Entry._cron_purge_history()
        self.assertFalse(self.Entry.browse(old).exists())
        self.assertTrue(self.Entry.browse(recent).exists())

    def test_purge_disabled(self):
        old = self.as_alice().calc_record("1 + 1")["entry"]["id"]
        self._age(old, 5000)
        for value in ("0", "pas un nombre"):
            self.env["ir.config_parameter"].sudo().set_param("bf_calculator.history_days", value)
            self.Entry._cron_purge_history()
            self.assertTrue(self.Entry.browse(old).exists())

    def test_default_parameter_and_cron_exist(self):
        self.assertEqual(
            self.env["ir.config_parameter"].sudo().get_param("bf_calculator.history_days"), "90")
        self.assertTrue(self.env.ref("bf_calculator.cron_purge_history").active)

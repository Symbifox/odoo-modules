import base64
import io
from datetime import date, datetime, time, timedelta

import pytz
from freezegun import freeze_time

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestRetailSupply(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.company = env.company
        env.user.groups_id = [
            Command.link(env.ref("bf_retail.group_retail_manager").id),
            Command.link(env.ref("purchase.group_purchase_user").id),
            Command.link(env.ref("stock.group_stock_user").id),
        ]
        cls.unit = env.ref("uom.product_uom_unit")
        cls.dozen = env.ref("uom.product_uom_dozen")
        cls.kg = env.ref("uom.product_uom_kgm")
        cls.warehouse = env["stock.warehouse"].search(
            [("company_id", "=", cls.company.id)], limit=1)
        assert cls.warehouse, "la société d'essai n'a pas d'entrepôt"

        Weekday = env["bf.retail.weekday"]
        cls.tuesday = Weekday.search([("code", "=", 1)], limit=1)
        cls.friday = Weekday.search([("code", "=", 4)], limit=1)

        cls.vendor = env["res.partner"].create({
            "name": "Grossiste Essai", "bf_retail_supplier": True,
            "bf_retail_delivery_day_ids": [Command.set((cls.tuesday + cls.friday).ids)],
            "bf_retail_order_cutoff": 14.0, "bf_retail_lead_days": 1,
            "bf_retail_min_order": 100.0, "bf_retail_returns_credited": True,
        })
        cls.other_vendor = env["res.partner"].create({
            "name": "Autre grossiste", "bf_retail_supplier": True})

        cls.categ = env["product.category"].create({
            "name": "Store", "bf_retail_managed": True,
            "bf_retail_margin_target": 30.0, "bf_retail_price_rounding": "0.99"})
        cls.chips = cls.make_product("Chips", price=2.49)
        cls.milk = cls.make_product("Milk 2 L", price=4.99)
        cls.today = date.today()
        cls.Guide = env["bf.retail.order.guide"]
        cls.Cost = env["bf.retail.cost"]
        cls.File = env["bf.retail.price.file"]
        cls.Info = env["product.supplierinfo"]

    @classmethod
    def make_product(cls, name, price=1.0, **kw):
        return cls.env["product.product"].create(dict({
            "name": name, "type": "consu", "is_storable": True,
            "categ_id": cls.categ.id, "uom_id": cls.unit.id, "uom_po_id": cls.unit.id,
            "list_price": price, "taxes_id": [Command.clear()],
            "supplier_taxes_id": [Command.clear()],
            "property_account_expense_id": cls.company_data["default_account_expense"].id,
        }, **kw))

    # ── helpers ──────────────────────────────────────────────────────────────────────
    def seller(self, product, partner=None, price=1.0, uom=None, **kw):
        """La ligne fournisseur d'Odoo 18 ne porte PAS son unité : `product_uom` est un champ
        lié à `uom_po_id` du produit, partagé par tous ses fournisseurs. `uom` se pose donc
        sur le produit, et une unité d'une autre catégorie que celle du stock est refusée par
        Odoo lui-même."""
        if uom:
            product.product_tmpl_id.uom_po_id = uom
        return self.Info.create(dict({
            "partner_id": (partner or self.vendor).id,
            "product_tmpl_id": product.product_tmpl_id.id,
            "product_id": product.id,
            "price": price, "min_qty": 0.0, "company_id": self.company.id,
        }, **kw))

    def rule(self, product, low, high, multiple=0.0):
        return self.env["stock.warehouse.orderpoint"].create({
            "product_id": product.id,
            "warehouse_id": self.warehouse.id,
            "location_id": self.warehouse.lot_stock_id.id,
            "product_min_qty": low, "product_max_qty": high, "qty_multiple": multiple,
            "company_id": self.company.id,
        })

    def on_hand(self, product, qty):
        self.env["stock.quant"].with_context(inventory_mode=True).create({
            "product_id": product.id,
            "location_id": self.warehouse.lot_stock_id.id,
            "inventory_quantity": qty,
        }).action_apply_inventory()

    def guide(self, partner=None):
        return self.Guide.create({
            "partner_id": (partner or self.vendor).id,
            "warehouse_id": self.warehouse.id,
            "company_id": self.company.id,
        })

    def csv_file(self, rows, header="Barcode;Description;Format;Price"):
        texte = header + "\n" + "\n".join(rows)
        return base64.b64encode(texte.encode("utf-8"))

    def price_file(self, rows, header="Barcode;Description;Format;Price", **kw):
        return self.File.create(dict({
            "partner_id": self.vendor.id, "company_id": self.company.id,
            "date": self.today, "filename": "liste.csv",
            "file": self.csv_file(rows, header=header)}, **kw))

    def xlsx_file(self, feuilles):
        """Un vrai classeur, écrit par openpyxl : le module en fait une dépendance dure et
        le lecteur n'avait jamais été mesuré sur un seul."""
        import openpyxl
        classeur = openpyxl.Workbook()
        classeur.remove(classeur.active)
        for nom, lignes in feuilles:
            feuille = classeur.create_sheet(nom)
            for ligne in lignes:
                feuille.append(ligne)
        tampon = io.BytesIO()
        classeur.save(tampon)
        return base64.b64encode(tampon.getvalue())

    def credit_note(self, product, qty, price, day=None):
        move = self.env["account.move"].create({
            "move_type": "in_refund", "partner_id": self.vendor.id,
            "invoice_date": day or self.today,
            "invoice_line_ids": [Command.create({
                "product_id": product.id, "quantity": qty, "price_unit": price,
                "tax_ids": [Command.clear()]})],
        })
        move.action_post()
        return move

    # ── the supplier's delivery days ─────────────────────────────────────────────────
    def test_the_next_deliveries_follow_the_days_and_the_cutoff(self):
        tz = pytz.timezone(self.company.partner_id.tz or "UTC")
        monday = tz.localize(datetime(2026, 11, 9, 10, 0))      # a Monday, before the cut-off
        first, after = self.vendor._bf_retail_deliveries(monday)
        self.assertEqual(first, date(2026, 11, 10), "ordered Monday morning: Tuesday's van")
        self.assertEqual(after, date(2026, 11, 13), "the one after is Friday's")
        late = tz.localize(datetime(2026, 11, 9, 15, 30))        # past 14:00
        first, after = self.vendor._bf_retail_deliveries(late)
        self.assertEqual(first, date(2026, 11, 13),
                         "past the cut-off, Tuesday's van is gone")
        self.assertEqual(after, date(2026, 11, 17))

    def test_a_supplier_without_delivery_days_delivers_any_day(self):
        """RÉÉCRIT : l'essai d'avant affirmait que sans jour coché la
        couverture saute à 7 jours, alors que l'aide du champ promet « chaque jour compte
        comme un jour de livraison ». Il figeait le défaut : la quantité proposée sur un
        fournisseur neuf était surévaluée d'un facteur ~7."""
        first, after = self.other_vendor._bf_retail_deliveries(
            pytz.utc.localize(datetime(2026, 11, 9, 10, 0)))
        self.assertEqual(first, date(2026, 11, 10), "un délai d'un jour, et chaque jour livre")
        self.assertEqual(after, date(2026, 11, 11),
                         "la livraison d'APRÈS est le lendemain, pas la semaine suivante")
        self.assertEqual((after - first).days, 1)

    # ── the guide ────────────────────────────────────────────────────────────────────
    def test_the_guide_suggests_up_to_the_maximum_in_the_supplier_unit(self):
        self.seller(self.chips, price=12.0, uom=self.dozen)
        self.rule(self.chips, 10.0, 30.0)
        self.on_hand(self.chips, 5.0)
        guide = self.guide()
        guide.action_compute()
        line = guide.line_ids.filtered(lambda ligne: ligne.product_id == self.chips)
        self.assertTrue(line, "the item of this supplier is in its guide")
        self.assertEqual(line.uom_id, self.dozen, "ordered in the item's purchase unit")
        # 30 wanted, 5 on hand: 25 units, which is 3 dozen once rounded up.
        self.assertAlmostEqual(line.suggested, 3.0)
        self.assertAlmostEqual(line.qty, 3.0)
        self.assertAlmostEqual(line.price_unit, 12.0)
        self.assertAlmostEqual(line.amount, 36.0)
        self.assertFalse(line.edited)
        self.assertEqual(guide.state, "computed")
        self.assertEqual(guide.delivery_date, self.vendor._bf_retail_deliveries()[0])

    def test_the_guide_keeps_a_quantity_typed_by_hand(self):
        self.seller(self.chips, price=1.0)
        self.rule(self.chips, 10.0, 30.0)
        self.on_hand(self.chips, 5.0)
        guide = self.guide()
        guide.action_compute()
        line = guide.line_ids
        line.qty = 100.0
        self.assertTrue(line.edited)
        self.on_hand(self.chips, 25.0)        # the shelf filled in between
        guide.action_compute()
        self.assertAlmostEqual(line.qty, 100.0, msg="what a person typed is not overwritten")
        self.assertAlmostEqual(line.on_hand, 25.0, msg="the facts around it are refreshed")

    def test_the_guide_shows_the_cost_and_how_much_it_moved(self):
        self.seller(self.chips, price=1.0)
        self.rule(self.chips, 10.0, 30.0)
        self.Cost.create({"product_id": self.chips.id, "company_id": self.company.id,
                          "date": self.today - timedelta(days=7), "cost": 1.00,
                          "source": "manual"})
        self.Cost.create({"product_id": self.chips.id, "company_id": self.company.id,
                          "date": self.today, "cost": 1.20, "source": "manual"})
        guide = self.guide()
        guide.action_compute()
        line = guide.line_ids
        self.assertAlmostEqual(line.cost_last, 1.20)
        self.assertAlmostEqual(line.cost_delta_pct, 20.0, places=1)
        self.assertEqual(line.cost_date, self.today)

    def test_the_guide_counts_what_came_back(self):
        self.seller(self.chips, price=1.0)
        self.rule(self.chips, 10.0, 30.0)
        self.credit_note(self.chips, 4.0, 1.0)
        guide = self.guide()
        guide.action_compute()
        self.assertAlmostEqual(guide.line_ids.credited_qty, 4.0)

    def test_the_guide_says_when_the_order_is_below_the_minimum(self):
        self.seller(self.chips, price=1.0)
        self.rule(self.chips, 10.0, 30.0)
        guide = self.guide()
        guide.action_compute()
        guide.line_ids.qty = 5.0
        self.assertTrue(guide.below_minimum, "5.00 against a minimum of 100.00")
        guide.line_ids.qty = 150.0
        self.assertFalse(guide.below_minimum)

    def test_an_item_sold_by_two_suppliers_is_in_one_guide_only(self):
        self.seller(self.chips, partner=self.vendor, price=1.0, sequence=1)
        self.seller(self.chips, partner=self.other_vendor, price=0.9, sequence=2)
        self.rule(self.chips, 10.0, 30.0)
        first = self.guide(self.vendor)
        first.action_compute()
        second = self.guide(self.other_vendor)
        second.action_compute()
        self.assertEqual(first.line_ids.product_id, self.chips,
                         "the preferred vendor carries the item")
        self.assertFalse(second.line_ids, "and the other guide does not order it again")

    def test_the_rule_of_the_store_beats_the_preferred_vendor(self):
        self.seller(self.chips, partner=self.vendor, price=1.0, sequence=1)
        info = self.seller(self.chips, partner=self.other_vendor, price=0.9, sequence=2)
        rule = self.rule(self.chips, 10.0, 30.0)
        rule.supplier_id = info
        guide = self.guide(self.other_vendor)
        guide.action_compute()
        self.assertEqual(guide.line_ids.product_id, self.chips)

    def test_the_request_for_quotation_is_made_in_the_supplier_unit_at_local_noon(self):
        self.seller(self.chips, price=12.0, uom=self.dozen)
        self.rule(self.chips, 10.0, 30.0)
        guide = self.guide()
        guide.action_compute()
        guide.action_create_rfq()
        order = guide.order_id
        self.assertTrue(order)
        self.assertEqual(guide.state, "ordered")
        self.assertEqual(order.origin, guide.name)
        self.assertEqual(order.order_line.product_uom, self.dozen)
        self.assertAlmostEqual(order.order_line.product_qty, 3.0)
        expected = self.vendor._bf_retail_local_noon(guide.delivery_date)
        self.assertEqual(order.date_planned, expected,
                         "noon in the store's own time zone, not midnight UTC")

    def test_an_ordered_guide_does_not_order_again(self):
        self.seller(self.chips, price=1.0)
        self.rule(self.chips, 10.0, 30.0)
        guide = self.guide()
        guide.action_compute()
        guide.action_create_rfq()
        with self.assertRaises(UserError):
            guide.action_create_rfq()
        with self.assertRaises(UserError):
            guide.action_compute()

    def test_a_guide_with_nothing_to_order_makes_no_request(self):
        self.seller(self.chips, price=1.0)
        self.rule(self.chips, 0.0, 0.0)
        self.on_hand(self.chips, 50.0)
        guide = self.guide()
        guide.action_compute()
        with self.assertRaises(UserError):
            guide.action_create_rfq()

    def test_only_a_manager_fills_a_guide(self):
        """RÉÉCRIT : l'ORM refuse de toute façon la lecture du guide à un
        commis, donc l'essai d'avant passait avec ou sans la garde. Ce qui se mesure ici est
        le MESSAGE, comme le l'a fait pour la garde d'achat : il nomme le geste au lieu
        de renvoyer un refus générique sur un modèle technique."""
        clerk = new_test_user(self.env, login="clerk-supply",
                              groups="bf_retail.group_retail_user")
        guide = self.guide()
        with self.assertRaises(AccessError) as attrape:
            guide.with_user(clerk).action_compute()
        # Le message part dans la langue de l'usager : la base est en fr_CA et le catalogue
        # du module est chargé.
        self.assertRegex(str(attrape.exception),
                         r"(order guide is the retail manager|guide de commande est au gérant)")

    def test_filling_a_guide_takes_the_manager_and_ordering_takes_the_buyer(self):
        manager = new_test_user(self.env, login="manager-no-purchase",
                                groups="bf_retail.group_retail_manager,stock.group_stock_user")
        self.seller(self.chips, price=1.0)
        self.rule(self.chips, 10.0, 30.0)
        guide = self.guide()
        guide.with_user(manager).action_compute()
        self.assertEqual(guide.state, "computed", "a manager fills the guide")
        # L'ORM refuserait de toute façon la création du bon de commande : ce qui est mesuré
        # ici, c'est le MESSAGE, qui dit quoi faire au lieu de nommer un modèle technique.
        with self.assertRaises(AccessError) as attrape:
            guide.with_user(manager).action_create_rfq()
        # Le message part dans la langue de l'usager : la base de BF est en fr_CA, et le
        # catalogue du module est chargé. L'essai reconnaît les deux, et ne confond pas ce
        # message avec le refus générique de l'ORM.
        self.assertRegex(str(attrape.exception),
                         r"(give the retail manager|donner au gérant du commerce)")

    def test_a_vendor_bill_is_not_a_return(self):
        move = self.env["account.move"].create({
            "move_type": "in_invoice", "partner_id": self.vendor.id,
            "invoice_date": self.today,
            "invoice_line_ids": [Command.create({
                "product_id": self.chips.id, "quantity": 6.0, "price_unit": 1.0,
                "tax_ids": [Command.clear()]})],
        })
        move.action_post()
        self.assertFalse(self.env["bf.retail.credit"].search(
            [("product_id", "=", self.chips.id)]), "a bill is a cost, not a credit")
        self.assertTrue(self.Cost.search([("product_id", "=", self.chips.id)]),
                        "and it did write the cost series")

    def test_a_file_line_for_an_item_the_store_does_not_follow_is_not_matched(self):
        loose = self.env["product.product"].create({
            "name": "Hors du commerce", "type": "consu", "barcode": "5555555555555",
            "property_account_expense_id": self.company_data["default_account_expense"].id,
        })
        self.assertFalse(loose.bf_retail_managed)
        file = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id,
            "date": self.today, "filename": "liste.csv",
            "file": self.csv_file(["5555555555555;Hors du commerce;Unit;1,20"])})
        file.action_read()
        self.assertEqual(file.unmatched_count, 1,
                         "un produit hors du détail n'est pas apparié par un fichier de prix")

    # ── what came back ───────────────────────────────────────────────────────────────
    def test_a_credit_note_records_the_return_without_touching_the_cost(self):
        self.credit_note(self.chips, 3.0, 1.5)
        credit = self.env["bf.retail.credit"].search([("product_id", "=", self.chips.id)])
        self.assertEqual(len(credit), 1)
        self.assertAlmostEqual(credit.qty, 3.0)
        self.assertAlmostEqual(credit.amount, 4.5)
        self.assertEqual(credit.partner_id, self.vendor)
        self.assertFalse(self.Cost.search([("product_id", "=", self.chips.id)]),
                         "a credit for what did not sell is not a new cost")

    def test_a_credit_note_posted_again_does_not_double_the_return(self):
        move = self.credit_note(self.chips, 3.0, 1.5)
        move.button_draft()
        move.action_post()
        self.assertEqual(
            self.env["bf.retail.credit"].search_count([("product_id", "=", self.chips.id)]), 1)

    # ── the price file ───────────────────────────────────────────────────────────────
    def test_the_price_file_is_read_and_matched_on_the_barcode(self):
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=1.00)
        file = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id,
            "date": self.today, "filename": "liste.csv",
            "file": self.csv_file(["0123456789012;Chips 40 g;Unit;1,20",
                                   "9999999999999;Inconnu;Unit;5,00"]),
        })
        file.action_read()
        self.assertEqual(file.state, "read")
        self.assertEqual(file.line_count, 2)
        self.assertEqual(file.matched_count, 1)
        self.assertEqual(file.unmatched_count, 1)
        line = file.line_ids.filtered(lambda ligne: ligne.product_id == self.chips)
        self.assertEqual(line.matched_on, "barcode")
        self.assertAlmostEqual(line.price, 1.20, msg="1,20 read as a number")
        self.assertAlmostEqual(line.current_price, 1.00)
        self.assertAlmostEqual(line.delta_pct, 20.0, places=1)

    def test_the_price_file_opens_a_new_vendor_line_and_closes_the_old_one(self):
        self.chips.barcode = "0123456789012"
        old = self.seller(self.chips, price=1.00)
        file = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id,
            "date": self.today, "filename": "liste.csv",
            "file": self.csv_file(["0123456789012;Chips;Unit;1,20"]),
        })
        file.action_read()
        file.action_apply()
        self.assertEqual(file.state, "applied")
        self.assertEqual(old.date_end, self.today - timedelta(days=1),
                         "the price before is closed the day before, not overwritten")
        new = self.Info.search([("product_id", "=", self.chips.id),
                                ("date_start", "=", self.today)])
        self.assertEqual(len(new), 1)
        self.assertAlmostEqual(new.price, 1.20)
        self.assertEqual(new.product_uom, old.product_uom)
        self.assertEqual(new.product_code, "0123456789012")

    def test_the_same_price_file_twice_is_refused(self):
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=1.00)
        rows = ["0123456789012;Chips;Unit;1,20"]
        first = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id,
            "date": self.today, "filename": "liste.csv", "file": self.csv_file(rows)})
        first.action_read()
        first.action_apply()
        again = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id,
            "date": self.today + timedelta(days=7), "filename": "liste.csv",
            "file": self.csv_file(rows)})
        with self.assertRaises(UserError):
            again.action_read()

    def test_a_corrected_file_of_the_same_day_does_not_stack_a_second_line(self):
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=1.00)
        first = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id,
            "date": self.today, "filename": "liste.csv",
            "file": self.csv_file(["0123456789012;Chips;Unit;1,20"])})
        first.action_read()
        first.action_apply()
        second = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id,
            "date": self.today, "filename": "corrige.csv",
            "file": self.csv_file(["0123456789012;Chips;Unit;1,35"])})
        second.action_read()
        second.action_apply()
        lines = self.Info.search([("product_id", "=", self.chips.id),
                                  ("date_start", "=", self.today)])
        self.assertEqual(len(lines), 1, "one line per day, corrected in place")
        self.assertAlmostEqual(lines.price, 1.35)

    def test_a_unit_that_disagrees_with_the_vendor_line_is_left_out(self):
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=12.00, uom=self.dozen)
        file = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id,
            "date": self.today, "filename": "liste.csv",
            "file": self.csv_file(["0123456789012;Chips;Each;1,20"])})
        file.action_read()
        self.assertEqual(file.doubt_count, 1,
                         "the file says Each, the vendor line is in dozens")
        with self.assertRaises(UserError):
            file.action_apply()

    def test_the_price_file_writes_the_cost_series_when_asked(self):
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=12.00, uom=self.dozen)
        file = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id,
            "date": self.today, "filename": "liste.csv", "write_costs": True,
            "file": self.csv_file(["0123456789012;Chips;Dozens;18,00"])})
        file.action_read()
        self.assertEqual(file.matched_count, 1)
        file.action_apply()
        cost = self.Cost.search([("product_id", "=", self.chips.id)])
        self.assertEqual(len(cost), 1)
        self.assertEqual(cost.source, "file")
        self.assertAlmostEqual(cost.cost, 1.50, msg="18.00 the dozen is 1.50 the unit")

    def test_a_file_without_a_price_column_is_refused(self):
        file = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id,
            "date": self.today, "filename": "liste.csv",
            "file": base64.b64encode(b"Un;Deux\n1;2\n")})
        with self.assertRaises(UserError):
            file.action_read()

    # ── comparing two suppliers ──────────────────────────────────────────────────────
    def test_the_cheapest_price_per_unit_is_flagged(self):
        # L'unité d'achat est celle du produit : les deux fournisseurs chiffrent la douzaine,
        # et ce qui se lit est le prix à l'unité de l'article.
        cheap = self.seller(self.chips, partner=self.vendor, price=12.00, uom=self.dozen)
        dear = self.seller(self.chips, partner=self.other_vendor, price=13.20)
        self.assertEqual(cheap.product_uom, self.dozen)
        self.assertAlmostEqual(cheap.bf_retail_price_per_unit, 1.00)
        self.assertAlmostEqual(dear.bf_retail_price_per_unit, 1.10)
        self.assertTrue(cheap.bf_retail_best, "12.00 la douzaine bat 13,20 la douzaine")
        self.assertFalse(dear.bf_retail_best)

    def test_a_purchase_unit_of_another_category_is_refused_by_odoo_itself(self):
        """Le garde de catégorie du calcul reste par prudence, mais Odoo rend la situation
        impossible : l'unité d'achat doit être de la même catégorie que celle du stock."""
        with self.assertRaises(ValidationError):
            self.chips.product_tmpl_id.uom_po_id = self.kg

    # ── les constats du tour 1 de la relecture adverse ────────────────────────
    # Un essai par constat, qui tombe avant son correctif et passe après.

    # 1. une étiquette de caisse qui NOMME l'unité ne passe pas pour l'unité.
    def test_a_case_label_that_names_the_unit_still_raises_the_doubt(self):
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=1.00)          # ligne fournisseur à l'unité
        file = self.price_file(["0123456789012;Chips;CASE OF 12 UNITS;18,00"],
                               write_costs=True)
        file.action_read()
        self.assertEqual(file.doubt_count, 1,
                         "« CASE OF 12 UNITS » annonce un contenant, pas une unité")
        self.assertEqual(file.matched_count, 0)
        with self.assertRaises(UserError):
            file.action_apply()
        self.assertFalse(self.Info.search([("product_id", "=", self.chips.id),
                                           ("date_start", "=", self.today)]),
                         "18,00 la caisse ne devient pas 18,00 l'unité")
        self.assertFalse(self.Cost.search([("product_id", "=", self.chips.id)]),
                         "et rien n'entre dans la série des coûts du lot 1")

    # 6. « EA », « Each », « Un » : les étiquettes courantes du métier s'accordent.
    def test_the_common_each_labels_agree_with_a_vendor_line_in_units(self):
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=1.00)
        for etiquette in ("EA", "Each", "EACH", "Un", "Unité", "UNITES", "1 EA", "PC",
                          "chaque", "pièce"):
            with self.subTest(etiquette=etiquette):
                file = self.price_file(
                    ["0123456789012;Chips;%s;1,20" % etiquette])
                file.action_read()
                self.assertEqual(file.doubt_count, 0, etiquette)
                self.assertEqual(file.matched_count, 1, etiquette)

    def test_a_unit_label_nobody_recognises_is_a_doubt_not_a_pass(self):
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=1.00)
        for etiquette in ("CS", "Caisse de 12", "Tray", "Plateau", "ZZZ", "12", "1 KG"):
            with self.subTest(etiquette=etiquette):
                file = self.price_file(["0123456789012;Chips;%s;18,00" % etiquette])
                file.action_read()
                self.assertEqual(file.doubt_count, 1, etiquette)

    # 7. le doute ne dépend pas de la langue de la session (uom.name est traduisible).
    def test_the_unit_doubt_does_not_depend_on_the_language_of_the_session(self):
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=12.00, uom=self.dozen)
        mesures = {}
        for lang in ("en_US", "fr_CA"):
            file = self.price_file(["0123456789012;Chips;Doz;13,20"])
            file.with_context(lang=lang).action_read()
            mesures[lang] = (file.doubt_count, file.matched_count)
        self.assertEqual(mesures["fr_CA"], mesures["en_US"],
                         "le même fichier ne peut pas être accepté dans une langue et "
                         "écarté dans l'autre")
        self.assertEqual(mesures["fr_CA"], (0, 1),
                         "« Doz » contre une ligne fournisseur à la douzaine")

    # 2. appliquer un fichier de prix ne touche QUE le prix et les dates.
    def test_applying_a_price_file_keeps_everything_but_the_price_and_the_dates(self):
        euro = self.env.ref("base.EUR")
        euro.active = True
        self.chips.barcode = "0123456789012"
        old = self.seller(self.chips, price=1.00, min_qty=24.0, delay=5, sequence=7,
                          currency_id=euro.id, product_name="CHIPS SEL 40 G",
                          product_code="ABC-1", discount=2.0)
        file = self.price_file(["0123456789012;Chips;Unit;1,20"])
        file.action_read()
        file.action_apply()
        new = self.Info.search([("product_id", "=", self.chips.id),
                                ("date_start", "=", self.today)])
        self.assertEqual(len(new), 1)
        self.assertAlmostEqual(new.price, 1.20, msg="seul le prix bouge")
        self.assertAlmostEqual(new.min_qty, 24.0,
                               msg="le minimum de la caisse du grossiste, seule forme qui "
                                   "reste de son format")
        self.assertEqual(new.delay, 5, "le délai de livraison du fournisseur")
        self.assertEqual(new.sequence, 7,
                         "la séquence décide DANS QUEL guide l'article tombe")
        self.assertEqual(new.currency_id, euro, "un prix coté en euros reste en euros")
        self.assertEqual(new.product_name, "CHIPS SEL 40 G")
        self.assertEqual(new.product_code, "ABC-1", "le code du fournisseur n'est pas perdu")
        self.assertAlmostEqual(new.discount, 2.0)
        self.assertEqual(new.date_start, self.today)
        self.assertFalse(new.date_end)
        self.assertEqual(old.date_end, self.today - timedelta(days=1))

    def test_a_price_file_on_an_item_without_a_vendor_line_still_opens_one(self):
        self.chips.barcode = "0123456789012"
        file = self.price_file(["0123456789012;Chips;Unit;1,20"])
        file.action_read()
        file.action_apply()
        new = self.Info.search([("product_id", "=", self.chips.id),
                                ("date_start", "=", self.today)])
        self.assertEqual(len(new), 1)
        self.assertAlmostEqual(new.price, 1.20)
        self.assertEqual(new.partner_id, self.vendor)
        self.assertEqual(new.product_code, "0123456789012",
                         "sans ligne d'avant, le code du fichier sert de code fournisseur")

    # 3. le lecteur de nombres refuse ce qui n'est pas UN nombre.
    def test_the_number_reader_refuses_what_is_not_one_number(self):
        for texte, attendu in (("1,20", 1.20), ("1.20", 1.20), ("1 234,56", 1234.56),
                               ("$1,234.56", 1234.56), ("12.50 CAD", 12.50),
                               ("1,20 $", 1.20), (" 18,00 ", 18.00), ("18", 18.0),
                               ("-1,20", -1.20), ("1.2000000000000002", 1.2),
                               ("2 pour 3,00", None), ("12 x 1,20", None), ("1 20", None),
                               ("(3,50)", None), ("sur demande", None), ("", None),
                               ("1.2E+09", None), ("2026-11-12", None), ("n/d", None)):
            with self.subTest(texte=texte):
                lu = self.File._bf_nombre(texte)
                if attendu is None:
                    self.assertIsNone(lu, "%r n'est pas un prix" % texte)
                else:
                    self.assertAlmostEqual(lu, attendu, places=3, msg=repr(texte))

    def test_a_price_of_zero_does_not_close_the_price_that_was_there(self):
        self.chips.barcode = "0123456789012"
        old = self.seller(self.chips, price=1.00)
        file = self.price_file(["0123456789012;Chips;Unit;0,00"])
        file.action_read()
        self.assertEqual(file.matched_count, 0, "0,00 n'est pas un prix de grossiste")
        with self.assertRaises(UserError):
            file.action_apply()
        self.assertFalse(old.date_end, "le vrai prix reste ouvert")
        self.assertEqual(file.line_ids.rejected, "zero")

    def test_a_negative_price_is_left_out_instead_of_breaking_the_cost_series(self):
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=1.00)
        file = self.price_file(["0123456789012;Chips;Unit;-1,20"], write_costs=True)
        file.action_read()
        self.assertEqual(file.matched_count, 0)
        with self.assertRaises(UserError):
            file.action_apply()
        self.assertFalse(self.Cost.search([("product_id", "=", self.chips.id)]),
                         "aucune IntegrityError brute au milieu de la pose")

    # 4. une ligne que le lecteur ne peut pas lire est comptée, gardée et nommée.
    def test_a_row_the_reader_cannot_read_is_counted_and_named(self):
        self.chips.barcode = "0123456789012"
        self.milk.barcode = "0123456789013"
        self.seller(self.chips, price=1.00)
        file = self.price_file(["0123456789012;Chips;Unit;1,20",
                                "0123456789013;Lait;Unit;sur demande",
                                ";Pain sans code;Unit;3,60"])
        file.action_read()
        self.assertEqual(file.line_count, 3, "les trois lignes du fichier sont là")
        self.assertEqual(file.matched_count, 1, "et le prix lisible s'applique quand même")
        self.assertEqual(file.rejected_count, 2)
        self.assertEqual(set(file.line_ids.mapped("rejected")) - {False},
                         {"price", "code"})
        corps = " ".join(file.message_ids.mapped("body"))
        self.assertIn("0123456789013", corps, "la ligne jetée est NOMMÉE au chatter")
        file.action_apply()
        self.assertEqual(len(self.Info.search([("product_id", "=", self.chips.id),
                                               ("date_start", "=", self.today)])), 1)
        self.assertIn("0123456789013", " ".join(file.message_ids.mapped("body")))

    def test_the_report_of_a_price_file_breaks_its_lines_instead_of_showing_the_tag(self):
        """18ᵉ constat : `_bf_rapport` joignait ses morceaux avec un `"<br/>"` en TEXTE, et les
        deux portes du chatter échappent le corps — le gérant lisait « … laissée(s) de côté.
        <br/>Colonnes lues : … » avec la balise en clair, au fil de son fichier de prix.

        Mesuré sur le corps TEL QU'IL EST EN BASE, et non sur ce que rend `_bf_rapport` : c'est
        l'échappement de la porte qui était le défaut, pas la jointure prise seule."""
        self.chips.barcode = "0123456789012"
        self.milk.barcode = "0123456789013"
        self.seller(self.chips, price=1.00)
        file = self.price_file(["0123456789012;Chips;Unit;1,20",
                                "0123456789013;Lait;Unit;sur demande"])
        file.action_read()
        corps = " ".join(file.message_ids.mapped("body"))
        self.assertNotIn("&lt;br/&gt;", corps,
                         "la balise de saut de ligne ne se lit pas en clair au chatter")
        # ⚠️ `<br` et non `<br/>` : le champ HTML du chatter NORMALISE la balise, et le corps
        # garde `<br>`. Un essai mesure ce que le champ GARDE, pas ce que le code a écrit.
        self.assertIn("<br", corps, "elle sépare vraiment les paragraphes du rapport")
        # Et l'échappement du RESTE tient : un nom de fournisseur qui porte un `&` reste
        # échappé, sinon `Markup` aurait ouvert une porte à du HTML venu d'un fichier.
        self.vendor.name = "Grossiste A & B <b>gras</b>"
        file2 = self.price_file(["0123456789012;Chips;Unit;1,30"])
        file2.action_read()
        file2.action_apply()
        corps2 = " ".join(file2.message_ids.mapped("body"))
        self.assertNotIn("<b>gras</b>", corps2,
                         "rien de ce qui vient du dehors ne passe en HTML")

    # 5. XLSX : la feuille lue est celle qui porte une entête, et elle est nommée.
    def test_an_excel_price_file_reads_the_sheet_that_carries_the_header(self):
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=1.00)
        file = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id,
            "date": self.today, "filename": "liste.xlsx",
            "file": self.xlsx_file([
                ("Lisez-moi", [["Liste du grossiste ABC"], ["En vigueur le 12 novembre"]]),
                ("Prix", [["Barcode", "Description", "Format", "Price"],
                          ["0123456789012", "Chips 40 g", "Unit", 1.2]])])})
        file.action_read()
        self.assertEqual(file.line_count, 1)
        self.assertEqual(file.matched_count, 1)
        self.assertAlmostEqual(file.line_ids.price, 1.20)
        self.assertIn("Prix", " ".join(file.message_ids.mapped("body")),
                      "le chatter nomme la feuille lue")

    def test_an_excel_cell_the_reader_cannot_read_is_named_not_dropped(self):
        self.chips.barcode = "0123456789012"
        self.milk.barcode = "0123456789013"
        self.seller(self.chips, price=1.00)
        file = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id,
            "date": self.today, "filename": "liste.xlsx",
            "file": self.xlsx_file([
                ("Prix", [["Barcode", "Description", "Format", "Price"],
                          ["0123456789012", "Chips", "Unit", 1.2],
                          ["0123456789013", "Lait", "Unit", "=ROUND(1.5*0.9,2)"],
                          ["0123456789014", "Pain", "Unit", date(2026, 11, 12)]])])})
        file.action_read()
        self.assertEqual(file.line_count, 3)
        self.assertEqual(file.matched_count, 1)
        self.assertEqual(file.rejected_count, 2,
                         "une formule sans valeur en cache et une date ne sont pas des prix")
        corps = " ".join(file.message_ids.mapped("body"))
        self.assertIn("0123456789013", corps)
        self.assertIn("0123456789014", corps)

    def test_an_excel_barcode_is_not_read_as_a_number_with_a_decimal(self):
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=1.00)
        file = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id,
            "date": self.today, "filename": "liste.xlsx",
            "file": self.xlsx_file([
                ("Prix", [["Barcode", "Description", "Format", "Price"],
                          [123456789012, "Chips", "Unit", 1.2]])])})
        file.action_read()
        self.assertEqual(file.line_ids.code, "123456789012",
                         "pas « 123456789012.0 » : un code-barres n'est pas un flottant")
        self.assertEqual(file.matched_count, 1, "et le zéro de tête mangé est retrouvé")

    # 9. un petit fichier, une ligne courte, une ligne de titre : lu, pas refusé.
    def test_a_small_file_with_a_ragged_row_or_a_title_is_still_read(self):
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=1.00)
        courte = self.price_file(["0123456789012;Chips;1,20",
                                  "0123456789013;Lait",          # la dernière cellule manque
                                  "0123456789014;Pain;3,60"],
                                 header="Code;Description;Prix")
        courte.action_read()
        self.assertEqual(courte.matched_count, 1,
                         "une cellule finale manquante ne fait pas refuser le fichier")
        self.assertEqual(courte.line_count, 3)
        titre = self.price_file(["Code;Description;Prix",
                                 "0123456789012;Chips;1,20"],
                                header="Liste de prix du grossiste ABC")
        titre.action_read()
        self.assertEqual(titre.matched_count, 1, "une ligne de titre au-dessus de l'entête")

    # 10. un seul fuseau pour une seule décision.
    def test_the_guide_and_the_supplier_read_the_same_time_zone(self):
        adresse = self.env["res.partner"].create({
            "name": "Magasin de Takapuna", "tz": "Pacific/Auckland"})
        self.warehouse.partner_id = adresse
        self.company.partner_id.tz = False
        self.env.user.tz = "America/Toronto"
        self.seller(self.chips, price=1.0)
        self.rule(self.chips, 10.0, 30.0)
        guide = self.guide()
        self.assertEqual(str(guide._bf_tz()), "Pacific/Auckland",
                         "le fuseau du commerce est celui de son adresse")
        self.assertEqual(str(guide._bf_tz()),
                         str(self.vendor._bf_retail_tz(self.warehouse)),
                         "le guide et le fournisseur répondent la même chose")
        # L'horloge est figée sur un instant où les deux fuseaux ne sont pas le même
        # JOUR : 2026-11-12 01:00 UTC, c'est jeudi 14 h à Auckland et mercredi 20 h à
        # Toronto. Sans cela les deux résolutions tombent souvent sur la même date, et rien
        # ne dit LAQUELLE a répondu.
        with freeze_time("2026-11-12 01:00:00"):
            self.assertEqual(
                self.vendor._bf_retail_deliveries(warehouse=self.warehouse),
                (date(2026, 11, 17), date(2026, 11, 20)),
                "jeudi 14 h à Auckland : la coupure de 14 h est passée, le vendredi n'est "
                "plus joignable, et la livraison d'après est le mardi")
            self.assertEqual(
                self.vendor._bf_retail_deliveries(),
                (date(2026, 11, 13), date(2026, 11, 17)),
                "sans l'entrepôt, le même fournisseur répond le JOUR D'AVANT : c'est cette "
                "réponse-là qu'une seule résolution interdit de servir au guide")
            guide.action_compute()
            guide.action_create_rfq()
        self.assertEqual(guide.delivery_date, date(2026, 11, 17),
                         "le guide commande pour le jour lu dans le fuseau du magasin")
        self.assertEqual(guide.cover_until, date(2026, 11, 20),
                         "et couvre jusqu'à la livraison d'après, lue dans le même fuseau")
        # La date attendue est POSÉE, pas recalculée depuis la réponse qu'on mesure.
        attendu = pytz.timezone("Pacific/Auckland").localize(
            datetime.combine(date(2026, 11, 17), time(12, 0))).astimezone(
                pytz.utc).replace(tzinfo=None)
        self.assertEqual(attendu, datetime(2026, 11, 16, 23, 0),
                         "midi à Auckland le 17 novembre, c'est 23 h UTC le 16")
        self.assertEqual(guide.order_id.date_planned, attendu,
                         "midi dans le fuseau du commerce, pas dans celui de l'usager")

    # 11. une quantité saisie à la main reste saisie à la main.
    def test_a_quantity_typed_by_hand_survives_the_suggestion_catching_up(self):
        self.seller(self.chips, price=1.0)
        self.rule(self.chips, 11.0, 30.0)
        self.on_hand(self.chips, 5.0)
        guide = self.guide()
        guide.action_compute()
        line = guide.line_ids
        self.assertAlmostEqual(line.suggested, 25.0)
        line.qty = 20.0
        self.assertTrue(line.edited)
        self.on_hand(self.chips, 10.0)       # le proposé devient 20, par hasard
        guide.action_compute()
        self.assertAlmostEqual(line.suggested, 20.0)
        self.assertTrue(line.edited, "le repère ne se perd pas quand le proposé rattrape")
        self.on_hand(self.chips, 25.0)       # la tablette se remplit
        guide.action_compute()
        self.assertAlmostEqual(line.qty, 20.0,
                               msg="les 20 décidées par une personne restent 20")

    # 12. une colonne « Nom » ne vole pas le rôle du code.
    def test_a_name_column_does_not_steal_the_code_role(self):
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=1.00)
        file = self.price_file(["Chips;0123456789012;1,20"], header="Nom;Code;Prix")
        file.action_read()
        self.assertEqual(file.line_ids.code, "0123456789012")
        self.assertEqual(file.matched_count, 1)
        colonnes = self.File._bf_colonnes(["Numéro", "Note", "Nouveau prix", "Format"])
        self.assertEqual(colonnes.get("code"), 0, "« Numéro » est un code")
        self.assertEqual(colonnes.get("price"), 2, "« Nouveau prix » est un prix")
        self.assertEqual(colonnes.get("uom"), 3)
        self.assertNotIn("name", colonnes, "« Note » n'est pas une description")
        self.assertEqual(self.File._bf_colonnes(["Item Code", "Item Description",
                                                 "Unit Price"]),
                         {"code": 0, "name": 1, "price": 2})
        # Une entête d'un seul mot est reconnue entière, avant la lecture mot par mot : le
        # chemin des mots ne se mesure que sur une entête COMPOSÉE, celle d'un grossiste qui
        # écrit « Nom du produit » plutôt que « Nom ».
        compose = self.File._bf_colonnes(["Nom du produit", "Code", "Prix"])
        self.assertEqual(compose, {"name": 0, "code": 1, "price": 2},
                         "« Nom du produit » est une description, pas un code qui commence "
                         "par « no »")
        self.assertEqual(
            self.File._bf_colonnes(["Description du produit", "Code du produit",
                                    "Nouveau prix net", "Format de vente"]),
            {"name": 0, "code": 1, "price": 2, "uom": 3})
        lu = self.price_file(["Chips;0123456789012;1,20"],
                             header="Nom du produit;Code;Prix")
        lu.action_read()
        self.assertEqual(lu.line_ids.code, "0123456789012",
                         "la colonne du code est celle du code, pas celle des noms")
        self.assertEqual(lu.matched_count, 1)

    # 13. deux lignes pour le même article : jamais la dernière qui gagne en silence.
    def test_the_same_item_twice_at_two_prices_is_named_not_silently_overwritten(self):
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=1.00)
        file = self.price_file(["0123456789012;Chips;Unit;1,20",
                                "0123456789012;Chips;Unit;9,99"])
        file.action_read()
        self.assertEqual(file.matched_count, 0, "le prix est ambigu : aucun ne s'applique")
        self.assertEqual(file.rejected_count, 2)
        with self.assertRaises(UserError):
            file.action_apply()
        deux_fois = self.price_file(["0123456789012;Chips;Unit;1,20",
                                     "0123456789012;Chips;Unit;1,20"])
        deux_fois.action_read()
        self.assertEqual(deux_fois.matched_count, 1,
                         "le même prix deux fois n'est pas ambigu")
        deux_fois.action_apply()
        posees = self.Info.search([("product_id", "=", self.chips.id),
                                   ("date_start", "=", self.today)])
        self.assertEqual(len(posees), 1, "un seul prix posé pour un seul article")

    # 14. le refus du jumeau ne se contourne pas par l'ordre des gestes.
    def test_the_same_file_applied_twice_is_refused_even_when_both_were_read_first(self):
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=1.00)
        rows = ["0123456789012;Chips;Unit;1,20"]
        first = self.price_file(rows)
        again = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id,
            "date": self.today + timedelta(days=7), "filename": "liste.csv",
            "file": self.csv_file(rows)})
        first.action_read()
        again.action_read()          # les deux lus avant qu'aucun ne soit appliqué
        first.action_apply()
        with self.assertRaises(UserError):
            again.action_apply()

    # 15. une liste future n'est pas masquée par le prix d'aujourd'hui.
    def test_a_price_file_does_not_hide_a_vendor_line_that_starts_later(self):
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=1.00)
        futur = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id,
            "date": self.today + timedelta(days=14), "filename": "futur.csv",
            "file": self.csv_file(["0123456789012;Chips;Unit;3,00"])})
        futur.action_read()
        futur.action_apply()
        courant = self.price_file(["0123456789012;Chips;Unit;2,00"])
        courant.action_read()
        courant.action_apply()
        ouvertes = self.Info.search([("product_id", "=", self.chips.id),
                                     ("date_end", "=", False)])
        self.assertEqual(len(ouvertes), 1, "un seul prix ouvert, pas deux qui se chevauchent")
        self.assertEqual(ouvertes.date_start, self.today + timedelta(days=14))
        neuve = self.Info.search([("product_id", "=", self.chips.id),
                                  ("date_start", "=", self.today)])
        self.assertEqual(neuve.date_end, self.today + timedelta(days=13),
                         "le prix d'aujourd'hui se ferme la veille de la liste future")
        choisi = self.chips._select_seller(
            partner_id=self.vendor, date=self.today + timedelta(days=20))
        self.assertAlmostEqual(choisi.price, 3.00, msg="dans 20 jours, la liste future")

    # 16. un crédit annulé ne compte plus dans « ce qui est revenu ».
    def test_a_cancelled_credit_note_stops_counting_as_a_return(self):
        self.seller(self.chips, price=1.0)
        self.rule(self.chips, 10.0, 30.0)
        move = self.credit_note(self.chips, 4.0, 1.0)
        Credit = self.env["bf.retail.credit"]
        self.assertEqual(Credit.search_count([("product_id", "=", self.chips.id)]), 1)
        move.button_draft()
        move.button_cancel()
        self.assertEqual(move.state, "cancel")
        self.assertEqual(Credit.search_count([("product_id", "=", self.chips.id)]), 0,
                         "une note de crédit annulée n'est pas un retour")
        guide = self.guide()
        guide.action_compute()
        self.assertAlmostEqual(guide.line_ids.credited_qty, 0.0)

    # 17. la ligne du guide nomme ses deux unités.
    def test_the_guide_line_names_both_the_ordered_unit_and_the_stocked_unit(self):
        self.seller(self.chips, price=12.00, uom=self.dozen)
        self.rule(self.chips, 10.0, 30.0)
        # Écrit à l'unité, et dit comme tel : sans `product_uom_id`, une facture d'achat
        # prend l'unité d'ACHAT de l'article, et 24 deviendraient 24 douzaines.
        self.env["account.move"].create({
            "move_type": "in_refund", "partner_id": self.vendor.id,
            "invoice_date": self.today,
            "invoice_line_ids": [Command.create({
                "product_id": self.chips.id, "quantity": 24.0, "price_unit": 1.0,
                "product_uom_id": self.unit.id, "tax_ids": [Command.clear()]})],
        }).action_post()
        guide = self.guide()
        guide.action_compute()
        line = guide.line_ids
        self.assertEqual(line.uom_id, self.dozen, "commandé à la douzaine")
        self.assertEqual(line.stock_uom_id, self.unit,
                         "en main, prévu et revenu se comptent à l'unité de l'article")
        self.assertAlmostEqual(line.credited_qty, 24.0)
        arch = self.env.ref("bf_retail_supply.bf_retail_order_guide_view_form").arch
        self.assertNotIn("uom.group_uom", arch,
                         "l'unité de la commande se lit sans le groupe des unités")
        self.assertIn('name="stock_uom_id"', arch)

    # 19. les trois chemins d'appariement du lecteur.
    def test_a_price_file_matches_on_the_internal_reference(self):
        self.chips.default_code = "CH-40G"
        self.seller(self.chips, price=1.00)
        file = self.price_file(["CH-40G;Chips;Unit;1,20"])
        file.action_read()
        self.assertEqual(file.matched_count, 1)
        self.assertEqual(file.line_ids.matched_on, "default_code")
        self.assertEqual(file.line_ids.product_id, self.chips)

    def test_a_price_file_matches_on_the_code_the_supplier_uses(self):
        self.seller(self.chips, price=1.00, product_code="GR-9981")
        file = self.price_file(["GR-9981;Chips;Unit;1,20"])
        file.action_read()
        self.assertEqual(file.matched_count, 1)
        self.assertEqual(file.line_ids.matched_on, "supplier_code")
        self.assertEqual(file.line_ids.product_id, self.chips)

    # 20. le lecteur se choisit sur le contenu, et un fichier démesuré est refusé.
    def test_the_reader_is_chosen_on_the_content_not_on_the_file_name(self):
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=1.00)
        classeur = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id,
            "date": self.today, "filename": "liste-du-grossiste.csv",
            "file": self.xlsx_file([
                ("Prix", [["Barcode", "Description", "Format", "Price"],
                          ["0123456789012", "Chips", "Unit", 1.2]])])})
        classeur.action_read()
        self.assertEqual(classeur.matched_count, 1,
                         "un classeur Excel nommé .csv se lit comme un classeur")
        vieux = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id,
            "date": self.today, "filename": "liste.csv",
            "file": base64.b64encode(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)})
        with self.assertRaises(UserError) as attrape:
            vieux.action_read()
        self.assertRegex(str(attrape.exception), r"(\.xls|Excel)")

    def test_a_file_far_too_long_to_be_a_price_list_is_refused_by_name(self):
        rows = ["012345678901%d;Article;Unit;1,20" % (index % 10)
                for index in range(20001)]
        file = self.price_file(rows)
        with self.assertRaises(UserError) as attrape:
            file.action_read()
        self.assertIn("20000", str(attrape.exception))
        self.assertFalse(file.line_ids)

    # 21. un code-barres amputé de son zéro de tête est retrouvé.
    def test_a_barcode_that_lost_its_leading_zero_is_still_matched(self):
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=1.00)
        file = self.price_file(["123456789012;Chips;Unit;1,20"])
        file.action_read()
        self.assertEqual(file.matched_count, 1,
                         "Excel mange le zéro de tête ; un GTIN a une longueur fixe")
        self.assertEqual(file.line_ids.matched_on, "barcode")
        self.assertEqual(file.line_ids.product_id, self.chips)

    # 23. un guide fermé ne se fait pas dire qu'il est déjà commandé.
    def test_a_closed_guide_is_told_it_is_closed_not_that_it_was_ordered(self):
        guide = self.guide()
        guide.action_close()
        with self.assertRaises(UserError) as attrape:
            guide.action_compute()
        self.assertRegex(str(attrape.exception), r"(is closed|est fermé)")
        self.assertNotRegex(str(attrape.exception), r"(already ordered|déjà commandé)")

    # 25. l'heure de coupure est une heure du jour.
    def test_the_cutoff_hour_is_an_hour_of_the_day(self):
        with self.assertRaises(ValidationError):
            self.vendor.bf_retail_order_cutoff = -1.0
        with self.assertRaises(ValidationError):
            self.vendor.bf_retail_order_cutoff = 25.0
        self.vendor.bf_retail_order_cutoff = 23.75
        self.assertAlmostEqual(self.vendor.bf_retail_order_cutoff, 23.75)

    # 22. la promesse retirée du « format par fournisseur » ne traîne plus.
    def test_no_trace_is_left_of_the_retired_per_supplier_format_promise(self):
        import pathlib

        from odoo.modules.module import get_module_path
        racine = pathlib.Path(get_module_path("bf_retail_supply"))
        # L'aiguille est composée : écrite d'un bloc, cet essai la trouverait dans son
        # propre fichier et tomberait sur lui-même.
        aiguille = "supplier" + "'s unit"
        for nom in ("__manifest__.py", "README.md", "models/order_guide.py",
                    "tests/test_supply.py", "models/product_supplierinfo.py",
                    "models/price_file.py", "views/supplierinfo_views.xml",
                    "views/order_guide_views.xml", "i18n/fr_CA.po"):
            texte = (racine / nom).read_text(encoding="utf-8")
            self.assertNotIn(aiguille, texte,
                             "%s promet encore une unité par fournisseur" % nom)

    # ── les survivants de la passe de mutation, rendus mesurés ────────────────
    # Un essai par survivant : il passe sur l'arbre propre, et une assertion tombe sous la
    # mutation qui abîme la décision.

    # Mutant 24 : la règle du COMPTE, qui n'avait aucun essai à elle. « CASE OF 12 UNITS »
    # lève déjà le doute par la règle du contenant (« case » met `pack` dans les familles de
    # l'étiquette) ; une étiquette qui COMPTE sans nommer de contenant n'était mesurée nulle
    # part, et sans la règle du compte elle s'applique au prix de la caisse.
    def test_a_label_that_counts_without_naming_a_container_raises_the_doubt(self):
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=1.00)          # ligne fournisseur à l'unité
        file = self.price_file(["0123456789012;Chips;12 UNITS;18,00"], write_costs=True)
        file.action_read()
        self.assertEqual(file.doubt_count, 1,
                         "« 12 UNITS » compte douze unités : c'est un contenant, et aucune "
                         "étiquette ne nomme ici de caisse")
        self.assertEqual(file.matched_count, 0)
        with self.assertRaises(UserError):
            file.action_apply()
        self.assertFalse(self.Info.search([("product_id", "=", self.chips.id),
                                           ("date_start", "=", self.today)]),
                         "18,00 les douze ne devient pas 18,00 l'unité")
        self.assertFalse(self.Cost.search([("product_id", "=", self.chips.id)]),
                         "et rien n'entre dans la série des coûts du lot 1")
        # La même règle sur une autre famille que l'unité : « 12 x 1 L » contre une ligne
        # fournisseur au litre. Le compte annonce le contenant, le litre ne gagne pas.
        litre = self.env.ref("uom.product_uom_litre")
        jus = self.make_product("Jus 1 L", price=3.49, barcode="0123456789013",
                                uom_id=litre.id, uom_po_id=litre.id)
        self.seller(jus, price=1.20)
        douze = self.price_file(["0123456789013;Jus;12 x 1 L;18,00"])
        douze.action_read()
        self.assertEqual(douze.doubt_count, 1,
                         "« 12 x 1 L » contre une ligne fournisseur au litre : un contenant")
        self.assertEqual(douze.matched_count, 0)

    # Mutant 26 : la branche « deux étiquettes que personne ne reconnaît » est la SEULE que
    # la mutation touche, et une unité semée n'y passe jamais (`_bf_famille_uom` la reconnaît
    # à son identifiant externe). Il faut donc une unité hors des unités semées, dont le nom
    # est traduit, lue dans une session en français.
    def test_an_unknown_unit_is_read_on_the_source_of_its_name_not_on_its_translation(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        famille = self.env["uom.category"].sudo().create({"name": "Blivets"})
        blivet = self.env["uom.uom"].sudo().with_context(lang="en_US").create({
            "name": "Blivet", "category_id": famille.id, "uom_type": "reference",
            "factor": 1.0, "rounding": 0.01})
        # `blivet.with_context(lang="fr_CA").name = ...` réécrirait AUSSI la source : tant
        # qu'une unité n'a aucune traduction, Odoo écrit la valeur dans toutes les langues.
        blivet.update_field_translations("name", {"fr_CA": "Bidule"})
        self.assertEqual(blivet.with_context(lang="en_US").name, "Blivet",
                         "la source du nom reste l'anglais")
        self.assertEqual(blivet.with_context(lang="fr_CA").name, "Bidule",
                         "et le nom EST traduit : c'est ce qui rendait le doute bavard")
        gadget = self.make_product("Gadget", price=9.99, barcode="0123456789014",
                                   uom_id=blivet.id, uom_po_id=blivet.id)
        self.seller(gadget, price=1.00)
        mesures = {}
        for lang in ("en_US", "fr_CA"):
            source = self.price_file(["0123456789014;Gadget;Blivet;1,20"])
            source.with_context(lang=lang).action_read()
            traduit = self.price_file(["0123456789014;Gadget;Bidule;1,20"])
            traduit.with_context(lang=lang).action_read()
            mesures[lang] = ((source.doubt_count, source.matched_count),
                             (traduit.doubt_count, traduit.matched_count))
        self.assertEqual(mesures["fr_CA"], mesures["en_US"],
                         "une unité que personne ne reconnaît se lit de la même façon dans "
                         "les deux langues")
        self.assertEqual(mesures["fr_CA"][0], (0, 1),
                         "l'étiquette écrite comme la SOURCE du nom s'accorde, même dans "
                         "une session en français")
        self.assertEqual(mesures["fr_CA"][1], (1, 0),
                         "et l'étiquette écrite comme la traduction lève le doute : le "
                         "fichier du grossiste ne parle pas la langue de l'usager")
        # La décision, mesurée sur la branche elle-même, avec une unité LUE en français :
        # le doute se tranche sur la source du nom, pas sur ce que l'usager voit à l'écran.
        familles = self.env["bf.retail.price.file.line"]._bf_familles_uom()
        fr = blivet.with_context(lang="fr_CA")
        self.assertEqual(fr.name, "Bidule", "l'unité est bien LUE en français ici")
        self.assertNotIn(blivet.id, familles,
                         "une unité maison n'est pas une unité semée : c'est son NOM qui "
                         "la départage, et c'est la branche que personne ne mesurait")
        ecrit_source = self.price_file(["0123456789014;Gadget;Blivet;1,20"])
        ecrit_source.action_read()
        self.assertFalse(
            ecrit_source.line_ids.with_context(lang="fr_CA")._bf_doute_unite(fr, familles),
            "« Blivet » EST le nom de l'unité, même quand la session affiche « Bidule »")
        ecrit_traduit = self.price_file(["0123456789014;Gadget;Bidule;1,20"])
        ecrit_traduit.action_read()
        self.assertTrue(
            ecrit_traduit.line_ids.with_context(lang="fr_CA")._bf_doute_unite(fr, familles),
            "« Bidule » n'est pas le nom de l'unité, c'est sa traduction : doute")

    # Mutant 14 : la garde `line.id in already`. Un repostage passe forcément par le
    # brouillon, qui efface les crédits (`_bf_retail_drop_credits`), donc `already` y est
    # vide et la garde n'y mord pas. Le chemin où elle mord est une SECONDE écriture sans
    # retour au brouillon, et c'est elle qui se mesure ici.
    def test_writing_the_credits_twice_does_not_double_the_return(self):
        Credit = self.env["bf.retail.credit"]
        move = self.credit_note(self.chips, 3.0, 1.5)
        self.assertEqual(Credit.search_count([("product_id", "=", self.chips.id)]), 1)
        move._bf_retail_write_credits()
        self.assertEqual(Credit.search_count([("product_id", "=", self.chips.id)]), 1,
                         "une ligne de note de crédit déjà consignée ne se consigne pas "
                         "une seconde fois")
        self.assertEqual(Credit.search([("move_line_id", "in", move.line_ids.ids)]).qty,
                         3.0, "et les 3 unités revenues restent 3")

    # ── les constats du tour 2, un essai par constat ──────────────────────────
    # Chacun TOMBE sur l'arbre d'avant son correctif et passe après.

    # D1 (a) et (c) : une colonne de prix de détail ne vole plus le rôle du prix d'achat,
    # et le rapport NOMME la colonne retenue et le séparateur.
    def test_a_retail_price_column_never_takes_the_role_of_the_purchase_price(self):
        self.chips.barcode = "0123456789012"
        old = self.seller(self.chips, price=4.00)
        file = self.price_file(["0123456789012;Chips;6,49;4,25"],
                               header="Code;Description;Prix de détail;Prix net")
        file.action_read()
        self.assertEqual(file.matched_count, 1)
        self.assertAlmostEqual(file.line_ids.price, 4.25,
                               msg="le prix d'achat est le prix net, pas le prix de tablette")
        # (c) la colonne retenue et le séparateur sont NOMMÉS : la seule information qui
        # rendait le défaut visible à l'écran.
        self.assertIn("Prix net", file.columns_note or "")
        self.assertNotIn("Prix de détail", file.columns_note or "")
        self.assertIn(";", file.columns_note or "")
        self.assertIn("Prix net", " ".join(file.message_ids.mapped("body")),
                      "le chatter nomme la colonne lue comme prix")
        file.action_apply()
        neuve = self.Info.search([("product_id", "=", self.chips.id),
                                  ("date_start", "=", self.today)])
        self.assertAlmostEqual(neuve.price, 4.25,
                               msg="la ligne fournisseur n'ouvre pas à 6,49")
        self.assertEqual(old.date_end, self.today - timedelta(days=1))
        # Les trois formes nommées par l'arbitrage, et elles seules.
        for entete in ("Prix de détail", "Retail price", "Prix de vente",
                       "Prix de détail suggéré"):
            with self.subTest(entete=entete):
                self.assertFalse(self.File._bf_role(entete),
                                 "« %s » n'est pas un prix d'achat" % entete)
        for entete in ("Prix net", "Prix", "Price", "Unit price", "Nouveau prix"):
            with self.subTest(entete=entete):
                self.assertEqual(self.File._bf_role(entete), "price")
        self.assertEqual(self.File._bf_role("Format de vente"), "uom",
                         "le mot « vente » ne retire que le rôle du PRIX")

    # D1 (b) : une colonne qui nomme la caisse lève le doute d'unité.
    def test_a_price_column_that_names_a_case_raises_the_unit_doubt(self):
        self.chips.barcode = "0123456789012"
        old = self.seller(self.chips, price=1.29)          # ligne fournisseur à l'unité
        for entete in ("Prix caisse", "Case price", "Case cost", "Coût caisse"):
            with self.subTest(entete=entete):
                file = self.price_file(["0123456789012;Chips;31,00"],
                                       header="Code;Description;%s" % entete,
                                       write_costs=True)
                file.action_read()
                self.assertEqual(self.File._bf_role(entete), "price",
                                 "la colonne garde le rôle du prix")
                self.assertEqual(file.doubt_count, 1,
                                 "31,00 est le prix de la CAISSE : l'unité est douteuse")
                self.assertEqual(file.matched_count, 0)
                with self.assertRaises(UserError):
                    file.action_apply()
        self.assertFalse(self.Info.search([("product_id", "=", self.chips.id),
                                           ("date_start", "=", self.today)]),
                         "31,00 la caisse ne devient pas 31,00 l'unité")
        self.assertFalse(self.Cost.search([("product_id", "=", self.chips.id)]))
        self.assertFalse(old.date_end, "et le vrai prix reste ouvert")
        # Une colonne de prix qui ne nomme aucun contenant ne lève rien.
        franc = self.price_file(["0123456789012;Chips;1,35"],
                                header="Code;Description;Prix unitaire")
        franc.action_read()
        self.assertEqual(franc.doubt_count, 0)
        self.assertEqual(franc.matched_count, 1)

    # D2 : la ligne fournisseur du MODÈLE sert de gabarit à toutes les variantes.
    def test_a_vendor_line_of_the_model_is_the_template_of_every_variant(self):
        attribut = self.env["product.attribute"].create({
            "name": "Format d'essai",
            "value_ids": [Command.create({"name": "500 ml"}),
                          Command.create({"name": "1 L"})],
        })
        gabarit = self.env["product.template"].create({
            "name": "Boisson", "type": "consu", "is_storable": True,
            "categ_id": self.categ.id, "uom_id": self.unit.id, "uom_po_id": self.unit.id,
            "taxes_id": [Command.clear()], "supplier_taxes_id": [Command.clear()],
            "property_account_expense_id": self.company_data["default_account_expense"].id,
            "attribute_line_ids": [Command.create({
                "attribute_id": attribut.id,
                "value_ids": [Command.set(attribut.value_ids.ids)]})],
        })
        petite, grande = gabarit.product_variant_ids[0], gabarit.product_variant_ids[1]
        self.assertEqual(len(gabarit.product_variant_ids), 2)
        petite.barcode = "0222222222221"
        grande.barcode = "0222222222222"
        # La ligne que l'onglet Achat crée par défaut : sur le MODÈLE, sans variante.
        commune = self.Info.create({
            "partner_id": self.vendor.id, "product_tmpl_id": gabarit.id,
            "price": 1.00, "min_qty": 24.0, "sequence": 7, "delay": 3,
            "company_id": self.company.id,
        })
        self.assertFalse(commune.product_id, "la ligne commune ne porte pas de variante")
        hier = self.today - timedelta(days=1)
        premier = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id, "date": hier,
            "filename": "petite.csv",
            "file": self.csv_file(["0222222222221;Boisson 500 ml;Unit;1,20"])})
        premier.action_read()
        premier.action_apply()
        self.assertFalse(commune.date_end,
                         "un fichier qui ne couvre qu'une variante ne ferme pas le gabarit "
                         "de ses sœurs")
        posee = self.Info.search([("product_id", "=", petite.id), ("date_start", "=", hier)])
        self.assertAlmostEqual(posee.min_qty, 24.0)
        self.assertEqual(posee.sequence, 7)
        self.assertEqual(posee.delay, 3)
        # La SECONDE variante, le lendemain : elle hérite encore du gabarit commun.
        second = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id, "date": self.today,
            "filename": "grande.csv",
            "file": self.csv_file(["0222222222222;Boisson 1 L;Unit;2,40"])})
        second.action_read()
        second.action_apply()
        soeur = self.Info.search([("product_id", "=", grande.id),
                                  ("date_start", "=", self.today)])
        self.assertAlmostEqual(soeur.price, 2.40)
        self.assertAlmostEqual(soeur.min_qty, 24.0,
                               msg="min_qty 0 ferait proposer 8 au lieu de 24")
        self.assertEqual(soeur.sequence, 7,
                         "sequence 1 déplacerait l'article d'un guide à un autre")
        self.assertEqual(soeur.delay, 3)
        self.assertFalse(commune.date_end, "et le gabarit commun est toujours ouvert")
        # Un fichier qui couvre TOUTES les variantes, lui, ferme le gabarit commun.
        complet = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id,
            "date": self.today + timedelta(days=7), "filename": "complet.csv",
            "file": self.csv_file(["0222222222221;Boisson 500 ml;Unit;1,30",
                                   "0222222222222;Boisson 1 L;Unit;2,60"])})
        complet.action_read()
        complet.action_apply()
        self.assertEqual(commune.date_end, self.today + timedelta(days=6),
                         "couvertes toutes les deux, la ligne commune se ferme la veille")
        for variante, prix in ((petite, 1.30), (grande, 2.60)):
            ligne = self.Info.search([("product_id", "=", variante.id),
                                      ("date_start", "=", self.today + timedelta(days=7))])
            self.assertAlmostEqual(ligne.price, prix)
            self.assertAlmostEqual(ligne.min_qty, 24.0,
                                   msg="le minimum du grossiste ne se perd sur aucune des "
                                       "deux variantes")

    # D3 : le fournisseur et la pièce jointe sont figés dès l'état « Lu ».
    def test_the_supplier_and_the_file_are_frozen_once_the_file_is_read(self):
        self.chips.barcode = "0123456789012"
        chez_nous = self.seller(self.chips, price=1.00, min_qty=24.0, sequence=7)
        file = self.price_file(["0123456789012;Chips;Unit;1,20"])
        file.action_read()
        with self.assertRaises(UserError):
            file.partner_id = self.other_vendor
        with self.assertRaises(UserError):
            file.file = self.csv_file(["0123456789012;Chips;Unit;9,99"])
        with self.assertRaises(UserError):
            file.date = self.today + timedelta(days=3)
        self.assertEqual(file.partner_id, self.vendor, "rien n'a bougé")
        file.action_apply()
        self.assertFalse(
            self.Info.search([("product_id", "=", self.chips.id),
                              ("partner_id", "=", self.other_vendor.id)]),
            "le prix ne part pas chez l'autre grossiste")
        neuve = self.Info.search([("product_id", "=", self.chips.id),
                                  ("date_start", "=", self.today)])
        self.assertAlmostEqual(neuve.price, 1.20)
        self.assertAlmostEqual(neuve.min_qty, 24.0)
        self.assertEqual(chez_nous.date_end, self.today - timedelta(days=1),
                         "et la ligne du VRAI fournisseur est bien fermée")
        # En brouillon, tout se corrige encore.
        brouillon = self.price_file(["0123456789012;Chips;Unit;1,25"])
        brouillon.partner_id = self.other_vendor
        self.assertEqual(brouillon.partner_id, self.other_vendor)

    # D4 : une ligne fournisseur du même jour ne laisse pas deux prix valides.
    def test_a_vendor_line_already_dated_today_does_not_leave_two_prices_valid(self):
        self.chips.barcode = "0123456789012"
        ancienne = self.seller(self.chips, price=1.00)
        a_la_main = self.seller(self.chips, price=1.11, date_start=self.today)
        file = self.price_file(["0123456789012;Chips;Unit;1,20"])
        file.action_read()
        file.action_apply()
        self.assertEqual(ancienne.date_end, self.today - timedelta(days=1),
                         "la ligne ouverte d'avant se ferme, même par la branche du jour")
        valides = self.Info.search([
            ("product_id", "=", self.chips.id),
            "|", ("date_start", "=", False), ("date_start", "<=", self.today),
            "|", ("date_end", "=", False), ("date_end", ">=", self.today)])
        self.assertEqual(len(valides), 1, "un seul prix valide aujourd'hui")
        self.assertEqual(valides, a_la_main)
        self.assertAlmostEqual(valides.price, 1.20)

    # D5 : une ligne fournisseur sans société n'est pas fermée par le fichier d'une société.
    def test_a_vendor_line_without_a_company_is_not_closed_by_one_store_s_file(self):
        self.chips.barcode = "0123456789012"
        partagee = self.Info.create({
            "partner_id": self.vendor.id,
            "product_tmpl_id": self.chips.product_tmpl_id.id,
            "product_id": self.chips.id,
            "price": 1.00, "min_qty": 12.0, "company_id": False,
        })
        self.assertFalse(partagee.company_id)
        file = self.price_file(["0123456789012;Chips;Unit;1,20"])
        file.action_read()
        file.action_apply()
        self.assertFalse(partagee.date_end,
                         "l'autre commerce ne perd pas son prix sans remplacement")
        neuve = self.Info.search([("product_id", "=", self.chips.id),
                                  ("date_start", "=", self.today)])
        self.assertEqual(neuve.company_id, self.company)
        self.assertAlmostEqual(neuve.price, 1.20)
        self.assertAlmostEqual(neuve.min_qty, 12.0,
                               msg="la ligne partagée sert quand même de gabarit")

    # D6 : « 1,250 » est ambigu, donc refusé et nommé — jamais divisé par mille.
    def test_a_thousands_comma_without_decimals_is_ambiguous_and_refused(self):
        for texte in ("1,250", "1.250", "12,345", "123,456"):
            with self.subTest(texte=texte):
                self.assertIsNone(self.File._bf_nombre(texte),
                                  "%r vaut 1,25 en français et 1 250 en anglais" % texte)
        for texte, attendu in (("1 250", 1250.0), ("1,250.00", 1250.00),
                               ("1.250,00", 1250.00), ("1,25", 1.25), ("1,2500", 1.25),
                               ("12,345.67", 12345.67), ("1 250,75", 1250.75)):
            with self.subTest(texte=texte):
                self.assertAlmostEqual(self.File._bf_nombre(texte), attendu, places=3)
        self.chips.barcode = "0123456789012"
        old = self.seller(self.chips, price=1.00)
        self.milk.barcode = "0123456789013"
        self.seller(self.milk, price=2.00)
        file = self.price_file(["0123456789012;Chips;Unit;1,250",
                                "0123456789013;Lait;Unit;2,10"])
        file.action_read()
        self.assertEqual(file.matched_count, 1, "la ligne lisible s'applique quand même")
        self.assertEqual(file.line_ids.filtered(
            lambda ligne: ligne.product_id == self.chips).rejected, "price")
        self.assertIn("0123456789012", " ".join(file.message_ids.mapped("body")),
                      "et la ligne ambiguë est NOMMÉE")
        file.action_apply()
        self.assertFalse(old.date_end, "1,25 n'a pas fermé un prix de 1,00")

    # D7 : une entête composée prend son rôle de son PREMIER mot reconnu.
    def test_a_composed_header_takes_its_role_from_its_first_known_word(self):
        self.assertEqual(
            self.File._bf_colonnes(["Description de l'article", "Code", "Prix"]),
            {"name": 0, "code": 1, "price": 2},
            "« Description de l'article » est une description, pas un code")
        self.assertEqual(self.File._bf_role("Code du produit"), "code")
        self.assertEqual(self.File._bf_role("Prix unitaire"), "price")
        self.assertEqual(self.File._bf_role("Numéro d'article"), "code")
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=1.00)
        file = self.price_file(["Chips 40 g;0123456789012;1,20"],
                               header="Description de l'article;Code;Prix")
        file.action_read()
        self.assertEqual(file.line_ids.code, "0123456789012")
        self.assertEqual(file.matched_count, 1,
                         "les 300 lignes ne sortent pas « code sans article »")
        self.assertEqual(file.unmatched_count, 0)

    # D8 : le prix montré avant la pose est celui en vigueur, pas une ligne à venir.
    def test_the_price_shown_before_the_apply_is_the_one_in_force_not_a_future_one(self):
        self.chips.barcode = "0123456789012"
        # ⚠️ La ligne en vigueur porte une DATE de début. Avec un `date_start` nul,
        # Postgres trie les NULL en tête sur un « desc » et l'ancienne recherche rendait la
        # bonne ligne par accident : le défaut ne se reproduit que sur une série datée, qui
        # est précisément ce qu'un fichier de prix écrit.
        self.seller(self.chips, price=4.00, date_start=self.today - timedelta(days=7))
        self.seller(self.chips, price=20.00, date_start=self.today + timedelta(days=14))
        file = self.price_file(["0123456789012;Chips;Unit;4,40"])
        file.action_read()
        ligne = file.line_ids
        self.assertAlmostEqual(ligne.current_price, 4.00,
                               msg="le prix « actuel » est celui d'aujourd'hui, pas celui "
                                   "d'une ligne qui commence dans deux semaines")
        self.assertAlmostEqual(ligne.delta_pct, 10.0, places=1,
                               msg="+10 %, pas −78 % : c'est l'écran de la décision")

    # D10 : deux fichiers du même jour écrivent UN coût, pas deux.
    def test_two_files_of_the_same_day_write_one_cost_not_two(self):
        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=1.00)
        premier = self.price_file(["0123456789012;Chips;Unit;1,80"], write_costs=True)
        premier.action_read()
        premier.action_apply()
        second = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id,
            "date": self.today, "filename": "corrige.csv", "write_costs": True,
            "file": self.csv_file(["0123456789012;Chips;Unit;1,70"])})
        second.action_read()
        second.action_apply()
        couts = self.Cost.search([("product_id", "=", self.chips.id),
                                  ("date", "=", self.today)])
        self.assertEqual(len(couts), 1, "un coût par jour et par fichier, corrigé en place")
        self.assertAlmostEqual(couts.cost, 1.70)
        self.assertFalse(couts.has_previous,
                         "aucune variation entre deux coûts du même jour")
        self.assertAlmostEqual(couts.delta_pct, 0.0,
                               msg="le −5,56 % mesuré n'a jamais eu lieu")
        # Un coût venu d'une facture le même jour n'est pas réécrit par un fichier.
        facture = self.Cost.create({
            "product_id": self.chips.id, "company_id": self.company.id,
            "date": self.today, "cost": 1.90, "source": "bill"})
        troisieme = self.File.create({
            "partner_id": self.vendor.id, "company_id": self.company.id,
            "date": self.today, "filename": "encore.csv", "write_costs": True,
            "file": self.csv_file(["0123456789012;Chips;Unit;1,60"])})
        troisieme.action_read()
        troisieme.action_apply()
        self.assertAlmostEqual(facture.cost, 1.90,
                               msg="le coût lu sur une facture reste celui de la facture")
        self.assertAlmostEqual(couts.cost, 1.60)

    # D11 : le dollar néo-zélandais est une devise, pas du bruit illisible.
    def test_the_new_zealand_dollar_is_read_as_a_currency(self):
        for texte, attendu in (("12.50 NZD", 12.50), ("NZ$12.50", 12.50),
                               ("12,50 NZD", 12.50), ("NZ$ 1 234.56", 1234.56),
                               ("nzd 3.99", 3.99)):
            with self.subTest(texte=texte):
                self.assertAlmostEqual(self.File._bf_nombre(texte), attendu, places=3)
        self.milk.barcode = "0123456789013"
        self.seller(self.milk, price=2.00)
        file = self.price_file(["0123456789013;Milk 2 L;Unit;NZ$2.49"])
        file.action_read()
        self.assertEqual(file.matched_count, 1,
                         "la liste de la laiterie n'est pas « Price not readable »")
        self.assertAlmostEqual(file.line_ids.price, 2.49)

    # D12 : une mesure seule décrit l'article et ne dit rien de l'unité d'achat.
    def test_a_measure_alone_describes_the_item_and_does_not_raise_the_doubt(self):
        litre = self.env.ref("uom.product_uom_litre")
        bidon = self.make_product("Lait 4 L", price=9.99, barcode="0333333333331")
        self.seller(bidon, price=7.20)                    # ligne fournisseur à l'unité
        avec = self.price_file(["0333333333331;Lait 4 L;4 L;7,45"])
        avec.action_read()
        self.assertEqual(avec.doubt_count, 0,
                         "« 4 L » est le format du bidon, pas l'unité de l'achat")
        self.assertEqual(avec.matched_count, 1)
        avec.action_apply()
        self.assertAlmostEqual(self.Info.search([("product_id", "=", bidon.id),
                                                 ("date_start", "=", self.today)]).price,
                               7.45)
        # Le MÊME fichier sans la colonne Format : la même lecture. Ajouter l'information
        # ne peut pas faire échouer ce qui passait sans elle.
        sans = self.price_file(["0333333333331;Lait 4 L;7,50"],
                               header="Barcode;Description;Price")
        sans.action_read()
        self.assertEqual((sans.doubt_count, sans.matched_count),
                         (avec.doubt_count, avec.matched_count))
        # Et les deux scènes du contenant tiennent toujours.
        vrac = self.make_product("Lait en vrac", price=3.49, barcode="0333333333332",
                                 uom_id=litre.id, uom_po_id=litre.id)
        self.seller(vrac, price=1.20)
        for etiquette, doute in (("12 x 1 L", 1), ("12 UNITS", 1), ("1 L", 0),
                                 ("4 L", 0), ("Caisse de 12", 1)):
            with self.subTest(etiquette=etiquette):
                file = self.price_file(["0333333333332;Lait;%s;1,35" % etiquette])
                file.action_read()
                self.assertEqual(file.doubt_count, doute, etiquette)

    # D13 : une ligne fournisseur périmée ne donne pas un prix de 0,00.
    def test_an_expired_vendor_line_does_not_make_a_request_for_quotation_at_zero(self):
        perimee = self.seller(self.chips, price=1.00,
                              date_start=self.today - timedelta(days=30),
                              date_end=self.today - timedelta(days=1))
        regle = self.rule(self.chips, 10.0, 20.0)
        regle.supplier_id = perimee
        guide = self.guide()
        guide.action_compute()
        ligne = guide.line_ids
        self.assertTrue(ligne, "l'article est bien dans le guide, il faut le réassortir")
        self.assertAlmostEqual(ligne.suggested, 20.0)
        with self.assertRaises(UserError) as attrape:
            guide.action_create_rfq()
        self.assertIn(self.chips.display_name, str(attrape.exception),
                      "le guide NOMME l'article dont le prix manque")
        self.assertFalse(guide.order_id, "aucune demande de prix n'est partie")
        self.assertTrue(ligne.price_missing, "absent n'est pas zéro")
        self.assertEqual(guide.price_missing_count, 1)
        self.assertAlmostEqual(guide.amount_total, 0.0)
        # Un prix revenu, et la demande part.
        self.seller(self.chips, price=1.05, date_start=self.today)
        guide.action_compute()
        self.assertFalse(guide.line_ids.price_missing)
        self.assertEqual(guide.price_missing_count, 0)
        guide.action_create_rfq()
        self.assertTrue(guide.order_id)
        self.assertAlmostEqual(guide.order_id.order_line.price_unit, 1.05)

    # L'hypothèse du une ligne fournisseur sur un SOUS-CONTACT du grossiste.
    def test_a_vendor_line_on_a_sub_contact_of_the_wholesaler_is_the_one_replaced(self):
        division = self.env["res.partner"].create({
            "name": "Grossiste Essai — division épicerie",
            "parent_id": self.vendor.id, "type": "delivery"})
        self.assertEqual(division.commercial_partner_id, self.vendor,
                         "le sous-contact répond au grossiste")
        self.chips.barcode = "0123456789012"
        chez_la_division = self.seller(self.chips, partner=division, price=1.00,
                                       min_qty=24.0, sequence=7, delay=3)
        file = self.price_file(["0123456789012;Chips;Unit;1,20"])
        file.action_read()
        self.assertEqual(file.matched_count, 1)
        self.assertAlmostEqual(file.line_ids.current_price, 1.00,
                               msg="la lecture voit la ligne de la division")
        file.action_apply()
        self.assertEqual(chez_la_division.date_end, self.today - timedelta(days=1),
                         "et la pose la ferme, au lieu de la laisser ouverte à côté")
        neuve = self.Info.search([("product_id", "=", self.chips.id),
                                  ("date_start", "=", self.today)])
        self.assertEqual(len(neuve), 1)
        self.assertAlmostEqual(neuve.price, 1.20)
        self.assertAlmostEqual(neuve.min_qty, 24.0,
                               msg="la ligne neuve n'est pas rebâtie de rien")
        self.assertEqual(neuve.sequence, 7)
        self.assertEqual(neuve.delay, 3)

    # Retombée du C11 du lot 1, mesurée ici : la lecture de la série s'arrête à
    # « aujourd'hui », et son défaut est le fuseau de l'USAGER. Le guide, lui, lit le fuseau
    # du commerce partout ailleurs — c'est le du tour 1, en miniature.
    def test_the_cost_of_the_guide_is_read_on_the_day_of_the_store(self):
        adresse = self.env["res.partner"].create({
            "name": "Magasin de Takapuna", "tz": "Pacific/Auckland"})
        self.warehouse.partner_id = adresse
        self.company.partner_id.tz = False
        self.env.user.tz = "America/Toronto"
        self.seller(self.chips, price=1.0)
        self.rule(self.chips, 10.0, 30.0)
        self.Cost.create({"product_id": self.chips.id, "company_id": self.company.id,
                          "date": date(2026, 11, 11), "cost": 1.00, "source": "manual"})
        self.Cost.create({"product_id": self.chips.id, "company_id": self.company.id,
                          "date": date(2026, 11, 12), "cost": 1.20, "source": "manual"})
        guide = self.guide()
        # 2026-11-12 01:00 UTC : jeudi 14 h à Auckland, mercredi 20 h à Toronto. Le coût du
        # 12 est en vigueur pour le commerce, et pas encore pour l'usager.
        with freeze_time("2026-11-12 01:00:00"):
            self.assertEqual(guide._bf_today(), date(2026, 11, 12))
            guide.action_compute()
        ligne = guide.line_ids
        self.assertEqual(ligne.cost_date, date(2026, 11, 12),
                         "le coût du jour du commerce, pas celui de la veille de l'usager")
        self.assertAlmostEqual(ligne.cost_last, 1.20)
        self.assertAlmostEqual(ligne.cost_delta_pct, 20.0, places=1)


    # ── la langue d'un corps que le chatter garde (16ᵉ constat) ──────────────────────────
    def test_the_report_of_a_price_file_is_rendered_in_the_language_of_the_store(self):
        """16ᵉ constat, côté lot 2 : le rapport est bâti **ailleurs** (`_bf_rapport`), de
        morceaux traduits un à un et des libellés de la sélection `rejected`, qui passent tous
        par `self.env`. La langue doit donc porter jusque dans la méthode auxiliaire, et pas
        seulement sur l'appel final.

        ⚠️ Mesuré : la langue **du contexte sous lequel le corps est rendu**, jamais le texte.
        La passe tourne sans montée (`TEST_SANS_U=1`), donc le `.po` du module n'est pas en
        base et le français n'y serait pas de toute façon."""
        from unittest.mock import patch
        self.env["res.lang"]._activate_lang("fr_CA")
        self.company.partner_id.lang = "fr_CA"
        self.assertFalse(self.env.context.get("lang"),
                         "aucune langue au contexte, comme hors requête web")
        File = type(self.File)
        origine_post = File._message_log
        origine_rapport = File._bf_rapport
        langues = {"post": [], "rapport": []}

        def espion_post(self, *args, **kwargs):
            langues["post"].append(self.env.context.get("lang"))
            return origine_post(self, *args, **kwargs)

        def espion_rapport(self, *args, **kwargs):
            langues["rapport"].append(self.env.context.get("lang"))
            return origine_rapport(self, *args, **kwargs)

        self.chips.barcode = "0123456789012"
        self.seller(self.chips, price=1.00)
        with patch.object(File, "_message_log", espion_post), \
                patch.object(File, "_bf_rapport", espion_rapport):
            file = self.price_file(["0123456789012;Chips 40 g;Unit;1,20"])
            file.action_read()
            file.action_apply()
        self.assertEqual(file.state, "applied")
        self.assertEqual(langues["rapport"], ["fr_CA", "fr_CA"],
                         "le rapport est bâti dans la langue du commerce, aux deux portes")
        self.assertEqual(langues["post"], ["fr_CA", "fr_CA"],
                         "et le corps est journalisé sous la même langue")

    # ── un rapport au chatter ne tue pas le geste du gérant ────────
    def test_a_manager_without_an_email_asks_for_a_quotation(self):
        """🔴 Le même défaut que celui mesuré sur une base semée au lot 1 : un gérant **sans adresse
        courriel** voyait son geste annulé par le rapport que le module écrit à la fin —
        `message_post` exige une adresse d'auteur (`raise_on_email=True`, `mail_thread.py`
        l. 2924) et `email_formatted` rend **False** sans courriel. Le bon de commande était
        créé, puis la levée annulait tout : le guide restait « calculé » et rien n'était
        commandé. Le banc ne pouvait pas le voir, `new_test_user` donnant un courriel à tous
        ses usagers."""
        manager = new_test_user(
            self.env, login="manager-no-email-supply", email=False,
            groups="bf_retail.group_retail_manager,stock.group_stock_user,"
                   "purchase.group_purchase_user")
        self.assertFalse(manager.partner_id.email_formatted,
                         "son contact ne rend aucune adresse : c'est ce que la levée lisait")
        self.seller(self.chips, price=1.0)
        self.rule(self.chips, 10.0, 30.0)
        guide = self.guide()
        guide.with_user(manager).action_compute()
        avant = len(guide.message_ids)

        guide.with_user(manager).action_create_rfq()

        self.assertEqual(guide.state, "ordered", "le geste a tenu de bout en bout")
        self.assertTrue(guide.order_id, "et la demande de prix existe")
        self.assertEqual(len(guide.message_ids), avant + 1, "une note, au fil du guide")
        note = guide.message_ids[0]
        self.assertIn(guide.order_id.name, note.body, "qui nomme la demande de prix")
        self.assertEqual(note.author_id, manager.partner_id)
        self.assertFalse(note.email_from, "sans adresse d'expéditeur, ce qui est le sujet")
        self.assertEqual(note.subtype_id, self.env.ref("mail.mt_note"))
        self.assertFalse(note.notification_ids,
                         "un rapport du module ne notifie personne : c'est ce qu'on échange")

    def test_a_manager_without_an_email_reads_and_applies_a_price_file(self):
        """Les deux autres portes du même défaut : le rapport de lecture et celui de la pose
        d'un fichier de prix. Sans courriel, le gérant ne pouvait **ni** lire **ni** appliquer
        un fichier — les lignes étaient écrites puis annulées par la levée du rapport."""
        manager = new_test_user(
            self.env, login="manager-no-email-file", email=False,
            groups="bf_retail.group_retail_manager,stock.group_stock_user")
        self.assertFalse(manager.partner_id.email_formatted)
        self.chips.barcode = "0123456789012"
        ancienne = self.seller(self.chips, price=1.00)
        file = self.price_file(["0123456789012;Chips;Unit;1,20"])
        courriels = self.env["mail.mail"].search_count([])

        file.with_user(manager).action_read()

        self.assertEqual(file.state, "read")
        self.assertEqual(file.matched_count, 1)
        self.assertEqual(len(file.message_ids), 1, "le rapport de lecture est au fil")

        file.with_user(manager).action_apply()

        self.assertEqual(file.state, "applied")
        self.assertEqual(ancienne.date_end, self.today - timedelta(days=1),
                         "le prix d'avant est fermé la veille : la pose est allée au bout")
        neuve = self.Info.search([("product_id", "=", self.chips.id),
                                  ("date_start", "=", self.today)])
        self.assertEqual(len(neuve), 1)
        self.assertAlmostEqual(neuve.price, 1.20)
        self.assertEqual(len(file.message_ids), 2, "et le rapport de pose aussi")
        for note in file.message_ids:
            self.assertEqual(note.author_id, manager.partner_id)
            self.assertFalse(note.email_from, "sans adresse d'expéditeur")
            self.assertFalse(note.notification_ids, "et sans notifier personne")
        self.assertEqual(self.env["mail.mail"].search_count([]), courriels,
                         "rien ne part de la file de courriels")

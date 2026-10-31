"""Sales of another register, imported from its export: one Kitchen consumption per day, a file or a
day never twice.

The fixtures are bf_kitchen's TestKitchen (a dish of 240 g of salmon, 30 g of a cream sauce and
3 g of salt). Odoo 18 runs only the test methods a class defines itself."""
import base64
import io
from datetime import date

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.bf_kitchen.tests.test_kitchen import TestKitchen

CSV_FR = ("Jour;Code article;Nom de l'article;Quantité vendue\n"
          "30/10/2026;T01;Tartare de saumon;3\n"
          "30/10/2026;GC;Carte-cadeau;1\n"
          "31/10/2026;T01;TARTARE  de saumon;2,0\n"
          "31/10/2026;;Frites maison;4\n")


@tagged("post_install", "-at_install")
class TestSalesImport(TestKitchen):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env["stock.warehouse"].search([("company_id", "=", cls.company.id)], limit=1)
        cls.source = cls.env["bf.kitchen.sales.source"].create({"name": "Square, salle", "warehouse_id": cls.warehouse.id})
        cls.fries = cls.ingredient("Frites maison (portion)", cls.unit, sale_ok=True)

    def new_import(self, content, filename="ventes.csv", source=None):
        if isinstance(content, str):
            content = content.encode("utf-8")
        return self.env["bf.kitchen.sales.import"].create({
            "source_id": (source or self.source).id, "file": base64.b64encode(content), "filename": filename})

    def on_stock(self, product):
        product.invalidate_recordset()
        return product.with_context(location=self.warehouse.lot_stock_id.id).qty_available

    def match_all(self, imp):
        for line in imp.line_ids:
            if line.code == "T01":
                line.product_id = self.dish
            elif line.code == "GC":
                line.ignored = True
            else:
                line.product_id = self.fries

    def test_i1_french_csv_read(self):
        """French columns, ; separated, decimal comma, day/month/year: four sales read."""
        imp = self.new_import(CSV_FR)
        imp.action_read()
        self.assertEqual(imp.state, "read")
        self.assertEqual(len(imp.line_ids), 4)
        first = imp.line_ids[0]
        self.assertEqual((first.date, first.code, first.name, first.qty),
                         (date(2026, 10, 30), "T01", "Tartare de saumon", 3.0))
        self.assertEqual(imp.line_ids[2].qty, 2.0)
        self.assertEqual((imp.date_from, imp.date_to), (date(2026, 10, 30), date(2026, 10, 31)))
        self.assertEqual(imp.unmatched_count, 4)

    def test_i2_excel_read(self):
        """An Excel export, English columns, real dates and numeric codes."""
        import openpyxl
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.append(["Item sales report"])
        sheet.append(["Date", "SKU", "Item", "Qty"])
        sheet.append([date(2026, 10, 29), 1001, "Salmon tartare", 5])
        buffer = io.BytesIO()
        book.save(buffer)
        imp = self.new_import(buffer.getvalue(), filename="sales.xlsx")
        imp.action_read()
        line = imp.line_ids
        self.assertEqual((line.date, line.code, line.name, line.qty), (date(2026, 10, 29), "1001", "Salmon tartare", 5.0))

    def test_i3_match_once_then_import_per_day(self):
        """Each item is matched once (same code, any case or spacing); the import makes one
        consumption per day, the dishes exploded; the gift card takes nothing."""
        imp = self.new_import(CSV_FR)
        imp.action_read()
        with self.assertRaises(UserError):
            imp.action_import()
        imp.line_ids[0].product_id = self.dish
        self.assertEqual(imp.line_ids[2].status, "matched", "the same item, matched with the first line")
        self.match_all(imp)
        imp.action_import()
        self.assertEqual(imp.state, "done")
        self.assertEqual(len(imp.picking_ids), 2)
        self.assertEqual(sorted(imp.picking_ids.mapped("bf_kitchen_event_date")), [date(2026, 10, 30), date(2026, 10, 31)])
        self.assertTrue(all(p.bf_kitchen_source == "import" and p.state == "done" for p in imp.picking_ids))
        self.assertAlmostEqual(self.on_stock(self.salmon), -5 * 240.0)
        self.assertAlmostEqual(self.on_stock(self.fries), -4.0)
        mapping = self.source.mapping_ids.filtered(lambda m: m.code == "T01")
        self.assertEqual(mapping.product_id, self.dish)
        # the next file of the register is matched already
        nxt = self.new_import("Date,Code,Item,Qty\n2026-11-01,T01,Tartare,1\n")
        nxt.action_read()
        self.assertEqual(nxt.line_ids.status, "matched")

    def test_i4_file_or_day_never_twice(self):
        """The same file, or a file with a day already imported, is refused; cancelled, the food
        goes back and the days are free again."""
        imp = self.new_import(CSV_FR)
        imp.action_read()
        self.match_all(imp)
        imp.action_import()
        again = self.new_import(CSV_FR)
        again.action_read()
        with self.assertRaises(UserError):
            again.action_import()
        overlap = self.new_import("Date,Code,Item,Qty\n2026-10-31,T01,Tartare,1\n")
        overlap.action_read()
        with self.assertRaises(UserError):
            overlap.action_import()
        imp.action_cancel()
        self.assertAlmostEqual(self.on_stock(self.salmon), 0.0)
        overlap.action_import()
        self.assertAlmostEqual(self.on_stock(self.salmon), -240.0)

    def test_i5_bad_files_refused(self):
        """A file without the columns, a date or a quantity that cannot be read: refused with the row."""
        with self.assertRaises(UserError):
            self.new_import("Bonjour,Monde\n1,2\n").action_read()
        with self.assertRaises(UserError) as error:
            self.new_import("Date,Item,Qty\n2026-10-30,Tartare,3\nhier,Tartare,1\n").action_read()
        self.assertIn("3", str(error.exception))
        with self.assertRaises(UserError):
            self.new_import("Date,Item,Qty\n2026-10-30,Tartare,beaucoup\n").action_read()

    def test_i6_month_first_register(self):
        """A register that writes month/day/year."""
        self.source.date_order = "mdy"
        imp = self.new_import("Date,Item,Qty\n10/30/2026,Tartare,3\n")
        imp.action_read()
        self.assertEqual(imp.line_ids.date, date(2026, 10, 30))

    def test_i7_rights(self):
        """The cook does not import; nobody forges the state or a sale."""
        cook = self.mkuser("i7_cook", "bf_kitchen.group_kitchen_cook")
        with self.assertRaises(AccessError):
            self.env["bf.kitchen.sales.import"].with_user(cook).create({"source_id": self.source.id})
        chef = self.mkuser("i7_chef", "bf_kitchen.group_kitchen_manager")
        imp = self.new_import(CSV_FR).with_user(chef)
        imp.action_read()
        with self.assertRaises(AccessError):
            imp.write({"state": "done"})
        with self.assertRaises(AccessError):
            imp.line_ids[0].write({"qty": 300.0})
        forged = self.env["bf.kitchen.sales.import"].with_user(chef).with_context(default_state="done").create(
            {"source_id": self.source.id})
        self.assertEqual(forged.state, "draft")
        imp.line_ids[0].product_id = self.dish
        self.assertEqual(imp.line_ids[0].status, "matched", "the manager matches")

    def test_i8_manager_without_email(self):
        """A kitchen manager with no email address imports: the note on the import never blocks it."""
        chef = self.mkuser("i8_chef", "bf_kitchen.group_kitchen_manager")
        chef.partner_id.email = False
        imp = self.new_import(CSV_FR).with_user(chef)
        imp.action_read()
        self.match_all(imp)
        imp.action_import()
        self.assertEqual(imp.state, "done")
        imp.action_cancel()
        self.assertEqual(imp.state, "cancel")

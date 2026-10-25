"""Une dépense rattachée compte dans les coûts de l'abonnement, et seulement une fois décidée."""
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSubscriptionExpense(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.vendor = cls.env['res.partner'].create({'name': 'Fournisseur dépense', 'supplier_rank': 1})
        cls.employee = cls.env['hr.employee'].create({'name': 'Employé dépense'})
        cls.product = cls.env['product.product'].create({
            'name': 'Frais abonnement', 'can_be_expensed': True, 'standard_price': 0.0,
            'supplier_taxes_id': [(5, 0, 0)],
        })
        cls.sub = cls.env['subscription.subscription'].create({
            'name': 'Abonnement payé à la carte', 'vendor_id': cls.vendor.id,
            'cycle': 'monthly', 'cycle_amount': 30.0, 'state': 'active',
            'start_date': fields.Date.today() - relativedelta(months=6),
        })

    def _expense(self, amount, date, state):
        exp = self.env['hr.expense'].create({
            'name': 'Abonnement', 'employee_id': self.employee.id, 'product_id': self.product.id,
            'total_amount_currency': amount, 'date': date, 'tax_ids': [(5, 0, 0)],
            'subscription_id': self.sub.id,
        })
        # L'état se calcule depuis la note ; on le pose pour isoler la règle de comptage.
        self.env.cr.execute("UPDATE hr_expense SET state = %s WHERE id = %s", (state, exp.id))
        exp.invalidate_recordset(['state'])
        self.sub.invalidate_recordset()
        self.sub.modified(['expense_ids'])
        return exp

    def test_depense_decidee_compte(self):
        hier = fields.Date.today() - relativedelta(days=1)
        self._expense(30.0, hier, 'done')
        self._expense(30.0, fields.Date.today() - relativedelta(days=40), 'approved')
        self.sub._compute_totals()
        self.sub._compute_last_vendor_bill_date()
        self.assertAlmostEqual(self.sub.total_paid_lifetime, 60.0)
        self.assertEqual(self.sub.last_vendor_bill_date, hier)
        self.assertEqual(self.sub.expense_count, 2)
        self.assertFalse(self.sub.is_dormant)

    def test_depense_non_decidee_ne_compte_pas(self):
        self._expense(30.0, fields.Date.today(), 'draft')
        self._expense(30.0, fields.Date.today(), 'refused')
        self.sub._compute_totals()
        self.sub._compute_last_vendor_bill_date()
        self.assertEqual(self.sub.total_paid_lifetime, 0.0)
        self.assertFalse(self.sub.last_vendor_bill_date)

    def test_piece_de_note_refusee(self):
        from odoo.exceptions import ValidationError
        exp = self._expense(30.0, fields.Date.today(), 'draft')
        sheet = self.env['hr.expense.sheet'].create({
            'name': 'Note', 'employee_id': self.employee.id, 'expense_line_ids': [(6, 0, exp.ids)],
        })
        move = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.vendor.id, 'expense_sheet_id': sheet.id,
            'invoice_date': fields.Date.today(),
        })
        self.assertFalse(move._suggest_subscription())
        with self.assertRaises(ValidationError):
            move.subscription_id = self.sub
            move.flush_recordset()

    def test_totaux_en_sudo(self):
        self._expense(30.0, fields.Date.today(), 'done')
        lecteur = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Lecteur abonnements', 'login': 'lecteur_sub_exp',
            # Facturation, sans droits sur les notes de frais.
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('account.group_account_invoice').id])],
        })
        sub = self.sub.with_user(lecteur)
        sub.invalidate_recordset()
        self.assertAlmostEqual(sub.total_paid_lifetime, 30.0)
        self.assertEqual(sub.expense_count, 1)

    def test_abonnement_d_une_autre_societe_refuse(self):
        # Le domaine de la vue ne joue qu'à l'écran : par RPC, une dépense se rattachait
        # à l'abonnement d'une autre société et en gonflait les coûts.
        autre = self.env['res.company'].create({'name': 'Autre société dépense'})
        sub_ailleurs = self.env['subscription.subscription'].create({
            'name': 'Abonnement ailleurs', 'vendor_id': self.vendor.id, 'company_id': autre.id,
            'cycle': 'monthly', 'cycle_amount': 30.0, 'state': 'active',
            'start_date': fields.Date.today() - relativedelta(months=6),
        })
        exp = self._expense(30.0, fields.Date.today(), 'done')
        with self.assertRaises(UserError):
            exp.subscription_id = sub_ailleurs
            exp.flush_recordset()

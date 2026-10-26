"""Facture brouillon générée depuis la banque d'heures."""
from datetime import date, timedelta

from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form, TransactionCase, tagged


@tagged('post_install', '-at_install', 'bf_hour_bank')
class TestFactureBanque(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = cls.env['res.partner'].create({'name': 'Client Facture Co', 'is_company': True})
        cls.contact_facturation = cls.env['res.partner'].create({
            'name': 'Comptes payables', 'parent_id': cls.client.id,
        })
        cls.autre_client = cls.env['res.partner'].create({'name': 'Autre Client'})
        cls.projet_a = cls.env['project.project'].create({
            'name': 'Projet A', 'allow_timesheets': True, 'partner_id': cls.client.id,
        })
        cls.projet_b = cls.env['project.project'].create({
            'name': 'Projet B', 'allow_timesheets': True, 'partner_id': cls.client.id,
        })
        # Prix catalogue volontairement différent du taux facturé.
        cls.heures = cls.env['product.product'].create({
            'name': 'Honoraires de consultation', 'type': 'service', 'list_price': 100.0,
        })
        cls.forfait = cls.env['product.product'].create({
            'name': 'Forfait hébergement', 'type': 'service', 'list_price': 300.0,
        })
        cls.journal = cls.env['account.journal'].search(
            [('type', '=', 'sale'), ('company_id', '=', cls.env.company.id)], limit=1,
        )
        cls.employe = cls.env.ref('hr.employee_admin', raise_if_not_found=False) \
            or cls.env['hr.employee'].create({'name': 'Test Emp'})
        cls.today = date.today()

    def _banque(self, **vals):
        return self.env['hour.bank.client'].create({
            'partner_id': self.client.id,
            'project_ids': [(6, 0, [self.projet_a.id, self.projet_b.id])],
            'product_filter_mode': 'include',
            'filter_product_ids': [(6, 0, [self.heures.id])],
            **vals,
        })

    def _fdt(self, projet, heures, jours):
        return self.env['account.analytic.line'].create({
            'name': 'FDT %sh' % heures,
            'date': self.today - timedelta(days=jours),
            'unit_amount': heures,
            'project_id': projet.id,
            'employee_id': self.employe.id,
        })

    def _facture(self, qty, jours, poster=True, price=80.0):
        move = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.contact_facturation.id,
            'invoice_date': self.today - timedelta(days=jours),
            'journal_id': self.journal.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': self.heures.id, 'quantity': qty, 'price_unit': price,
            })],
        })
        if poster:
            move.action_post()
        return move

    def _assistant(self, bank, **vals):
        return self.env['hour.bank.invoice.wizard'].create({'bank_id': bank.id, **vals})

    def _jeu_postpaye(self):
        """A : 4 h (j-10) + 3 h (j-5), facture de 5 h (j-4), B : 1 h (j-2).
        Non couvert : 2 h de A (les plus récentes) et 1 h de B."""
        self._fdt(self.projet_a, 4.0, 10)
        self._fdt(self.projet_a, 3.0, 5)
        self._facture(5.0, 4)
        self._fdt(self.projet_b, 1.0, 2)

    # ------------------------------------------------------------------

    def test_postpaye_fifo_et_ventilation(self):
        self._jeu_postpaye()
        bank = self._banque(invoice_split_by_project=True)
        wiz = self._assistant(bank)
        self.assertAlmostEqual(wiz.uncovered_hours, 3.0, places=2)
        lignes = {l.project_id: l.quantity for l in wiz.line_ids}
        self.assertAlmostEqual(lignes[self.projet_a], 2.0, places=2)
        self.assertAlmostEqual(lignes[self.projet_b], 1.0, places=2)
        # La coupe exclut ce qui est porté après elle.
        wiz.date_cutoff = self.today - timedelta(days=3)
        self.assertEqual(wiz.line_ids.project_id, self.projet_a)
        self.assertAlmostEqual(wiz.total_hours, 2.0, places=2)

    def test_ligne_unique_par_defaut(self):
        self._jeu_postpaye()
        wiz = self._assistant(self._banque())
        self.assertEqual(len(wiz.line_ids), 1)
        self.assertAlmostEqual(wiz.line_ids.quantity, 3.0, places=2)
        self.assertIn('rapport de banque', wiz.line_ids.name)

    def test_brouillons_deduits(self):
        self._jeu_postpaye()
        bank = self._banque(invoice_split_by_project=True)
        brouillon = self._facture(2.0, 0, poster=False)
        wiz = self._assistant(bank)
        self.assertEqual(wiz.draft_move_ids, brouillon)
        self.assertAlmostEqual(wiz.draft_hours, 2.0, places=2)
        # Le brouillon couvre les heures les plus anciennes : reste B.
        self.assertEqual(wiz.line_ids.project_id, self.projet_b)
        self.assertAlmostEqual(wiz.total_hours, 1.0, places=2)
        wiz.action_create_invoice()
        self.assertRegex(str(bank.message_ids[:1].body), r'2[.,]00 h déjà en brouillon')

    def test_taux_contact_et_produit_de_la_derniere_facture(self):
        self._jeu_postpaye()
        wiz = self._assistant(self._banque())
        self.assertEqual(wiz.product_id, self.heures)
        self.assertAlmostEqual(wiz.price_unit, 80.0)
        self.assertEqual(wiz.partner_id, self.contact_facturation)
        # Le taux de la banque l'emporte.
        wiz = self._assistant(self._banque(invoice_hourly_rate=120.0))
        self.assertAlmostEqual(wiz.price_unit, 120.0)

    def test_creation_du_brouillon(self):
        self._jeu_postpaye()
        bank = self._banque(invoice_split_by_project=True)
        action = self._assistant(bank).action_create_invoice()
        move = self.env['account.move'].browse(action['res_id'])
        self.assertEqual(move.state, 'draft')
        self.assertEqual(move.move_type, 'out_invoice')
        self.assertEqual(move.partner_id, self.contact_facturation)
        self.assertEqual(move.invoice_line_ids.product_id, self.heures)
        self.assertEqual(set(move.invoice_line_ids.mapped('price_unit')), {80.0})
        self.assertAlmostEqual(sum(move.invoice_line_ids.mapped('quantity')), 3.0, places=2)
        self.assertTrue(self.env['ir.attachment'].search_count([
            ('res_model', '=', 'account.move'), ('res_id', '=', move.id),
        ]))
        trace = bank.message_ids[:1].body
        self.assertIn('data-oe-id="%d"' % move.id, trace)
        # Aucun brouillon avant : la trace ne compte pas celui qu'on vient de créer.
        self.assertNotIn('brouillon,', trace)
        self.assertNotIn('déjà en brouillon', trace)
        # Le PDF joint n'est pas pris pour une facture à importer.
        self.assertFalse(move.message_ids.filtered(
            lambda m: 'attachment' in (m.body or '') or 'pièce jointe' in (m.body or '')))
        # Le solde ne bouge pas tant que le brouillon n'est pas validé...
        self.assertAlmostEqual(bank.current_balance, -3.0, places=2)
        # ...mais un second clic ne repropose pas les mêmes heures.
        with self.assertRaises(UserError):
            self._assistant(bank).action_create_invoice()
        move.action_post()
        bank.invalidate_recordset(['current_balance'])
        self.assertAlmostEqual(bank.current_balance, 0.0, places=2)

    def test_prepaye_tranche(self):
        bank = self._banque(invoice_mode='prepaid', invoice_block_hours=10.0,
                            invoice_product_id=self.heures.id, invoice_hourly_rate=90.0)
        wiz = self._assistant(bank)
        self.assertEqual(len(wiz.line_ids), 1)
        self.assertAlmostEqual(wiz.line_ids.quantity, 10.0)
        self.assertIn('10 h', wiz.line_ids.name)
        move = self.env['account.move'].browse(wiz.action_create_invoice()['res_id'])
        self.assertAlmostEqual(move.amount_untaxed, 900.0, places=2)
        # Le produit est ajouté au libellé à la création.
        self.assertTrue(move.invoice_line_ids.name.startswith('Honoraires de consultation — '))

    def test_garde_produit(self):
        with self.assertRaises(ValidationError):
            self._banque(invoice_product_id=self.forfait.id)
        self._jeu_postpaye()
        wiz = self._assistant(self._banque())
        wiz.product_id = self.forfait
        with self.assertRaises(UserError):
            wiz.action_create_invoice()

    def test_garde_partenaire(self):
        self._jeu_postpaye()
        wiz = self._assistant(self._banque())
        wiz.partner_id = self.autre_client
        with self.assertRaises(UserError):
            wiz.action_create_invoice()

    def test_projet_prive_pour_qui_facture(self):
        """Un projet que la personne qui facture ne voit pas compte quand même :
        c'est la banque qui décide, pas les droits de lecture."""
        self.projet_b.privacy_visibility = 'followers'
        self._jeu_postpaye()
        bank = self._banque(invoice_split_by_project=True)
        facturation = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Facturation seule', 'login': 'facturation-essai',
            'email': 'facturation-essai@example.test',
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('account.group_account_invoice').id,
                                  self.env.ref('project.group_project_user').id])],
        })
        self.assertFalse(self.projet_b.with_user(facturation).has_access('read'))
        form = Form(self.env['hour.bank.invoice.wizard'].with_user(facturation)
                    .with_context(default_bank_id=bank.id))
        self.assertAlmostEqual(form.uncovered_hours, 3.0, places=2)
        self.assertEqual(len(form.line_ids), 2)
        wiz = form.save()
        move = self.env['account.move'].browse(wiz.action_create_invoice()['res_id'])
        self.assertAlmostEqual(sum(move.invoice_line_ids.mapped('quantity')), 3.0, places=2)
        self.assertTrue(any('Projet B' in n for n in move.invoice_line_ids.mapped('name')))

    def test_correction_negative_couvre_les_heures_anterieures(self):
        """5 h puis une correction de −2 h, sans facture : 3 h à facturer, pas 5."""
        self._fdt(self.projet_a, 5.0, 10)
        self._fdt(self.projet_a, -2.0, 8)
        bank = self._banque()
        self.assertAlmostEqual(bank.current_balance, -3.0, places=2)
        wiz = self._assistant(bank)
        self.assertAlmostEqual(wiz.uncovered_hours, 3.0, places=2)
        self.assertAlmostEqual(wiz.total_hours, 3.0, places=2)

    def test_arrondi_ventile_egal_ligne_unique(self):
        self._fdt(self.projet_a, 1.006, 3)
        self._fdt(self.projet_b, 0.004, 2)
        bank = self._banque()
        unique = self._assistant(bank).total_hours
        wiz = self._assistant(bank, split_by_project=True)
        self.assertAlmostEqual(wiz.total_hours, unique, places=2)
        self.assertTrue(all(l.quantity > 0 for l in wiz.line_ids))

    def test_changer_de_produit_garde_les_lignes_corrigees(self):
        heures_bis = self.env['product.product'].create({'name': 'Accompagnement', 'type': 'service'})
        self._jeu_postpaye()
        bank = self._banque(filter_product_ids=[(6, 0, [self.heures.id, heures_bis.id])])
        form = Form(self.env['hour.bank.invoice.wizard'].with_context(default_bank_id=bank.id))
        with form.line_ids.edit(0) as ligne:
            ligne.quantity = 1.25
        form.product_id = heures_bis
        with form.line_ids.edit(0) as ligne:
            self.assertAlmostEqual(ligne.quantity, 1.25)

    def test_formulaire(self):
        """Le chemin de l'écran : défauts par le contexte, lignes en onchange."""
        self._jeu_postpaye()
        bank = self._banque()
        form = Form(self.env['hour.bank.invoice.wizard'].with_context(default_bank_id=bank.id))
        self.assertEqual(len(form.line_ids), 1)
        form.split_by_project = True
        self.assertEqual(len(form.line_ids), 2)
        with form.line_ids.edit(0) as ligne:
            ligne.quantity = 1.5
        wiz = form.save()
        self.assertAlmostEqual(wiz.total_hours, 2.5, places=2)

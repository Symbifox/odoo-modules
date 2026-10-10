from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import MenageCase

MODELS = [
    'personal.budget.book',
    'personal.budget.category',
    'personal.budget.contributor',
    'personal.budget.transaction',
    'personal.budget.plan',
    'personal.budget.recurring',
    'personal.budget.loan',
    'personal.budget.loan.line',
    'personal.budget.cheque',
    'personal.budget.invoice',
    'personal.budget.share.line',
]


@tagged('post_install', '-at_install', 'personal_budget_menage')
class TestIsolationMenage(MenageCase):

    def test_chaque_personne_recoit_un_budget_prive(self):
        book_a = self.env_a['personal.budget.book']._default_book()
        book_b = self.env_b['personal.budget.book']._default_book()
        self.assertTrue(book_a and book_b)
        self.assertNotEqual(book_a, book_b)
        self.assertEqual(book_a.user_id, self.user_a)
        self.assertFalse(book_a.member_ids)
        self.assertTrue(book_a.is_default)
        # Une deuxième visite ne recrée rien.
        self.assertEqual(self.env_a['personal.budget.book']._default_book(), book_a)
        # Une donnée créée sans nommer de budget va dans le budget par défaut.
        cat = self.env_a['personal.budget.category'].create({'name': 'Loyer', 'category_type': 'expense'})
        self.assertEqual(cat.book_id, book_a)

    def test_a_ne_voit_rien_de_b(self):
        data_b = self._seed_everything(self.env_b, 'B')
        data_a = self._seed_everything(self.env_a, 'A')
        for model in MODELS:
            with self.subTest(model=model):
                records_b = data_b[model]
                # L'essai mesure quelque chose : les lignes de B existent bien.
                self.assertEqual(len(records_b.sudo().exists()), len(records_b))
                visible = self.env_a[model].with_context(active_test=False).search([])
                self.assertFalse(visible & records_b.with_env(self.env_a),
                                 "A voit des %s de B" % model)
                self.assertTrue(visible & data_a[model].with_env(self.env_a),
                                "A ne voit plus ses propres %s" % model)
                with self.assertRaises(AccessError):
                    records_b.with_env(self.env_a).read(['display_name'])

    def test_a_ne_modifie_ni_ne_supprime_rien_de_b(self):
        data_b = self._seed_everything(self.env_b, 'B')
        writable = {
            'personal.budget.book': {'name': 'piraté'},
            'personal.budget.category': {'notes': 'piraté'},
            'personal.budget.contributor': {'name': 'piraté'},
            'personal.budget.transaction': {'details': 'piraté'},
            'personal.budget.plan': {'planned_amount': 1.0},
            'personal.budget.recurring': {'amount': 1.0},
            'personal.budget.loan': {'notes': 'piraté'},
            'personal.budget.loan.line': {'description': 'piraté'},
            'personal.budget.cheque': {'memo': 'piraté'},
            'personal.budget.invoice': {'description': 'piraté'},
            'personal.budget.share.line': {'description': 'piraté'},
        }
        for model, vals in writable.items():
            with self.subTest(model=model, op='write'):
                with self.assertRaises(AccessError):
                    data_b[model].with_env(self.env_a).write(vals)
        for model in reversed(MODELS):
            with self.subTest(model=model, op='unlink'):
                with self.assertRaises(AccessError):
                    data_b[model].with_env(self.env_a).unlink()

    def test_a_ne_peut_rien_glisser_dans_le_budget_de_b(self):
        data_b = self._seed_everything(self.env_b, 'B')
        book_b = data_b['personal.budget.book']
        cat_b = data_b['personal.budget.category'][0]
        loan_b = data_b['personal.budget.loan']
        E = self.env_a
        # Créer directement dans le budget de B
        with self.assertRaises(AccessError):
            E['personal.budget.category'].create({'name': 'X', 'category_type': 'expense', 'book_id': book_b.id})
        with self.assertRaises(AccessError):
            E['personal.budget.cheque'].create({'number': 'X', 'book_id': book_b.id})
        # Créer en visant une catégorie ou un prêt de B (le budget suit le parent)
        with self.assertRaises(AccessError):
            E['personal.budget.transaction'].create(
                {'date': '2031-01-01', 'category_id': cat_b.id, 'gross_amount': 1.0})
        with self.assertRaises(AccessError):
            E['personal.budget.recurring'].create(
                {'name': 'X', 'category_id': cat_b.id, 'amount': 1.0, 'date_start': '2031-01-01'})
        with self.assertRaises(AccessError):
            E['personal.budget.loan.line'].create({'loan_id': loan_b.id, 'amount': 1.0})
        # Déplacer après coup : le contrôle d'Odoo passe AVANT l'écriture.
        own = self._seed_everything(self.env_a, 'A')
        with self.assertRaises(AccessError):
            own['personal.budget.category'][0].write({'book_id': book_b.id})
        with self.assertRaises(AccessError):
            own['personal.budget.share.line'].write({'book_id': book_b.id})
        # (la transaction sans contributeur : sinon la garde « même budget que
        # le contributeur » refuserait avant la règle, et l'essai ne mesurerait
        # pas la règle)
        tx_sans_contributeur = own['personal.budget.transaction'].filtered(lambda t: not t.contributor_id)
        self.assertTrue(tx_sans_contributeur)
        with self.assertRaises(AccessError):
            tx_sans_contributeur.write({'category_id': cat_b.id})
        with self.assertRaises(AccessError):
            own['personal.budget.loan.line'].write({'loan_id': loan_b.id})
        # Écrire le budget d'une transaction remonte à sa catégorie (champ lié) :
        # la catégorie de A partirait chez B. Refusé, et rien n'a bougé.
        tx = own['personal.budget.transaction'][0]
        with self.assertRaises(AccessError):
            tx.write({'book_id': book_b.id})
        self.assertEqual(tx.book_id, own['personal.budget.book'])
        self.assertEqual(tx.category_id.book_id, own['personal.budget.book'])
        # Rien n'a bougé du côté de B.
        self.assertEqual(
            self.env_b['personal.budget.category'].search_count([('book_id', '=', book_b.id)]), 2)

    def test_a_ne_peut_pas_s_inviter_ni_creer_au_nom_de_b(self):
        book_b = self.env_b['personal.budget.book']._default_book()
        with self.assertRaises(AccessError):
            book_b.with_env(self.env_a).write({'member_ids': [(4, self.user_a.id)]})
        with self.assertRaises(AccessError):
            self.env_a['personal.budget.book'].create({'name': 'Au nom de B', 'user_id': self.user_b.id})
        own = self.env_a['personal.budget.book'].create({'name': 'À céder'})
        with self.assertRaises(AccessError):
            own.write({'user_id': self.user_b.id})

    def test_budget_partage_expres_visible_de_b_et_seulement_de_b(self):
        prive_a = self._seed_everything(self.env_a, 'A')
        couple = self.env_a['personal.budget.book'].create({
            'name': 'Budget de couple', 'member_ids': [(6, 0, [self.user_b.id])],
        })
        self.assertTrue(couple.is_shared)
        commun = self._seed_everything(self.env_a, 'commun', book=couple)

        # B voit tout le budget commun…
        for model in MODELS:
            with self.subTest(model=model, qui='B'):
                seen = self.env_b[model].search([]) & commun[model].with_env(self.env_b)
                self.assertEqual(seen, commun[model].with_env(self.env_b))
        # …et y travaille : saisir une transaction, éditer une dépense récurrente.
        cat_commune = commun['personal.budget.category'][0].with_env(self.env_b)
        tx_b = self.env_b['personal.budget.transaction'].create(
            {'date': '2031-03-01', 'category_id': cat_commune.id, 'gross_amount': 55.0})
        self.assertEqual(tx_b.book_id, couple.with_env(self.env_b))
        commun['personal.budget.recurring'].with_env(self.env_b).write({'amount': 22.0})
        # A voit ce que B a saisi dans le budget commun.
        self.assertIn(tx_b.with_env(self.env_a), self.env_a['personal.budget.transaction'].search([]))

        # B ne voit toujours rien du budget PRIVÉ de A.
        for model in MODELS:
            with self.subTest(model=model, qui='B-prive'):
                self.assertFalse(self.env_b[model].search([]) & prive_a[model].with_env(self.env_b))

        # C, du même ménage mais non invitée, ne voit rien du tout.
        for model in MODELS:
            with self.subTest(model=model, qui='C'):
                self.assertFalse(self.env_c[model].search([]) & commun[model].with_env(self.env_c))
                self.assertFalse(self.env_c[model].search([]) & prive_a[model].with_env(self.env_c))
        with self.assertRaises(AccessError):
            couple.with_env(self.env_c).read(['name'])

    def test_seule_la_personne_proprietaire_gere_le_partage(self):
        couple = self.env_a['personal.budget.book'].create({
            'name': 'Budget de couple', 'member_ids': [(6, 0, [self.user_b.id])],
        })
        as_b = couple.with_env(self.env_b)
        self.assertEqual(as_b.name, 'Budget de couple')
        with self.assertRaises(AccessError):
            as_b.write({'member_ids': [(4, self.user_c.id)]})
        with self.assertRaises(AccessError):
            as_b.write({'name': 'renommé par B'})
        with self.assertRaises(AccessError):
            as_b.unlink()
        # A retire B : l'accès tombe.
        cat = self.env_a['personal.budget.category'].create(
            {'name': 'Épicerie', 'category_type': 'expense', 'book_id': couple.id})
        self.assertTrue(cat.with_env(self.env_b).read(['name']))
        couple.write({'member_ids': [(5, 0, 0)]})
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            cat.with_env(self.env_b).read(['name'])

    def test_partage_limite_aux_comptes_internes(self):
        portal = self.env['res.users'].create({
            'name': 'Portail', 'login': 'menage_portail',
            'groups_id': [(6, 0, [self.env.ref('base.group_portal').id])],
        })
        with self.assertRaises(ValidationError):
            self.env_a['personal.budget.book'].create({'name': 'X', 'member_ids': [(6, 0, [portal.id])]})
        with self.assertRaises(ValidationError):
            self.env_a['personal.budget.book'].create({'name': 'X', 'member_ids': [(6, 0, [self.user_a.id])]})

    def test_meme_nom_de_categorie_dans_deux_budgets(self):
        """L'unicité est par budget : elle ne bloque pas B et ne lui apprend rien de A."""
        self.env_a['personal.budget.category'].create({'name': 'Épicerie', 'category_type': 'expense'})
        cat_b = self.env_b['personal.budget.category'].create({'name': 'Épicerie', 'category_type': 'expense'})
        self.assertTrue(cat_b)

    def test_meme_fitid_dans_deux_budgets(self):
        """Compte conjoint importé par les deux personnes, chacune chez elle."""
        for env in (self.env_a, self.env_b):
            cat = env['personal.budget.category'].create({'name': 'À attribuer', 'category_type': 'expense'})
            env['personal.budget.transaction'].create({
                'date': '2031-01-01', 'category_id': cat.id, 'gross_amount': 9.0,
                'account_ref': 'CONJOINT', 'fitid': 'F1',
            })

    def test_contributeur_d_un_autre_budget_refuse(self):
        couple = self.env_a['personal.budget.book'].create({'name': 'Couple'})
        contrib_couple = self.env_a['personal.budget.contributor'].create({'name': 'X', 'book_id': couple.id})
        cat = self.env_a['personal.budget.category'].create({'name': 'Épicerie', 'category_type': 'expense'})
        with self.assertRaises(ValidationError):
            self.env_a['personal.budget.transaction'].create({
                'date': '2031-01-01', 'category_id': cat.id, 'gross_amount': 1.0,
                'contributor_id': contrib_couple.id,
            })

    def test_budget_avec_donnees_ne_se_supprime_pas(self):
        couple = self.env_a['personal.budget.book'].create({'name': 'Couple'})
        self.env_a['personal.budget.category'].create(
            {'name': 'Épicerie', 'category_type': 'expense', 'book_id': couple.id})
        with self.assertRaises(UserError):
            couple.unlink()

    def test_registre_de_partage_solde_par_budget(self):
        E_a, E_b = self.env_a, self.env_b
        E_a['personal.budget.share.line'].create([{'description': 'a1', 'amount': 100.0},
                                                  {'description': 'a2', 'amount': -30.0}])
        lines_b = E_b['personal.budget.share.line'].create([{'description': 'b1', 'amount': 5.0}])
        self.assertEqual(lines_b.running_balance, 5.0)
        a2 = E_a['personal.budget.share.line'].search([('description', '=', 'a2')])
        self.assertEqual(a2.running_balance, 70.0)

    def test_analyse_isolee(self):
        self._seed_everything(self.env_b, 'B')
        data_a = self._seed_everything(self.env_a, 'A')
        book_b = self.env_b['personal.budget.book']._default_book()
        Analysis = self.env['personal.budget.analysis']
        self.assertTrue(Analysis.sudo().search_count([('book_id', '=', book_b.id)]))
        rows = self.env_a['personal.budget.analysis'].search_read([], ['book_id'])
        self.assertTrue(rows)
        self.assertEqual({r['book_id'][0] for r in rows}, {data_a['personal.budget.book'].id})

    def test_tableau_de_bord_isole(self):
        self._seed_everything(self.env_b, 'B')
        data_a = self._seed_everything(self.env_a, 'A')
        book_b = self.env_b['personal.budget.book']._default_book()
        Dash_a = self.env_a['personal.budget.dashboard']
        # Viser le budget de B par RPC : refusé.
        with self.assertRaises(AccessError):
            Dash_a.get_dashboard_data(2031, book_b.id)
        with self.assertRaises(AccessError):
            Dash_a.get_available_years(book_b.id)
        # Le tableau de bord de A ne compte que A.
        data = Dash_a.get_dashboard_data(2031)
        self.assertEqual(data['book']['id'], data_a['personal.budget.book'].id)
        self.assertEqual(data['summary']['total_expense_real'], 80.0)
        self.assertEqual(data['summary']['total_income_real'], 1000.0)
        names = {r['category_name'] for r in data['expense_rows'] + data['income_rows']}
        self.assertEqual(names, {'Épicerie A', 'Salaire A'})
        self.assertEqual(data['invoice_summary']['total_invoices'], 1)
        self.assertEqual([l['name'] for l in data['loan_balances']], ['Prêt A'])
        self.assertTrue(all(t['details'] != 'marché B' for t in data['recent_transactions']))
        self.assertEqual(data['recurring']['count'], 1)
        # La liste des budgets offerts à A ne montre pas celui de B.
        ids = {b['id'] for b in Dash_a.get_available_books()}
        self.assertNotIn(book_b.id, ids)

    def test_annees_offertes_isolees(self):
        """Le sélecteur d'années ne révèle pas les années où B seule a des données."""
        cat_b = self.env_b['personal.budget.category'].create({'name': 'Vieux', 'category_type': 'expense'})
        self.env_b['personal.budget.transaction'].create(
            {'date': '2019-06-01', 'category_id': cat_b.id, 'gross_amount': 1.0})
        self._seed_everything(self.env_a, 'A')
        years_a = self.env_a['personal.budget.dashboard'].get_available_years()
        self.assertIn(2031, years_a)
        self.assertNotIn(2019, years_a)
        self.assertIn(2019, self.env_b['personal.budget.dashboard'].get_available_years())

    def test_tableau_de_bord_du_budget_commun(self):
        couple = self.env_a['personal.budget.book'].create({
            'name': 'Budget de couple', 'member_ids': [(6, 0, [self.user_b.id])],
        })
        self._seed_everything(self.env_a, 'commun', book=couple)
        self._seed_everything(self.env_a, 'A')
        data = self.env_b['personal.budget.dashboard'].get_dashboard_data(2031, couple.id)
        self.assertEqual(data['summary']['total_expense_real'], 80.0)
        self.assertEqual({r['category_name'] for r in data['expense_rows']}, {'Épicerie commun'})
        ids = {b['id'] for b in self.env_b['personal.budget.dashboard'].get_available_books()}
        self.assertIn(couple.id, ids)
        with self.assertRaises(AccessError):
            self.env_c['personal.budget.dashboard'].get_dashboard_data(2031, couple.id)

    def test_import_range_dans_le_budget_choisi(self):
        import base64
        couple = self.env_a['personal.budget.book'].create({'name': 'Couple'})
        self.env_a['personal.budget.category'].create({'name': 'Épicerie', 'category_type': 'expense'})
        csv = "date,categorie,type,montant\n2031-01-05,Épicerie,E,12.50\n"
        wizard = self.env_a['personal.budget.import.wizard'].create({
            'book_id': couple.id, 'import_type': 'transaction',
            'csv_file': base64.b64encode(csv.encode()),
        })
        wizard.action_import()
        tx = self.env_a['personal.budget.transaction'].search([('gross_amount', '=', 12.5)])
        self.assertEqual(tx.book_id, couple)
        self.assertEqual(tx.category_id.book_id, couple, "la catégorie du budget privé a été prise")

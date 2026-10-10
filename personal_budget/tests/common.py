from odoo.tests import TransactionCase, new_test_user


class MenageCase(TransactionCase):
    """Un ménage de trois personnes, aucune administratrice.

    A et B forment le couple ; C est une troisième personne de la même base,
    témoin de ce qui ne doit PAS fuir quand A partage avec B.
    Les essais jouent chaque parcours DANS LE RÔLE (`with_user`), jamais en
    superutilisateur : un essai en administrateur ne mesure pas les règles.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = 'base.group_user,personal_budget.group_budget_user'
        cls.user_a = new_test_user(cls.env, login='menage_a', name="Personne A", groups=groups)
        cls.user_b = new_test_user(cls.env, login='menage_b', name="Personne B", groups=groups)
        cls.user_c = new_test_user(cls.env, login='menage_c', name="Personne C", groups=groups)
        for user in (cls.user_a, cls.user_b, cls.user_c):
            assert not user.has_group('base.group_system'), "une personne d'essai est administratrice"
        cls.env_a = cls.env(user=cls.user_a)
        cls.env_b = cls.env(user=cls.user_b)
        cls.env_c = cls.env(user=cls.user_c)

    @staticmethod
    def _seed_everything(env, suffix, book=None):
        """Une donnée de chaque modèle du budget, créée dans le rôle de `env`."""
        book = book or env['personal.budget.book']._default_book()
        vals_book = {'book_id': book.id}
        cat = env['personal.budget.category'].create(
            dict(vals_book, name='Épicerie ' + suffix, category_type='expense'))
        rev = env['personal.budget.category'].create(
            dict(vals_book, name='Salaire ' + suffix, category_type='revenue'))
        contributor = env['personal.budget.contributor'].create(
            dict(vals_book, name='Coloc ' + suffix))
        tx = env['personal.budget.transaction'].create({
            'date': '2031-02-03', 'category_id': cat.id, 'gross_amount': 80.0,
            'details': 'marché ' + suffix, 'contributor_id': contributor.id,
        })
        tx_rev = env['personal.budget.transaction'].create({
            'date': '2031-02-04', 'category_id': rev.id, 'gross_amount': 1000.0,
        })
        plan = env['personal.budget.plan'].create({
            'year': 2031, 'month': 0, 'category_id': cat.id, 'planned_amount': 1200.0,
        })
        recurring = env['personal.budget.recurring'].create({
            'name': 'Diffusion ' + suffix, 'category_id': cat.id, 'amount': 20.0,
            'frequency': 'monthly', 'date_start': '2031-01-15',
        })
        loan = env['personal.budget.loan'].create(dict(vals_book, name='Prêt ' + suffix, initial_amount=5000.0))
        loan_line = env['personal.budget.loan.line'].create(
            {'loan_id': loan.id, 'date': '2031-02-01', 'amount': -250.0})
        cheque = env['personal.budget.cheque'].create(dict(vals_book, number='C-' + suffix, amount=42.0))
        invoice = env['personal.budget.invoice'].create(dict(
            vals_book, invoice_number='F-' + suffix, invoice_date='2031-02-01', pretax_amount=900.0))
        share = env['personal.budget.share.line'].create(dict(
            vals_book, description='avance ' + suffix, amount=30.0))
        return {
            'personal.budget.book': book,
            'personal.budget.category': cat | rev,
            'personal.budget.contributor': contributor,
            'personal.budget.transaction': tx | tx_rev,
            'personal.budget.plan': plan,
            'personal.budget.recurring': recurring,
            'personal.budget.loan': loan,
            'personal.budget.loan.line': loan_line,
            'personal.budget.cheque': cheque,
            'personal.budget.invoice': invoice,
            'personal.budget.share.line': share,
        }

"""Relecture adverse de la 18.0.2.1.0 : chaque trouvaille fermée, prouvée par son essai.

Une garde se prouve en refusant POUR LA BONNE RAISON : chaque essai vérifie le
message, et ceux qui visent une porte précise sont bâtis pour échouer si on la
retire (fichier vide pour la garde d'entrée de l'import, création simulée pour
les points de sauvegarde). Données inventées.
"""
import base64
from unittest.mock import MagicMock, patch

from odoo.addons.base.models import res_users
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from .common import MenageCase

REFUS = "This budget does not exist or is not shared with you."
SECRET = "Thérapie secrète"
NOMS_DE_B = (SECRET, "Budget secret de B", "Diffusion B", "Prêt B", "Coloc B")


def _csv(*lignes):
    texte = "date,categorie,type,montant\n" + "".join(l + "\n" for l in lignes)
    return base64.b64encode(texte.encode())


@tagged('post_install', '-at_install')
class TestRelecture(MenageCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Les messages se comparent en anglais, la langue source, même quand la
        # base porte fr_CA (sinon le refus sort traduit).
        cls.env = cls.env(context=dict(cls.env.context, lang='en_US'))
        (cls.user_a | cls.user_b | cls.user_c).write({'lang': 'en_US'})
        cls.env_a = cls.env(user=cls.user_a)
        cls.env_b = cls.env(user=cls.user_b)
        cls.env_c = cls.env(user=cls.user_c)
        cls.data_b = cls._seed_everything(cls.env_b, 'B')
        cls.book_b = cls.data_b['personal.budget.book']
        cls.book_b.write({'name': "Budget secret de B"})
        cls.cat_b = cls.env_b['personal.budget.category'].create(
            {'name': SECRET, 'category_type': 'expense', 'book_id': cls.book_b.id})
        cls.recurring_b = cls.data_b['personal.budget.recurring']
        cls.loan_b = cls.data_b['personal.budget.loan']

    def _rien_de_c_chez_b(self):
        for model in ('personal.budget.category', 'personal.budget.transaction',
                      'personal.budget.share.line', 'personal.budget.cheque',
                      'personal.budget.invoice', 'personal.budget.plan'):
            self.assertFalse(self.env[model].sudo().search(
                [('book_id', '=', self.book_b.id), ('create_uid', '=', self.user_c.id)]), model)

    # ---------------------------------------------------------- 1. assistants
    def test_1_l_assistant_d_import_est_prive(self):
        w = self.env_a['personal.budget.import.wizard'].create({
            'import_type': 'transaction', 'csv_file': _csv("2031-01-05,Épicerie,E,12.50"),
            'csv_filename': 'releve.csv',
        })
        self.assertFalse(self.env_c['personal.budget.import.wizard'].search([('id', '=', w.id)]))
        # Sans la règle, rien d'autre ne refuserait : le groupe Budget lit les assistants.
        with self.assertRaises(AccessError):
            w.with_user(self.user_c).read(['csv_file', 'csv_filename', 'preview'])
        # Le fichier lui-même, par sa pièce jointe.
        piece = self.env['ir.attachment'].sudo().search([
            ('res_model', '=', w._name), ('res_id', '=', w.id), ('res_field', '=', 'csv_file')])
        self.assertTrue(piece, "précondition : le fichier est une pièce jointe")
        with self.assertRaises(AccessError):
            piece.with_user(self.user_c).read(['datas'])
        self.assertEqual(w.with_user(self.user_a).read(['csv_filename'])[0]['csv_filename'], 'releve.csv')

    def test_1_l_assignation_en_lot_est_privee(self):
        cat = self.env_a['personal.budget.category'].create({'name': 'Épicerie', 'category_type': 'expense'})
        w = self.env_a['personal.budget.bulk.category'].create({'category_id': cat.id})
        self.assertFalse(self.env_c['personal.budget.bulk.category'].search([('id', '=', w.id)]))
        with self.assertRaises(AccessError):
            w.with_user(self.user_c).read(['category_id'])

    # ---------------------------------------------------- 2. import chez autrui
    def test_2_l_assistant_ne_vise_pas_le_budget_d_autrui(self):
        Wiz = self.env_c['personal.budget.import.wizard']
        with self.assertRaisesRegex(AccessError, REFUS):
            Wiz.create({'book_id': self.book_b.id, 'import_type': 'transaction',
                        'csv_file': _csv("2031-01-05,Épicerie,E,12.50")})
        w = Wiz.create({'import_type': 'transaction', 'csv_file': _csv("2031-01-05,Épicerie,E,12.50")})
        with self.assertRaisesRegex(AccessError, REFUS):
            w.write({'book_id': self.book_b.id})
        with self.assertRaisesRegex(AccessError, REFUS):
            w.write({'loan_id': self.loan_b.id})
        self._rien_de_c_chez_b()

    def test_2_l_import_refuse_avant_de_lire_le_fichier(self):
        # Fichier vide : sans la garde d'entrée, l'import ne ferait rien et ne
        # refuserait rien. Le refus prouve donc la garde elle-même.
        w = self.env_c['personal.budget.import.wizard'].create(
            {'import_type': 'transaction', 'csv_file': _csv()})
        self.env.cr.execute(
            "UPDATE personal_budget_import_wizard SET book_id = %s WHERE id = %s", (self.book_b.id, w.id))
        w.invalidate_recordset()
        with self.assertRaisesRegex(AccessError, REFUS):
            w.action_import()
        self._rien_de_c_chez_b()

    def test_2_une_erreur_d_acces_n_est_jamais_avalee(self):
        w = self.env_c['personal.budget.import.wizard'].create(
            {'import_type': 'transaction', 'csv_file': _csv("2031-01-05,Épicerie,E,12.50")})
        Tx = type(self.env['personal.budget.transaction'])

        def refuse(recs, vals_list):
            raise AccessError("refus de règle simulé")
        with patch.object(Tx, 'create', refuse), self.assertRaisesRegex(AccessError, "simulé"):
            w.action_import()

    def test_2_une_ligne_qui_echoue_en_sql_n_emporte_qu_elle(self):
        w = self.env_c['personal.budget.import.wizard'].create({
            'import_type': 'transaction',
            'csv_file': _csv("2031-01-05,Épicerie,E,1", "2031-01-06,Épicerie,E,2", "2031-01-07,Épicerie,E,3"),
        })
        Tx = type(self.env['personal.budget.transaction'])
        origine = Tx.create
        appels = []

        def create(recs, vals_list):
            appels.append(1)
            if len(appels) == 2:
                recs.env.cr.execute("SELECT 1/0")
            return origine(recs, vals_list)
        with patch.object(Tx, 'create', create):
            w.action_import()
        self.assertIn("2 record(s) created", w.result)
        self.assertIn("1 error(s)", w.result)
        montants = self.env_c['personal.budget.transaction'].search(
            [('category_id.name', '=', 'Épicerie'), ('create_uid', '=', self.user_c.id)]).mapped('gross_amount')
        self.assertEqual(sorted(montants), [1.0, 3.0])

    # --------------------------------------------------------- 3. mode debug
    def test_3_en_debug_l_erreur_d_acces_ne_nomme_rien(self):
        # Le mode debug : membre de base.group_no_one ET session en debug.
        self.user_c.sudo().write({'groups_id': [(4, self.env.ref('base.group_no_one').id)]})
        session = MagicMock()
        session.session.debug = "1"
        for rec in (self.book_b, self.cat_b, self.recurring_b, self.loan_b):
            with patch.object(res_users, "request", session), self.assertRaises(AccessError) as refus:
                rec.with_user(self.user_c).read(['id'])
            message = str(refus.exception)
            self.assertIn("%s: %d" % (rec._name, rec.id), message,
                          "précondition : en debug, le message liste les fiches refusées")
            for nom in NOMS_DE_B:
                self.assertNotIn(nom, message)

    # ------------------------------------------- 4. noms à travers un many2one
    def test_4_la_depense_recurrente_d_autrui_est_refusee_puis_neutre(self):
        own = self._seed_everything(self.env_c, 'C')
        tx = own['personal.budget.transaction'].filtered(lambda t: not t.contributor_id)[:1]
        with self.assertRaisesRegex(AccessError, REFUS):
            tx.write({'recurring_id': self.recurring_b.id})
        # Posée par une autre voie (ancienne donnée, SQL) : le nom reste neutre.
        self.env.cr.execute("UPDATE personal_budget_transaction SET recurring_id = %s WHERE id = %s",
                            (self.recurring_b.id, tx.id))
        tx.invalidate_recordset()
        self.env['personal.budget.recurring'].invalidate_model()
        lu = tx.with_user(self.user_c).read(['recurring_id'])[0]['recurring_id']
        self.assertEqual(lu[1], "Private budget entry")
        groupes = self.env_c['personal.budget.transaction'].read_group(
            [('id', '=', tx.id)], ['gross_amount:sum'], ['recurring_id'])
        self.assertEqual(groupes[0]['recurring_id'][1], "Private budget entry")

    def test_4_la_depense_recurrente_d_un_autre_de_ses_budgets_est_refusee(self):
        couple = self.env_a['personal.budget.book'].create({'name': 'Couple'})
        cat_couple = self.env_a['personal.budget.category'].create(
            {'name': 'Loisirs', 'category_type': 'expense', 'book_id': couple.id})
        recurring = self.env_a['personal.budget.recurring'].create({
            'name': 'Abonnement du couple', 'category_id': cat_couple.id, 'amount': 9.0,
            'frequency': 'monthly', 'date_start': '2031-01-01'})
        cat = self.env_a['personal.budget.category'].create({'name': 'Épicerie', 'category_type': 'expense'})
        with self.assertRaises(ValidationError) as refus:
            self.env_a['personal.budget.transaction'].create({
                'date': '2031-01-02', 'category_id': cat.id, 'gross_amount': 1.0,
                'recurring_id': recurring.id})
        self.assertIn("different budget", str(refus.exception))
        self.assertNotIn("Abonnement du couple", str(refus.exception))

    def test_4_les_assistants_ne_rendent_pas_le_nom_d_autrui(self):
        w = self.env_c['personal.budget.import.wizard'].create(
            {'import_type': 'transaction', 'csv_file': _csv()})
        self.env.cr.execute("UPDATE personal_budget_import_wizard SET book_id = %s WHERE id = %s",
                            (self.book_b.id, w.id))
        w.invalidate_recordset()
        self.env['personal.budget.book'].invalidate_model()
        self.assertEqual(w.with_user(self.user_c).read(['book_id'])[0]['book_id'][1], "Private budget")
        with self.assertRaisesRegex(AccessError, REFUS):
            self.env_c['personal.budget.bulk.category'].create({'category_id': self.cat_b.id})

    def test_4_onchange_refuse_une_cible_d_autrui(self):
        spec = {'category_id': {}, 'book_id': {'fields': {'display_name': {}}}, 'category_type': {}}
        with self.assertRaisesRegex(AccessError, REFUS):
            self.env_c['personal.budget.transaction'].onchange(
                {'category_id': self.cat_b.id}, ['category_id'], spec)
        with self.assertRaisesRegex(AccessError, REFUS):
            self.env_c['personal.budget.loan.line'].onchange(
                {'loan_id': self.loan_b.id}, ['loan_id'], {'loan_id': {}, 'book_id': {}})

    # ----------------------------------------- 5. message d'une contrainte
    def test_5_le_message_de_contrainte_ne_nomme_pas_la_cible(self):
        couple = self.env_a['personal.budget.book'].create({'name': 'Couple'})
        contrib = self.env_a['personal.budget.contributor'].create(
            {'name': 'Ex-conjoint secret', 'book_id': couple.id})
        cat = self.env_a['personal.budget.category'].create({'name': 'Épicerie', 'category_type': 'expense'})
        with self.assertRaises(ValidationError) as refus:
            self.env_a['personal.budget.transaction'].create({
                'date': '2031-01-01', 'category_id': cat.id, 'gross_amount': 1.0,
                'contributor_id': contrib.id})
        self.assertIn("different budget", str(refus.exception))
        self.assertNotIn("Ex-conjoint", str(refus.exception))

    # ---------------------------------------------- 6. oracle d'existence
    def test_6_meme_refus_que_la_fiche_existe_ou_non(self):
        Cat = self.env_c['personal.budget.category']
        messages = []
        for nom in (SECRET, "Nom inventé"):
            with self.assertRaises(AccessError) as refus:
                Cat.create({'name': nom, 'category_type': 'expense', 'book_id': self.book_b.id})
            messages.append(str(refus.exception))
        self.assertEqual(messages[0], messages[1])
        self.assertIn(REFUS, messages[0])
        Plan = self.env_c['personal.budget.plan']
        with self.assertRaisesRegex(AccessError, REFUS):
            Plan.create({'year': 2031, 'month': 0, 'category_id': self.data_b['personal.budget.category'][0].id,
                         'planned_amount': 1.0})

    def test_6_par_les_defauts_du_contexte_et_personnels(self):
        Cat = self.env_c['personal.budget.category']
        with self.assertRaisesRegex(AccessError, REFUS):
            Cat.with_context(default_book_id=self.book_b.id).create({'name': SECRET, 'category_type': 'expense'})
        self.env['ir.default'].with_user(self.user_c).set(
            'personal.budget.category', 'book_id', self.book_b.id, user_id=True)
        with self.assertRaisesRegex(AccessError, REFUS):
            Cat.create({'name': SECRET, 'category_type': 'expense'})
        self._rien_de_c_chez_b()

    # ------------------------------------------------ 11. tableau de bord
    def test_11_le_tableau_de_bord_refuse_pareil(self):
        Dash = self.env_c['personal.budget.dashboard']
        messages = []
        for book_id in (self.book_b.id, 999999):
            with self.assertRaises(AccessError) as refus:
                Dash._resolve_book(book_id)
            messages.append(str(refus.exception))
        self.assertEqual(messages[0], messages[1])
        self.assertIn(REFUS, messages[0])

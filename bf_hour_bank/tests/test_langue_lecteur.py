"""Ce que le client reçoit suit SA langue, pas celle de qui l'envoie.

Avant la bascule, la source était française : le classeur, les deux courriels et
leur habillage étaient écrits en dur, en français pour tout le monde. Ils passent
maintenant par le catalogue, et chaque envoi se fait dans la langue du
destinataire. Ces essais se jouent dans les DEUX langues, sur une base où en_US
et fr_CA sont actives toutes les deux, et ne supposent pas la langue de la base.
"""
import io
from datetime import date, timedelta

from odoo.tests import TransactionCase, tagged

try:
    import openpyxl
except ImportError:  # pragma: no cover
    openpyxl = None


@tagged('post_install', '-at_install', 'bf_hour_bank')
class TestLangueDuLecteur(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Lang = cls.env['res.lang']
        for code in ('en_US', 'fr_CA'):
            Lang._activate_lang(code)
        cls.env['ir.module.module']._load_module_terms(
            ['bf_hour_bank'], ['fr_CA'], overwrite=True)
        cls.client = cls.env['res.partner'].create({
            'name': 'Client Langue', 'is_company': True, 'lang': 'fr_CA',
            'email': 'client@langue.invalid',
        })
        cls.lecteur_fr = cls.env['res.partner'].create({
            'name': 'Lectrice FR', 'email': 'fr@langue.invalid', 'lang': 'fr_CA',
        })
        cls.lecteur_en = cls.env['res.partner'].create({
            'name': 'Reader EN', 'email': 'en@langue.invalid', 'lang': 'en_US',
        })
        cls.projet = cls.env['project.project'].create({
            'name': 'Projet Langue', 'allow_timesheets': True,
            'partner_id': cls.client.id,
        })
        cls.produit = cls.env['product.product'].create({
            'name': 'Heures langue', 'type': 'service', 'list_price': 100.0,
        })
        cls.journal = cls.env['account.journal'].search(
            [('type', '=', 'sale'), ('company_id', '=', cls.env.company.id)], limit=1)
        cls.employe = cls.env.ref('hr.employee_admin', raise_if_not_found=False) \
            or cls.env['hr.employee'].create({'name': 'Emp Langue'})
        cls.banque = cls.env['hour.bank.client'].create({
            'partner_id': cls.client.id,
            'project_ids': [(6, 0, [cls.projet.id])],
            'recipient_ids': [(6, 0, [cls.lecteur_fr.id, cls.lecteur_en.id])],
            'notify_internal_followers': False,
        })
        cls.env['account.analytic.line'].create({
            'name': 'FDT langue', 'date': date.today() - timedelta(days=3),
            'unit_amount': 5.0, 'project_id': cls.projet.id,
            'employee_id': cls.employe.id,
        })
        facture = cls.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': cls.client.id,
            'invoice_date': date.today() - timedelta(days=1),
            'journal_id': cls.journal.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': cls.produit.id, 'quantity': 2.0, 'price_unit': 100.0,
            })],
        })
        facture.action_post()
        cls.env['hour.bank.adjustment'].create({
            'hour_bank_id': cls.banque.id, 'hours': 1.0, 'description': 'Geste',
            'date': date.today() - timedelta(days=2),
        })

    def setUp(self):
        super().setUp()
        # Les courriels restent en file : on lit ce qui a été composé.
        self.patch(type(self.env['mail.mail']), 'send', lambda self, *a, **k: True)

    def _courriels(self):
        return self.env['mail.mail'].search(
            [('recipient_ids', 'in', (self.lecteur_fr | self.lecteur_en).ids)])

    def test_libelles_des_credits_dans_la_langue_du_contexte(self):
        """Les deux regroupements écrits en dur dans le SQL se traduisent."""
        for lang, facture, ajustement in (
                ('fr_CA', 'Heures facturées', 'Ajustement'),
                ('en_US', 'Billed hours', 'Adjustment')):
            projets = {e['project'] for e in
                       self.banque.with_context(lang=lang)._get_report_data()['entries']}
            self.assertIn(facture, projets, lang)
            self.assertIn(ajustement, projets, lang)

    def test_classeur_dans_les_deux_langues(self):
        if not openpyxl:
            self.skipTest("openpyxl absent")
        for lang, onglets, entete in (
                ('fr_CA', ['Feuilles de temps', 'Sommaire par projet', 'Synthèse par mois', 'À facturer'],
                 "Nombre d'heures"),
                ('en_US', ['Timesheets', 'Per-project summary', 'Monthly summary', 'To bill'],
                 'Number of hours')):
            classeur = openpyxl.load_workbook(io.BytesIO(
                self.banque._generate_xlsx_binary(lang=lang)))
            self.assertEqual(classeur.sheetnames, onglets, lang)
            self.assertEqual(classeur.worksheets[0].cell(row=1, column=2).value, entete, lang)

    def test_rapport_automatique_un_courriel_par_langue(self):
        self.banque._send_report_to_recipients()
        courriels = self._courriels()
        fr = courriels.filtered(lambda m: self.lecteur_fr in m.recipient_ids)
        en = courriels.filtered(lambda m: self.lecteur_en in m.recipient_ids)
        self.assertEqual(len(fr), 1)
        self.assertEqual(len(en), 1)
        self.assertEqual(fr.subject, "État des banques d'heures — Client Langue")
        self.assertEqual(en.subject, "Hour bank statement — Client Langue")
        self.assertIn('Veuillez trouver ci-joint', fr.body_html)
        self.assertIn('Pour toute question', fr.body_html)
        self.assertIn('Please find attached', en.body_html)
        self.assertIn('If you have any questions', en.body_html)
        self.assertNotIn('Bonjour', en.body_html)
        # L'habillage aussi (titre, signature, liens légaux) : rien de français
        # chez le lecteur anglais, apostrophe échappée ou non.
        self.assertNotIn('Banque d', en.body_html)
        self.assertIn('Hour bank by', en.body_html)
        self.assertIn("Banque d", fr.body_html)
        # Les pièces jointes suivent la langue du courriel qui les porte.
        self.assertTrue(all(a.name.startswith('Banque_heures_') for a in fr.attachment_ids))
        self.assertTrue(all(a.name.startswith('Hour_bank_') for a in en.attachment_ids))

    def test_alerte_de_palier_un_courriel_par_langue(self):
        self.banque.write({'threshold_mode': 'unbilled'})
        self.env['hour.bank.threshold.line'].create({
            'bank_id': self.banque.id, 'value': 1.0})
        self.env['account.analytic.line'].create({
            'name': 'FDT palier', 'date': date.today(), 'unit_amount': 4.0,
            'project_id': self.projet.id, 'employee_id': self.employe.id,
        })
        self.assertEqual(self.banque._check_thresholds(), 1)
        courriels = self._courriels()
        fr = courriels.filtered(lambda m: self.lecteur_fr in m.recipient_ids)
        en = courriels.filtered(lambda m: self.lecteur_en in m.recipient_ids)
        self.assertIn('palier « 1.0h non facturées » atteint', fr.subject)
        self.assertIn('threshold "1.0h unbilled" reached', en.subject)
        self.assertIn('Mesure actuelle', fr.body_html)
        self.assertIn('Current measure', en.body_html)
        self.assertIn('non facturées', fr.body_html)
        self.assertIn('unbilled', en.body_html)
        # Les heures suivent la langue : virgule décimale en français.
        self.assertIn('4,00 h', fr.body_html)
        self.assertIn('4.00 h', en.body_html)

    def test_assistant_prerempli_dans_la_langue_du_client(self):
        for lang, sujet, phrase in (
                ('fr_CA', "État des banques d'heures", 'Veuillez trouver ci-joint'),
                ('en_US', 'Hour bank statement', 'Please find attached')):
            self.client.lang = lang
            assistant = self.env['hour.bank.send.wizard'].create(
                {'hour_bank_id': self.banque.id})
            self.assertTrue(assistant.subject.startswith(sujet), lang)
            self.assertIn(phrase, str(assistant.body), lang)

    def test_facture_contact_en_langue_inactive(self):
        """Un contact resté sur une langue désactivée ne fait pas sortir la facture
        et son rapport dans cette langue : on retombe sur celle du client."""
        contact = self.env['res.partner'].create({
            'name': 'Contact langue morte', 'parent_id': self.client.id})
        # Une langue que la base n'a pas (on ne peut pas désactiver fr_CA ici).
        self.env.cr.execute("UPDATE res_partner SET lang = 'de_DE' WHERE id = %s", (contact.id,))
        contact.invalidate_recordset(['lang'])
        self.assertFalse(self.env['res.lang']._lang_get('de_DE'))
        assistant = self.env['hour.bank.invoice.wizard'].new({
            'bank_id': self.banque.id, 'partner_id': contact.id})
        self.assertEqual(assistant._bf_lang(), 'fr_CA')

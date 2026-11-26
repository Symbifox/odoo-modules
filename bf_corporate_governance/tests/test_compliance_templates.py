"""Des gabarits d'obligations, un générateur par exercice, plus d'événements livrés.

Ce que ces essais tiennent :
- le module ne livre AUCUN événement daté, et le catalogue livré ne porte que des règles;
- la règle d'échéance, sur ses bords (fin de mois, février bissextile, deux bornes);
- le générateur : une société, un exercice, un geste, et jamais deux fois;
- le retrait des cinq événements que le module livrait avant 18.0.2.0.0 : l'intact part,
  le touché reste (détaché);
- le statut stocké qui suit le calendrier, et les deux règles d'accès neuves.
"""

from datetime import date, timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger
from psycopg2 import IntegrityError

from ..models.corporate_compliance import SEMIS_ECHEANCE, SEMIS_LIVRES

MODULE = 'bf_corporate_governance'


class _Base(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env['corporate.compliance.template']
        cls.Event = cls.env['corporate.compliance.event']
        cls.company = cls.env['res.company'].create({'name': 'Société d\'essai conformité'})
        cls.env.user.company_ids |= cls.company

    def _gabarit(self, **kwargs):
        valeurs = {
            'name': 'Obligation d\'essai',
            'code': 'essai_%s' % len(self.Template.with_context(active_test=False).search([])),
            'legal_form': 'qc_business_corporation',
            'scope': 'corporate',
            'event_type': 'agm',
            'anchor': 'fiscal_year_end',
            'offset_months': 6,
        }
        valeurs.update(kwargs)
        return self.Template.create(valeurs)


@tagged('post_install', '-at_install')
class TestShippedCatalogue(_Base):

    def test_the_module_ships_no_dated_event(self):
        """La faute d'origine : des événements datés de l'éditeur dans la table de travail."""
        livres = self.env['ir.model.data'].search([
            ('module', '=', MODULE), ('model', '=', 'corporate.compliance.event')])
        self.assertFalse(livres, "Le module livre encore des événements : %s" % livres.mapped('name'))

    def test_the_shipped_templates_are_sourced_rules(self):
        """Chaque gabarit livré a une base légale, un lien vérifiable et une règle valide."""
        livres = self.env['ir.model.data'].search([
            ('module', '=', MODULE), ('model', '=', 'corporate.compliance.template')])
        self.assertTrue(livres, "Aucun gabarit livré")
        for tpl in self.Template.browse(livres.mapped('res_id')):
            with self.subTest(gabarit=tpl.code):
                self.assertTrue(tpl.legal_basis, "sans base légale")
                self.assertTrue((tpl.reference_url or '').startswith('https://'), "sans lien")
                # La règle se calcule sur une fin d'exercice quelconque, sans lever.
                self.assertIsInstance(tpl._due_date(date(2026, 5, 31)), date)

    def test_the_four_areas_are_covered(self):
        """Ce que le catalogue promet : SPA, OBNL, coopérative, et les volets fiscal et SST."""
        livres = self.Template.browse(self.env['ir.model.data'].search([
            ('module', '=', MODULE), ('model', '=', 'corporate.compliance.template')]).mapped('res_id'))
        for forme in ('qc_business_corporation', 'qc_nonprofit', 'qc_cooperative'):
            with self.subTest(forme=forme):
                self.assertTrue(livres.filtered(
                    lambda t: t.legal_form == forme and t.scope == 'corporate'))
        for portee in ('tax', 'ohs'):
            with self.subTest(portee=portee):
                self.assertTrue(livres.filtered(lambda t: t.scope == portee))


class TestDeadlineRule(_Base):

    def test_six_months_after_the_fiscal_year_end(self):
        tpl = self._gabarit(offset_months=6)
        self.assertEqual(tpl._due_date(date(2026, 5, 30)), date(2026, 11, 30))

    def test_the_day_is_clamped_to_the_end_of_a_short_month(self):
        """31 août + 6 mois : le dernier jour de février, bissextile ou non."""
        tpl = self._gabarit(offset_months=6)
        self.assertEqual(tpl._due_date(date(2026, 8, 31)), date(2027, 2, 28))
        self.assertEqual(tpl._due_date(date(2027, 8, 31)), date(2028, 2, 29))

    def test_a_month_end_lands_on_a_month_end(self):
        """30 juin + 6 mois = 31 décembre : l'ARC compte ainsi l'échéance de la T2."""
        tpl = self._gabarit(offset_months=6)
        self.assertEqual(tpl._due_date(date(2026, 6, 30)), date(2026, 12, 31))
        quatre = self._gabarit(offset_months=4)
        self.assertEqual(quatre._due_date(date(2026, 6, 30)), date(2026, 10, 31))
        # Le 30 mai n'est pas une fin de mois : 30 novembre, pas le 31.
        self.assertEqual(tpl._due_date(date(2026, 5, 30)), date(2026, 11, 30))

    def test_months_and_days(self):
        tpl = self._gabarit(offset_months=6, offset_days=1)
        self.assertEqual(tpl._due_date(date(2026, 5, 30)), date(2026, 12, 1))

    def test_a_fixed_date_falls_the_year_after_the_fiscal_year_ends(self):
        tpl = self._gabarit(anchor='fixed_date', fixed_month='2', fixed_day=31)
        self.assertEqual(tpl._due_date(date(2027, 12, 31)), date(2028, 2, 29))
        self.assertEqual(tpl._due_date(date(2026, 5, 30)), date(2027, 2, 28))
        mars = self._gabarit(anchor='fixed_date', fixed_month='3', fixed_day=15)
        self.assertEqual(mars._due_date(date(2026, 12, 31)), date(2027, 3, 15))

    def test_the_nearer_of_two_bounds_wins(self):
        """AGA : 6 mois après l'exercice (art. 225) ET 15 mois après la précédente (art. 163)."""
        tpl = self._gabarit(offset_months=6, cap_months_after_previous=15)
        self.assertEqual(
            tpl._due_date(date(2026, 12, 31), previous_date=date(2026, 1, 10)), date(2027, 4, 10))
        self.assertEqual(
            tpl._due_date(date(2026, 12, 31), previous_date=date(2026, 6, 1)), date(2027, 6, 30))

    def test_the_incorporation_bounds_only_the_first_one(self):
        tpl = self._gabarit(offset_months=6, first_cap_months_after_incorporation=18)
        # Première : constitution le 2025-06-27, 18 mois = 2026-12-27, la règle donne 11-30.
        self.assertEqual(
            tpl._due_date(date(2026, 5, 30), incorporation_date=date(2025, 6, 27)),
            date(2026, 11, 30))
        # Constitution récente : la borne de 18 mois commande.
        self.assertEqual(
            tpl._due_date(date(2026, 12, 31), incorporation_date=date(2025, 11, 1)),
            date(2027, 5, 1))
        # Société ancienne sans précédente en base : la borne échue est ignorée.
        self.assertEqual(
            tpl._due_date(date(2026, 12, 31), incorporation_date=date(2010, 3, 1)),
            date(2027, 6, 30))

    def test_a_bound_from_an_event_does_not_jump_to_the_month_end(self):
        """Constat n° 7 : 15 mois après une AGA du 28 février = 28 mai, pas 31."""
        tpl = self._gabarit(offset_months=12, cap_months_after_previous=15)
        self.assertEqual(
            tpl._due_date(date(2026, 12, 31), previous_date=date(2026, 2, 28)), date(2027, 5, 28))

    def test_a_rule_reads_in_words(self):
        tpl = self._gabarit(offset_months=6, cap_months_after_previous=15)
        self.assertIn('6', tpl.with_context(lang='en_US').deadline_rule)
        self.assertIn('15', tpl.with_context(lang='en_US').deadline_rule)


class TestGenerator(_Base):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Les essais ne lisent que LEURS gabarits : le catalogue livré est archivé ici,
        # pour que le décompte ne dépende pas de son contenu.
        cls.env['corporate.compliance.template'].search([]).action_archive()
        cls.aga = cls.env['corporate.compliance.template'].create({
            'name': 'AGA', 'code': 'essai_aga', 'legal_form': 'qc_business_corporation',
            'scope': 'corporate', 'event_type': 'agm', 'anchor': 'fiscal_year_end',
            'offset_months': 6, 'cap_months_after_previous': 15,
            'legal_basis': 'LSAQ, art. 225', 'notes': 'Note du gabarit',
        })
        cls.t4 = cls.env['corporate.compliance.template'].create({
            'name': 'T4', 'code': 'essai_t4', 'legal_form': 'any', 'scope': 'tax',
            'event_type': 'information_return', 'anchor': 'fixed_date',
            'fixed_month': '2', 'fixed_day': 31, 'condition': 'Si la société a des employés',
        })
        cls.sst = cls.env['corporate.compliance.template'].create({
            'name': 'Déclaration des salaires', 'code': 'essai_sst', 'legal_form': 'any',
            'scope': 'ohs', 'event_type': 'ohs_filing', 'anchor': 'fixed_date',
            'fixed_month': '3', 'fixed_day': 15,
        })
        cls.obnl = cls.env['corporate.compliance.template'].create({
            'name': 'AGA OBNL', 'code': 'essai_obnl', 'legal_form': 'qc_nonprofit',
            'scope': 'corporate', 'event_type': 'agm', 'offset_months': 4,
        })

    def _assistant(self, **kwargs):
        valeurs = {
            'company_id': self.company.id,
            'legal_form': 'qc_business_corporation',
            'fiscal_year_end': date(2026, 5, 30),
            'fiscal_year': '2026',
        }
        valeurs.update(kwargs)
        return self.env['corporate.compliance.generate'].create(valeurs)

    def _evenements(self):
        return self.Event.search([('company_id', '=', self.company.id)])

    @staticmethod
    def _lignes(formulaire):
        """{gabarit: (cochée, échéance)} des lignes du formulaire."""
        lignes = {}
        for i in range(len(formulaire.line_ids)):
            with formulaire.line_ids.edit(i) as ligne:
                lignes[ligne.template_id.id] = (ligne.selected, ligne.due_date)
        return lignes

    def test_the_form_offers_the_legal_form_and_the_areas(self):
        with Form(self.env['corporate.compliance.generate'].with_company(self.company)) as f:
            f.legal_form = 'qc_business_corporation'
            f.fiscal_year_end = date(2026, 5, 30)
            self.assertEqual(f.fiscal_year, '2026')
            lignes = self._lignes(f)
            self.assertEqual(set(lignes), {self.aga.id, self.t4.id, self.sst.id},
                             "l'OBNL n'a rien à faire dans une SPA")
            self.assertEqual(lignes[self.aga.id], (True, date(2026, 11, 30)))
            self.assertFalse(lignes[self.t4.id][0], "une obligation conditionnelle part décochée")
            f.include_ohs = False
            self.assertNotIn(self.sst.id, self._lignes(f))
            f.legal_form = 'qc_nonprofit'
            self.assertEqual(set(self._lignes(f)), {self.obnl.id, self.t4.id})

    def test_the_legal_form_comes_before_any_form(self):
        """Les obligations de la forme choisie d'abord : l'ordre du modèle (par forme)
        mettait « Toute forme » devant, et l'AGA tombait au bas de la liste."""
        self.aga.sequence, self.t4.sequence, self.sst.sequence = 100, 400, 410
        ordre = [v['template_id'] for v in self._assistant()._line_values()]
        self.assertEqual(ordre[0], self.aga.id)

    def test_generate_once_then_never_twice(self):
        self._assistant().action_generate()
        evenements = self._evenements()
        self.assertEqual(evenements.template_id, self.aga | self.sst)
        aga = evenements.filtered(lambda e: e.template_id == self.aga)
        self.assertEqual(aga.due_date, date(2026, 11, 30))
        self.assertEqual(aga.fiscal_year_end, date(2026, 5, 30))
        self.assertEqual(aga.legal_basis, 'LSAQ, art. 225')
        self.assertEqual(aga.notes, 'Note du gabarit')
        self.assertEqual(aga.name, 'AGA 2026')
        with self.assertRaises(UserError):
            self._assistant().action_generate()
        self.assertEqual(self._evenements(), evenements)

    def test_an_edited_deadline_is_kept(self):
        assistant = self._assistant()
        assistant.line_ids = [(0, 0, v) for v in assistant._line_values()]
        ligne = assistant.line_ids.filtered(lambda l: l.template_id == self.aga)
        ligne.due_date = date(2026, 10, 14)
        assistant.action_generate()
        self.assertEqual(
            self._evenements().filtered(lambda e: e.template_id == self.aga).due_date,
            date(2026, 10, 14))

    def test_the_previous_meeting_bounds_the_next(self):
        self._assistant().action_generate()
        precedente = self._evenements().filtered(lambda e: e.template_id == self.aga)
        precedente.completed_date = date(2026, 7, 1)
        self._assistant(fiscal_year_end=date(2027, 5, 30), fiscal_year='2027').action_generate()
        suivante = self._evenements().filtered(
            lambda e: e.template_id == self.aga and e.fiscal_year == '2027')
        # 2027-05-30 + 6 mois = 2027-11-30, plus loin que 2026-07-01 + 15 mois = 2027-10-01.
        self.assertEqual(suivante.due_date, date(2027, 10, 1))

    def test_the_company_remembers_its_profile(self):
        self._assistant(incorporation_date=date(2025, 6, 27)).action_generate()
        self.assertEqual(self.company.corporate_legal_form, 'qc_business_corporation')
        self.assertEqual(self.company.corporate_incorporation_date, date(2025, 6, 27))
        self.assertEqual((self.company.corporate_fy_end_month, self.company.corporate_fy_end_day),
                         ('5', 30))
        nouveau = self.env['corporate.compliance.generate'].with_company(self.company).new({})
        profil = nouveau._profil(self.company)
        self.assertEqual(profil['legal_form'], 'qc_business_corporation')
        # La dernière fin d'exercice CLOSE, jamais une fin à venir.
        self.assertLessEqual(profil['fiscal_year_end'], fields.Date.today())
        self.assertEqual((profil['fiscal_year_end'].month, profil['fiscal_year_end'].day), (5, 30))

    def test_the_database_refuses_a_duplicate(self):
        self._assistant().action_generate()
        aga = self._evenements().filtered(lambda e: e.template_id == self.aga)
        # Une copie est un événement saisi à la main (pas de gabarit) : elle passe.
        self.assertFalse(aga.copy().template_id)
        with mute_logger('odoo.sql_db'), self.assertRaises(IntegrityError), self.cr.savepoint():
            self.Event.create(aga.copy_data({'template_id': self.aga.id})[0])
            self.env.flush_all()

    def test_the_form_saves_with_an_already_generated_line(self):
        """Constat n° 1 de la relecture : une ligne grisée (readonly) doit repartir avec
        son échéance, sinon l'enregistrement du formulaire refuse (champ obligatoire)."""
        self._assistant().action_generate()
        with Form(self.env['corporate.compliance.generate'].with_company(self.company)) as f:
            f.legal_form = 'qc_business_corporation'
            f.fiscal_year_end = date(2026, 5, 30)
            for i in range(len(f.line_ids)):
                with f.line_ids.edit(i) as ligne:
                    if ligne.template_id == self.t4:
                        ligne.selected = True
        assistant = f.record
        self.assertTrue(assistant.line_ids.filtered('already_generated'))
        assistant.action_generate()
        self.assertIn(self.t4, self._evenements().template_id)

    def test_the_profile_keeps_a_month_end_and_refuses_a_bad_day(self):
        self._assistant(fiscal_year_end=date(2027, 2, 28), fiscal_year='2027').action_generate()
        self.assertEqual(self.company.corporate_fy_end_day, 31, "fin de mois retenue comme 31")
        with self.assertRaises(ValidationError):
            self.company.corporate_fy_end_day = -3

    def test_a_template_can_be_duplicated(self):
        """Le geste que le module recommande pour adapter un gabarit livré."""
        copie = self.aga.copy()
        self.assertNotEqual(copie.code, self.aga.code)
        self.assertNotEqual(copie.copy().code, copie.code)


class TestRetraitDesSemis(_Base):
    """Les cinq événements livrés avant 18.0.2.0.0 : l'intact part, le touché reste."""

    def _semis(self, nom_xml, **kwargs):
        nom, notes = SEMIS_LIVRES[nom_xml]
        valeurs = {
            'name': nom, 'notes': notes, 'event_type': 'agm',
            'fiscal_year': '2026', 'due_date': SEMIS_ECHEANCE,
        }
        valeurs.update(kwargs)
        evenement = self.Event.create(valeurs)
        self.env['ir.model.data'].create({
            'module': MODULE, 'name': nom_xml, 'model': self.Event._name,
            'res_id': evenement.id, 'noupdate': True,
        })
        return evenement

    def _xmlid(self, nom_xml):
        return self.env['ir.model.data'].search([('module', '=', MODULE), ('name', '=', nom_xml)])

    def test_an_untouched_seed_is_removed_with_its_reminder(self):
        evenement = self._semis('compliance_agm')
        evenement.activity_schedule('mail.mail_activity_data_todo', user_id=self.env.uid)
        retires, gardes = self.Event._retirer_semis_livres()
        self.assertEqual((retires, gardes), ([evenement.id], []))
        self.assertFalse(evenement.exists())
        self.assertFalse(self._xmlid('compliance_agm'))
        self.assertFalse(self.env['mail.activity'].search([
            ('res_model', '=', self.Event._name), ('res_id', '=', evenement.id)]))

    def test_a_touched_seed_is_kept_and_detached(self):
        cas = {
            'compliance_agm': {'completed_date': date(2026, 10, 14)},
            'compliance_auditor': {'filing_reference': 'REQ-0000000000'},
            'compliance_annual_declaration': {'due_date': date(2026, 11, 30)},
            'compliance_financial_approval': {'notes': 'Réécrite le 2026-08-29'},
        }
        touches = {nom: self._semis(nom, **valeurs) for nom, valeurs in cas.items()}
        commente = self._semis('compliance_director_election')
        commente.message_post(body='Vu avec le comptable', message_type='comment')
        retires, gardes = self.Event._retirer_semis_livres()
        self.assertEqual(retires, [])
        self.assertEqual(sorted(gardes), sorted([e.id for e in touches.values()] + [commente.id]))
        for nom, evenement in list(touches.items()) + [('compliance_director_election', commente)]:
            with self.subTest(semis=nom):
                self.assertTrue(evenement.exists())
                self.assertFalse(self._xmlid(nom), "resté rattaché au module")

    def test_a_tracked_change_counts_as_touched(self):
        evenement = self._semis('compliance_agm')
        evenement.due_date = date(2026, 12, 1)
        self.env.cr.precommit.run()  # le suivi s'écrit au précommit, qu'un essai ne joue pas
        evenement.due_date = SEMIS_ECHEANCE  # remis à la valeur livrée : le fil s'en souvient
        self.env.cr.precommit.run()
        self.assertTrue(evenement.message_ids.tracking_value_ids, "garde-fou : aucun suivi écrit")
        self.assertEqual(self.Event._retirer_semis_livres(), ([], [evenement.id]))

    def test_a_reminder_closed_with_a_comment_counts_as_touched(self):
        evenement = self._semis('compliance_agm')
        activite = evenement.activity_schedule('mail.mail_activity_data_todo', user_id=self.env.uid)
        activite.action_feedback(feedback='Déposé, confirmation n° 123')
        self.assertEqual(self.Event._retirer_semis_livres(), ([], [evenement.id]))

    def test_a_kept_seed_is_tied_to_its_template(self):
        """Constat n° 6 : gardé, il porte son gabarit, et le générateur ne le repropose pas."""
        evenement = self._semis('compliance_agm', completed_date=date(2026, 10, 14))
        self.Event._retirer_semis_livres()
        gabarit = self.env.ref('bf_corporate_governance.tpl_qc_spa_agm')
        self.assertEqual(evenement.template_id, gabarit)
        assistant = self.env['corporate.compliance.generate'].create({
            'company_id': evenement.company_id.id, 'legal_form': 'qc_business_corporation',
            'fiscal_year_end': date(2026, 5, 30), 'fiscal_year': '2026'})
        ligne = [v for v in assistant._line_values() if v['template_id'] == gabarit.id][0]
        self.assertTrue(ligne['already_generated'])

    def test_running_it_twice_is_harmless(self):
        self._semis('compliance_agm')
        self.Event._retirer_semis_livres()
        self.assertEqual(self.Event._retirer_semis_livres(), ([], []))


class TestStatusAndAccess(_Base):

    def test_the_stored_status_follows_the_calendar(self):
        """Sans recalcul, un événement « À venir » le restait après son échéance."""
        evenement = self.Event.create({
            'name': 'Échéance', 'event_type': 'other',
            'due_date': fields.Date.today() + timedelta(days=90),
        })
        self.assertEqual(evenement.status, 'upcoming')
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE corporate_compliance_event SET due_date = %s WHERE id = %s",
            (fields.Date.today() - timedelta(days=1), evenement.id))
        evenement.invalidate_recordset()
        self.assertEqual(evenement.status, 'upcoming', "garde-fou : le stocké n'a pas bougé")
        self.Event._refresh_status()
        evenement.invalidate_recordset()
        self.assertEqual(evenement.status, 'overdue')

    def test_an_event_is_seen_only_by_its_companies(self):
        autre = self.env['res.company'].create({'name': 'Voisine essai'})
        chez_autre = self.Event.create({
            'name': 'Échéance de la voisine', 'event_type': 'other',
            'due_date': fields.Date.today(), 'company_id': autre.id,
        })
        usager = new_test_user(
            self.env, login='corp_essai', groups='bf_corporate_governance.group_corporate_manager',
            company_id=self.company.id, company_ids=[(6, 0, [self.company.id])])
        self.assertFalse(self.Event.with_user(usager).search([('id', '=', chez_autre.id)]))
        with self.assertRaises(AccessError):
            chez_autre.with_user(usager).read(['name'])
        regle = self.env.ref('bf_corporate_governance.corporate_compliance_event_rule_company')
        self.assertTrue(regle["global"])

    def test_reminders_go_only_to_managers_who_see_the_company(self):
        """Constat n° 8 : sinon un gestionnaire reçoit un rappel qu'il ne peut pas ouvrir."""
        autre = self.env['res.company'].create({'name': 'Voisine rappels essai'})
        dedans = new_test_user(
            self.env, login='corp_essai_in', groups='bf_corporate_governance.group_corporate_manager',
            company_id=autre.id, company_ids=[(6, 0, [autre.id])])
        dehors = new_test_user(
            self.env, login='corp_essai_out', groups='bf_corporate_governance.group_corporate_manager',
            company_id=self.company.id, company_ids=[(6, 0, [self.company.id])])
        evenement = self.Event.create({
            'name': 'Rappel', 'event_type': 'other', 'company_id': autre.id,
            'due_date': fields.Date.today() + timedelta(days=3)})
        self.Event.with_context(mail_notify_force_send=False)._cron_check_compliance_deadlines()
        assignes = self.env['mail.activity'].search([
            ('res_model', '=', self.Event._name), ('res_id', '=', evenement.id)]).user_id
        self.assertIn(dedans, assignes)
        self.assertNotIn(dehors, assignes)

    def test_a_generator_form_is_read_only_by_its_author(self):
        un, deux = (new_test_user(
            self.env, login='corp_essai_%s' % n,
            groups='bf_corporate_governance.group_corporate_manager') for n in (1, 2))
        assistant = self.env['corporate.compliance.generate'].with_user(un).create({
            'company_id': un.company_id.id, 'legal_form': 'qc_nonprofit',
            'fiscal_year_end': date(2026, 3, 31), 'fiscal_year': '2026',
        })
        self.assertFalse(self.env['corporate.compliance.generate'].with_user(deux).search(
            [('id', '=', assistant.id)]))
        for xmlid in ('corporate_compliance_generate_rule_owner',
                      'corporate_compliance_generate_line_rule_owner'):
            with self.subTest(regle=xmlid):
                self.assertTrue(self.env.ref('bf_corporate_governance.' + xmlid)["global"])

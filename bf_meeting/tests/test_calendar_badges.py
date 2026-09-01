"""Les deux pastilles OdJ / compte rendu de la vue Calendrier, et les
étiquettes qui dispensent une rencontre du suivi.

Ce que ces tests gardent vraiment, ce n'est pas l'affichage — une pastille est
du CSS — mais la chaîne qui l'alimente : deux champs STOCKÉS, calculés depuis
l'état réel de l'OdJ et du compte rendu, et déclarés dans l'arch de la vue
calendrier. Chacun des trois maillons peut disparaître sans lever la moindre
erreur, et la pastille s'éteint alors en silence.
"""

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCalendarBadges(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Client Test'})
        cls.project = cls.env['project.project'].create({
            'name': 'Projet Test Pastilles',
            'partner_id': cls.partner.id,
        })
        cls.organiser = cls.env['res.users'].create({
            'name': 'Organisatrice',
            'login': 'bf_badges_organiser',
            'email': 'organiser@example.com',
        })

    def _event(self, **values):
        vals = {
            'name': 'Rencontre de suivi',
            'start': '2026-09-10 14:00:00',
            'stop': '2026-09-10 15:00:00',
            'user_id': self.organiser.id,
            'partner_ids': [Command.set([self.partner.id])],
        }
        vals.update(values)
        return self.env['calendar.event'].create(vals)

    def _agenda(self, event, **values):
        vals = {
            'project_id': self.project.id,
            'date': event.start,
            'calendar_event_id': event.id,
        }
        vals.update(values)
        return self.env['meeting.agenda'].create(vals)

    def _record(self, event, **values):
        vals = {
            'project_id': self.project.id,
            'date': event.start,
            'calendar_event_id': event.id,
        }
        vals.update(values)
        return self.env['meeting.record'].create(vals)

    # --- l'ordre du jour -----------------------------------------------

    def test_sans_odj_l_etat_est_absent(self):
        self.assertEqual(self._event().bf_agenda_state, 'none')

    def test_l_odj_avance_de_redige_a_envoye(self):
        event = self._event()
        agenda = self._agenda(event)
        self.assertEqual(event.bf_agenda_state, 'draft')

        agenda.state = 'confirmed'
        self.assertEqual(event.bf_agenda_state, 'reviewed')

        agenda.email_sent_date = fields.Datetime.now()
        self.assertEqual(event.bf_agenda_state, 'sent')

    def test_un_composeur_ouvert_puis_abandonne_ne_compte_pas_comme_envoye(self):
        """La distinction qui a coûté un cron de rappel muet.

        `sent_date` est posé à la simple OUVERTURE du composeur, parce que le
        bloc de contributions publiques en dérive. Lire ce champ-là ferait
        passer pour envoyé un ordre du jour que personne n'a expédié — et la
        pastille dirait « c'est fait » sur la seule rencontre où il reste
        justement quelque chose à faire.
        """
        event = self._event()
        agenda = self._agenda(event)
        agenda.sent_date = fields.Datetime.now()
        self.assertEqual(agenda.send_state, 'prepared')
        self.assertNotEqual(
            event.bf_agenda_state, 'sent',
            "la pastille lit `sent_date` au lieu de `email_sent_date`")

    # --- le compte rendu -----------------------------------------------

    def test_le_compte_rendu_avance_de_redige_a_envoye(self):
        event = self._event()
        record = self._record(event)
        self.assertEqual(event.bf_minutes_state, 'draft')

        record.report_state = 'reviewed'
        self.assertEqual(event.bf_minutes_state, 'reviewed')

        record.report_state = 'sent'
        self.assertEqual(event.bf_minutes_state, 'sent')

    def test_les_deux_pastilles_sont_independantes(self):
        event = self._event()
        self._agenda(event, email_sent_date=fields.Datetime.now())
        self.assertEqual(event.bf_agenda_state, 'sent')
        self.assertEqual(event.bf_minutes_state, 'none')

    # --- stockage et arch : les deux maillons muets ---------------------

    def test_les_etats_sont_stockes_donc_cherchables(self):
        """Stockés, et le test le prouve en CHERCHANT dessus.

        Un `search` sur un champ calculé non stocké lève. Vérifier l'attribut
        `store` de la définition ne prouverait rien : la colonne pourrait
        manquer en base. La requête, elle, tranche.

        L'enjeu n'est pas théorique : la vue calendrier lit ses lignes par un
        `search_read` sur tous les champs de l'arch, donc un calcul non stocké
        se rejouerait pour chaque rencontre affichée.
        """
        event = self._event()
        self._agenda(event, email_sent_date=fields.Datetime.now())
        found = self.env['calendar.event'].search([
            ('id', '=', event.id),
            ('bf_agenda_state', '=', 'sent'),
        ])
        self.assertEqual(found, event)

    def test_le_gabarit_vise_le_renderer_reellement_utilise(self):
        """🔴 Le défaut qui a coûté une passe entière, et qui ne se voit qu'à l'œil.

        La première version des pastilles héritait de
        `web.CalendarCommonRenderer.event`, le gabarit générique. Il existe,
        l'ancre y correspond, la compilation des assets passe sans un mot — et
        rien ne s'affiche, parce que la vue calendrier de `calendar.event`
        porte `js_class="attendee_calendar"`, dont le renderer redéfinit
        `eventTemplate` pour pointer sur SON gabarit.

        Ce test tient les deux bouts : le `js_class` de l'arch d'un côté, le
        `t-inherit` de notre fichier d'assets de l'autre. Si Odoo change l'un,
        il échoue et nomme l'autre.
        """
        import pathlib
        from odoo.modules.module import get_module_path

        arch = self.env['calendar.event'].get_view(
            self.env.ref('calendar.view_calendar_event_calendar').id,
            'calendar',
        )['arch']
        self.assertIn('js_class="attendee_calendar"', arch,
                      "la vue calendrier n'utilise plus le renderer des "
                      "participants : le gabarit des pastilles vise le mauvais "
                      "eventTemplate et ne s'affichera plus")

        tpl = pathlib.Path(get_module_path('bf_meeting')) / \
            'static/src/xml/calendar_event_badges.xml'
        content = tpl.read_text(encoding='utf-8')
        self.assertIn('t-inherit="calendar.AttendeeCalendarCommonRenderer.event"',
                      content,
                      "les pastilles n'héritent plus du gabarit que le "
                      "renderer des participants rend réellement")
    def test_la_vue_calendrier_declare_les_deux_champs(self):
        """Sans eux dans l'arch, les pastilles s'éteignent sans erreur.

        Le modèle calendrier d'Odoo ne va chercher que les champs déclarés dans
        l'arch (`activeFields`), et c'est par là que les deux valeurs arrivent
        au gabarit OWL. Retirer ces deux lignes ne casse rien, n'affiche rien,
        et ne se voit qu'à l'œil sur la grille.
        """
        arch = self.env['calendar.event'].get_view(
            self.env.ref('calendar.view_calendar_event_calendar').id,
            'calendar',
        )['arch']
        self.assertIn('name="bf_agenda_state"', arch)
        self.assertIn('name="bf_minutes_state"', arch)


@tagged('post_install', '-at_install')
class TestTagSkips(TransactionCase):
    """Les étiquettes qui dispensent d'OdJ et de compte rendu."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Client Étiquettes'})
        cls.tag_interne = cls.env['calendar.event.type'].create({
            'name': 'Interne',
            'bf_skip_agenda': True,
        })
        cls.tag_hors_suivi = cls.env['calendar.event.type'].create({
            'name': 'Hors suivi',
            'bf_skip_dashboard': True,
        })
        cls.tag_neutre = cls.env['calendar.event.type'].create({'name': 'Client'})
        cls.project = cls.env['project.project'].create({
            'name': 'Projet Dispenses',
            'partner_id': cls.partner.id,
        })

    def _event(self, tags=None, **values):
        vals = {
            'name': 'Point hebdo',
            'start': '2026-09-10 14:00:00',
            'stop': '2026-09-10 15:00:00',
            'partner_ids': [Command.set([self.partner.id])],
        }
        if tags is not None:
            vals['categ_ids'] = [Command.set([t.id for t in tags])]
        vals.update(values)
        return self.env['calendar.event'].create(vals)

    def test_l_etiquette_dispense_a_la_creation(self):
        """Et pas seulement dans un formulaire.

        Un `onchange` ne se joue que dans l'interface. Les rencontres arrivent
        ici par la synchronisation Nextcloud, par la prise de rendez-vous et
        par import : si la dispense ne passait que par l'onchange, l'étiquette
        ne servirait à rien dans exactement les cas où on la pose en lot.
        """
        event = self._event(tags=[self.tag_interne])
        self.assertTrue(event.bf_skip_agenda)

    def test_l_etiquette_dispense_aussi_a_l_ecriture(self):
        event = self._event(tags=[self.tag_neutre])
        self.assertFalse(event.bf_skip_agenda)
        event.categ_ids = [Command.link(self.tag_interne.id)]
        self.assertTrue(event.bf_skip_agenda)

    def test_les_deux_dispenses_sont_distinctes(self):
        """« Sans OdJ formel » et « exclue du tableau de bord » ne disent pas
        la même chose : la première dispense du document, la seconde retire du
        suivi une rencontre qui en aurait bien besoin mais qu'on ne veut plus
        voir. Une étiquette qui poserait les deux les confondrait."""
        event = self._event(tags=[self.tag_hors_suivi])
        self.assertTrue(event.bf_skip_dashboard)
        self.assertFalse(event.bf_skip_agenda)

    def test_retirer_l_etiquette_ne_decoche_pas(self):
        """Volontaire, et c'est le comportement qu'il faut garder.

        Décocher au retrait ferait réapparaître au tableau de bord des
        rencontres qu'on en avait sorties délibérément, et écraserait une
        décision prise à la main sur une rencontre précise.
        """
        event = self._event(tags=[self.tag_interne])
        self.assertTrue(event.bf_skip_agenda)
        event.categ_ids = [Command.clear()]
        self.assertTrue(event.bf_skip_agenda,
                        "retirer l'étiquette a défait une décision manuelle")

    def test_la_dispense_l_emporte_sur_un_document_existant(self):
        """🔴 Le défaut vu à l'essai : la pastille restait allumée.

        Une rencontre qui a déjà un OdJ et qu'on dispense ensuite sortait du
        tableau de bord (dont le SQL filtre sur `bf_skip_agenda`) mais gardait
        sa pastille « rédigé » dans la grille. Les deux surfaces disaient deux
        choses différentes du même état.

        Une pastille répond « reste-t-il quelque chose à faire ? ». Sur une
        rencontre dispensée, la réponse est non, qu'un document traîne ou pas.
        """
        event = self._event()
        self.env['meeting.agenda'].create({
            'project_id': self.project.id,
            'date': event.start,
            'calendar_event_id': event.id,
        })
        self.assertEqual(event.bf_agenda_state, 'draft')

        event.bf_skip_agenda = True
        self.assertEqual(event.bf_agenda_state, 'skipped')
        self.assertEqual(event.bf_minutes_state, 'skipped')

        event.bf_skip_agenda = False
        self.assertEqual(event.bf_agenda_state, 'draft',
                         "retirer la dispense n'a pas rendu son état à l'OdJ")

    def test_une_etiquette_neutre_ne_dispense_de_rien(self):
        """Le contrôle qui prouve que le mécanisme DISCRIMINE.

        Sans lui, un `_bf_apply_tag_skips` qui cocherait tout ce qui porte une
        étiquette quelconque passerait tous les autres tests de cette classe.
        """
        event = self._event(tags=[self.tag_neutre])
        self.assertFalse(event.bf_skip_agenda)
        self.assertFalse(event.bf_skip_dashboard)

    def test_une_rencontre_dispensee_n_affiche_pas_de_pastille(self):
        event = self._event(tags=[self.tag_interne])
        self.assertEqual(event.bf_agenda_state, 'skipped')

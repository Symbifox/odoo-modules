"""Gouvernance corporative.

Résolutions, administrateurs, dirigeants et échéances de conformité forment un
sous-système autonome. Ce qui doit survivre à toute évolution du module, c'est
la numérotation des résolutions, le calcul de statut des échéances et les
périodes de mandat.
"""

import html
from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase


class TestCorporateResolution(TransactionCase):

    def _creer(self, **kwargs):
        valeurs = {
            'name': 'Résolution d\'essai',
            'resolution_type': 'board',
            'meeting_date': fields.Date.today(),
        }
        valeurs.update(kwargs)
        return self.env['corporate.resolution'].create(valeurs)

    def test_a_resolution_gets_a_reference_from_the_sequence(self):
        """La référence sort d'``ir.sequence``, pas d'un compteur maison.

        La séquence est une donnée du module. Un déplacement du modèle qui
        l'oublie laisse toutes les résolutions suivantes en « New ».
        """
        resolution = self._creer()
        self.assertTrue(resolution.sequence)
        self.assertNotEqual(resolution.sequence, 'New')

    def test_two_resolutions_do_not_share_a_reference(self):
        premiere = self._creer()
        seconde = self._creer(name='Deuxième résolution')
        self.assertNotEqual(premiere.sequence, seconde.sequence)

    def test_an_explicit_reference_is_kept(self):
        resolution = self._creer(sequence='RES-MANUELLE-1')
        self.assertEqual(resolution.sequence, 'RES-MANUELLE-1')

    def test_state_actions(self):
        resolution = self._creer()
        self.assertEqual(resolution.status, 'draft')
        resolution.action_propose()
        self.assertEqual(resolution.status, 'proposed')
        resolution.action_adopt()
        self.assertEqual(resolution.status, 'adopted')
        resolution.action_reset_draft()
        self.assertEqual(resolution.status, 'draft')
        resolution.action_reject()
        self.assertEqual(resolution.status, 'rejected')


class TestCorporateCompliance(TransactionCase):

    def _creer(self, jours, **kwargs):
        valeurs = {
            'name': 'Échéance d\'essai',
            'event_type': 'annual_declaration',
            'due_date': fields.Date.today() + timedelta(days=jours),
        }
        valeurs.update(kwargs)
        return self.env['corporate.compliance.event'].create(valeurs)

    def test_status_follows_the_due_date(self):
        """Les quatre statuts, sur les quatre côtés de la frontière des 30 jours."""
        self.assertEqual(self._creer(-1).status, 'overdue')
        self.assertEqual(self._creer(0).status, 'due_soon')
        self.assertEqual(self._creer(30).status, 'due_soon')
        self.assertEqual(self._creer(31).status, 'upcoming')

    def test_completing_an_event_wins_over_the_due_date(self):
        evenement = self._creer(-90)
        self.assertEqual(evenement.status, 'overdue')
        evenement.action_complete()
        self.assertEqual(evenement.status, 'completed')
        evenement.action_reset()
        self.assertEqual(evenement.status, 'overdue')


class TestCorporateMandates(TransactionCase):
    """Administrateurs et dirigeants : ``is_active`` est un calcul stocké."""

    def _partenaire(self, nom):
        return self.env['res.partner'].create({'name': nom})

    def test_a_director_without_end_date_is_active(self):
        administrateur = self.env['corporate.director'].create({
            'partner_id': self._partenaire('Administratrice Un').id,
            'appointment_date': fields.Date.today() - timedelta(days=365),
        })
        self.assertTrue(administrateur.is_active)

    def test_a_director_whose_mandate_ended_is_inactive(self):
        administrateur = self.env['corporate.director'].create({
            'partner_id': self._partenaire('Administratrice Deux').id,
            'appointment_date': fields.Date.today() - timedelta(days=365),
            'end_date': fields.Date.today() - timedelta(days=1),
        })
        self.assertFalse(administrateur.is_active)

    def test_an_officer_without_end_date_is_active(self):
        dirigeant = self.env['corporate.officer'].create({
            'partner_id': self._partenaire('Dirigeant Un').id,
            'title': 'president',
            'appointment_date': fields.Date.today() - timedelta(days=365),
        })
        self.assertTrue(dirigeant.is_active)

    def test_an_officer_whose_mandate_ended_is_inactive(self):
        dirigeant = self.env['corporate.officer'].create({
            'partner_id': self._partenaire('Dirigeant Deux').id,
            'title': 'secretary',
            'appointment_date': fields.Date.today() - timedelta(days=365),
            'end_date': fields.Date.today() - timedelta(days=1),
        })
        self.assertFalse(dirigeant.is_active)


class TestCorporateSignatories(TransactionCase):
    """Le bloc de signature du PDF.

    Le défaut : le gabarit imprimait « Administrateur » sous chaque nom, et
    tirait ses noms du registre des administrateurs quel que soit le type de
    résolution. Sur une résolution des ACTIONNAIRES dont le dispositif écarte
    expressément tout vote d'administrateur (art. 127 et 129 LSAQ), le document
    se contredisait donc lui-même.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Société dédiée : ``_get_directors_at_date`` interroge TOUT le registre
        # de la société. Sur une base qui porte déjà des administrateurs — donc
        # sur toute copie de production — le registre existant se mêlerait au
        # jeu d'essai et ferait échouer les replis pour une raison qui n'a rien
        # à voir avec ce qu'ils éprouvent.
        cls.societe = cls.env['res.company'].create({'name': 'Société d\'essai'})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.societe.id]))
        cls.actionnaire = cls.env['res.partner'].create({'name': 'Actionnaire Unique'})
        cls.dirigeant = cls.env['res.partner'].create({'name': 'Dirigeant Attestant'})
        cls.ancien = cls.env['res.partner'].create({'name': 'Administrateur Sortant'})
        cls.nouveau = cls.env['res.partner'].create({'name': 'Administrateur Entrant'})

    def _resolution(self, **kwargs):
        valeurs = {
            'name': 'Résolution d\'essai',
            'resolution_type': 'board',
            'meeting_date': fields.Date.today(),
            'company_id': self.societe.id,
        }
        valeurs.update(kwargs)
        return self.env['corporate.resolution'].create(valeurs)

    def _rendu(self, resolution):
        """Le HTML du rapport, apostrophes déséchappées.

        QWeb rend « d'intérêt » en « d&#39;intérêt » : sans ce passage, une
        assertion sur le texte français échoue pour une raison qui n'a rien à
        voir avec ce qu'elle éprouve.
        """
        return html.unescape(str(self.env['ir.qweb']._render(
            'project_knowledge_matrix.report_corporate_resolution',
            {'docs': resolution, 'env': self.env},
        )))

    def _administrateur(self, partenaire, nomme, fin=False):
        return self.env['corporate.director'].create({
            'partner_id': partenaire.id,
            'appointment_date': nomme,
            'end_date': fin,
            'company_id': self.societe.id,
        })

    # ------------------------------------------------------------------
    # Les signataires saisis font foi
    # ------------------------------------------------------------------

    def test_signatories_win_over_the_director_registry(self):
        """Une résolution d'actionnaires, réduite à ses deux signatures.

        Un actionnaire adopte, un dirigeant contresigne pour attester sa
        dénonciation d'intérêt. Aucun des deux ne signe « comme
        administrateur » — et il y a bien un administrateur au registre pour
        que le repli ait quelque chose à proposer s'il l'emportait.
        """
        self._administrateur(self.nouveau, fields.Date.today() - timedelta(days=30))
        resolution = self._resolution(resolution_type='written_shareholder')
        self.env['corporate.resolution.signatory'].create([
            {
                'resolution_id': resolution.id,
                'sequence': 10,
                'partner_id': self.actionnaire.id,
                'capacity': 'sole_shareholder',
            },
            {
                'resolution_id': resolution.id,
                'sequence': 20,
                'partner_id': self.dirigeant.id,
                'capacity': 'other',
                'capacity_custom': 'Vice-président, secrétaire et trésorier',
                'purpose': "aux seules fins d'attester la dénonciation d'intérêt",
            },
        ])

        signataires = resolution._get_signatories()

        self.assertEqual(
            [s['name'] for s in signataires],
            ['Actionnaire Unique', 'Dirigeant Attestant'],
            "Les signataires saisis doivent primer le registre, dans leur ordre",
        )
        self.assertEqual(signataires[0]['capacity'], 'Actionnaire unique')
        self.assertEqual(
            signataires[1]['capacity'], 'Vice-président, secrétaire et trésorier',
            "La qualité littérale doit passer telle quelle sous le nom",
        )
        self.assertEqual(
            signataires[1]['purpose'],
            "aux seules fins d'attester la dénonciation d'intérêt",
        )
        self.assertNotIn(
            'Administrateur', [s['capacity'] for s in signataires],
            "Aucune qualité d'administrateur ne doit être affirmée ici",
        )

    def test_other_capacity_demands_its_text(self):
        """« Autre » sans texte réintroduit exactement le défaut corrigé."""
        resolution = self._resolution(resolution_type='written_shareholder')
        with self.assertRaises(ValidationError):
            self.env['corporate.resolution.signatory'].create({
                'resolution_id': resolution.id,
                'partner_id': self.actionnaire.id,
                'capacity': 'other',
            })

    def test_duplicating_a_resolution_keeps_its_signature_block(self):
        """Le dispositif se copie, le proposeur aussi : les signataires doivent suivre.

        Sans ``copy=True``, la copie repart avec son texte entier et un bloc de
        signature vide, qui se rabat sur le seul proposeur. La contresignature
        disparaît sans que rien ne le dise.
        """
        resolution = self._resolution(
            resolution_type='written_shareholder',
            mover_id=self.actionnaire.id,
        )
        self.env['corporate.resolution.signatory'].create([
            {
                'resolution_id': resolution.id,
                'partner_id': self.actionnaire.id,
                'capacity': 'sole_shareholder',
            },
            {
                'resolution_id': resolution.id,
                'sequence': 20,
                'partner_id': self.dirigeant.id,
                'capacity': 'officer',
                'purpose': "aux seules fins d'attester la dénonciation d'intérêt",
            },
        ])

        copie = resolution.copy()

        self.assertEqual(
            [s['name'] for s in copie._get_signatories()],
            ['Actionnaire Unique', 'Dirigeant Attestant'],
        )
        self.assertEqual(
            copie._get_signatories()[1]['purpose'],
            "aux seules fins d'attester la dénonciation d'intérêt",
        )
        self.assertNotEqual(
            copie.signatory_ids.ids, resolution.signatory_ids.ids,
            'La copie doit avoir ses propres lignes, pas celles de la source',
        )

    # ------------------------------------------------------------------
    # Le repli — et son absence
    # ------------------------------------------------------------------

    def test_a_shareholder_resolution_names_its_mover_without_a_capacity(self):
        """Sans registre des actionnaires, la fiche sait QUI, pas EN QUELLE QUALITÉ.

        Le repli du conseil ne doit surtout pas déborder ici : c'est
        précisément lui qui faisait signer un actionnaire « comme
        administrateur ».
        """
        self._administrateur(self.nouveau, fields.Date.today() - timedelta(days=30))
        resolution = self._resolution(
            resolution_type='written_shareholder',
            mover_id=self.actionnaire.id,
        )
        self.assertEqual(resolution._get_signatories(), [{
            'name': 'Actionnaire Unique', 'capacity': False, 'purpose': False,
        }])

    def test_a_shareholder_resolution_without_a_mover_deduces_nobody(self):
        self._administrateur(self.nouveau, fields.Date.today() - timedelta(days=30))
        resolution = self._resolution(resolution_type='written_shareholder')
        self.assertEqual(resolution._get_signatories(), [])

    def test_a_board_resolution_falls_back_on_its_directors(self):
        self._administrateur(self.nouveau, fields.Date.today() - timedelta(days=30))
        resolution = self._resolution(resolution_type='written_board')
        signataires = resolution._get_signatories()
        self.assertEqual([s['name'] for s in signataires], ['Administrateur Entrant'])
        self.assertEqual(signataires[0]['capacity'], 'Administrateur')

    def test_the_fallback_is_the_board_of_the_meeting_day(self):
        """Un PDF réimprimé aujourd'hui doit nommer le conseil de l'époque.

        ``is_active`` répond « aujourd'hui » : sans borne de date, les
        des résolutions fondatrices nommaient un administrateur élu onze mois
        plus tard.
        """
        self._administrateur(
            self.ancien,
            fields.Date.today() - timedelta(days=400),
            fin=fields.Date.today() - timedelta(days=100),
        )
        self._administrateur(self.nouveau, fields.Date.today() - timedelta(days=100))

        ancienne = self._resolution(
            resolution_type='written_board',
            meeting_date=fields.Date.today() - timedelta(days=300),
        )
        recente = self._resolution(
            resolution_type='written_board',
            meeting_date=fields.Date.today() - timedelta(days=10),
        )

        self.assertEqual(
            [s['name'] for s in ancienne._get_signatories()],
            ['Administrateur Sortant'],
        )
        self.assertEqual(
            [s['name'] for s in recente._get_signatories()],
            ['Administrateur Entrant'],
        )

    # ------------------------------------------------------------------
    # Le rendu
    # ------------------------------------------------------------------

    def test_the_mover_fallback_prints_a_name_and_no_capacity(self):
        """Le repli nomme, il n'affirme pas.

        La quasi-totalité des résolutions d'actionnaires d'un livre de minutes
        portent le véritable actionnaire du jour comme proposeur : les laisser
        s'imprimer sur une ligne anonyme serait une régression.
        """
        resolution = self._resolution(
            resolution_type='written_shareholder',
            mover_id=self.actionnaire.id,
        )
        rendu = self._rendu(resolution)
        self.assertIn('<div class="res-sig-name">Actionnaire Unique</div>', rendu)
        self.assertNotIn('<div class="res-sig-role">', rendu)

    def test_the_report_prints_the_capacity_of_each_signatory(self):
        """Le contrôle qui aurait vu le défaut : lire le HTML rendu.

        Les tests du modèle seuls ne l'auraient pas vu — le rôle était en dur
        dans le gabarit, pas dans le code.
        """
        resolution = self._resolution(
            resolution_type='written_shareholder',
            name='Approbation d\'une avance sans intérêt',
        )
        self.env['corporate.resolution.signatory'].create([
            {
                'resolution_id': resolution.id,
                'partner_id': self.actionnaire.id,
                'capacity': 'sole_shareholder',
            },
            {
                'resolution_id': resolution.id,
                'sequence': 20,
                'partner_id': self.dirigeant.id,
                'capacity': 'other',
                'capacity_custom': 'Vice-président, secrétaire et trésorier',
                'purpose': "aux seules fins d'attester la dénonciation d'intérêt",
            },
        ])

        rendu = self._rendu(resolution)

        self.assertIn('<div class="res-sig-role">Actionnaire unique</div>', rendu)
        self.assertIn(
            '<div class="res-sig-role">Vice-président, secrétaire et trésorier</div>',
            rendu,
        )
        self.assertIn(
            '<div class="res-sig-purpose">aux seules fins d\'attester la '
            'dénonciation d\'intérêt</div>',
            rendu,
        )
        self.assertNotIn(
            '<div class="res-sig-role">Administrateur</div>', rendu,
            "Le rôle « Administrateur » ne doit plus être écrit en dur",
        )

    def test_a_shareholder_resolution_with_nothing_to_name_prints_a_blank_line(self):
        """Une ligne à remplir vaut mieux qu'une qualité inventée."""
        self._administrateur(self.nouveau, fields.Date.today() - timedelta(days=30))
        resolution = self._resolution(resolution_type='written_shareholder')

        rendu = self._rendu(resolution)

        # La classe elle-même vit aussi dans la feuille de style en ligne :
        # c'est la balise rendue qu'il faut chercher, pas le nom de classe.
        self.assertIn('<div class="res-sig-line"></div>', rendu)
        self.assertNotIn('<div class="res-sig-role">', rendu)
        self.assertNotIn('<div class="res-sig-name">', rendu)
        self.assertNotIn(
            'Administrateur Entrant', rendu,
            "Le registre des administrateurs n'a rien à faire ici",
        )

"""Qui bf_sign va faire signer.

Le PDF et la demande de signature doivent nommer les mêmes personnes. Tant que
les deux surfaces lisaient chacune leur propre source (le registre des
administrateurs d'un côté, le proposeur de l'autre), une résolution des
actionnaires contresignée par un dirigeant partait en signature sans lui.
"""

from odoo import fields
from odoo.tests import TransactionCase


class TestSignerRouting(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Société dédiée : le repli du conseil lit tout le registre de la
        # société, donc les administrateurs déjà en base d'une copie de
        # production fausseraient le résultat. Voir le même montage dans
        # bf_corporate_governance/tests/test_corporate_governance.py.
        cls.societe = cls.env['res.company'].create({'name': 'Société d\'essai 24525 (signature)'})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.societe.id]))
        cls.actionnaire = cls.env['res.partner'].create({
            'name': 'Actionnaire Unique', 'email': 'actionnaire@example.test',
        })
        cls.dirigeant = cls.env['res.partner'].create({
            'name': 'Dirigeant Attestant', 'email': 'dirigeant@example.test',
        })
        cls.administrateur = cls.env['res.partner'].create({
            'name': 'Administrateur En Poste', 'email': 'admin@example.test',
        })
        cls.env['corporate.director'].create({
            'partner_id': cls.administrateur.id,
            'appointment_date': fields.Date.today(),
            'company_id': cls.societe.id,
        })

    def _resolution(self, **kwargs):
        valeurs = {
            'name': "Résolution d'essai",
            'resolution_type': 'written_shareholder',
            'meeting_date': fields.Date.today(),
            'company_id': self.societe.id,
        }
        valeurs.update(kwargs)
        return self.env['corporate.resolution'].create(valeurs)

    def test_the_signatory_lines_drive_the_signers(self):
        resolution = self._resolution(mover_id=self.actionnaire.id)
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
        self.assertEqual(
            resolution._sign_signer_partners(),
            self.actionnaire | self.dirigeant,
            "Le contresignataire doit partir en signature, lui aussi",
        )

    def test_without_signatories_a_shareholder_resolution_falls_back_on_the_mover(self):
        resolution = self._resolution(mover_id=self.actionnaire.id)
        self.assertEqual(resolution._sign_signer_partners(), self.actionnaire)

    def test_without_signatories_a_board_resolution_falls_back_on_its_directors(self):
        resolution = self._resolution(resolution_type='written_board')
        self.assertEqual(resolution._sign_signer_partners(), self.administrateur)

    def test_a_board_resolution_of_the_past_does_not_borrow_todays_directors(self):
        """Le repli du conseil est borné à la date de la séance.

        L'administrateur du jeu d'essai est nommé aujourd'hui : une résolution
        d'il y a un an ne peut pas le faire signer.
        """
        resolution = self._resolution(
            resolution_type='written_board',
            meeting_date=fields.Date.today().replace(year=fields.Date.today().year - 1),
        )
        self.assertFalse(resolution._sign_signer_partners())

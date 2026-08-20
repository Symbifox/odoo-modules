"""Frontières du module — ce que le découpage ne doit pas casser.

Trois familles d'invariants :

* la dépendance ``hr``, retirée en 18.0.11.0.0, ne doit pas revenir par la
  bande ;
* les deux modèles du catalogue de logiciels, supprimés, doivent rester morts ;
* tout modèle porté par le module doit avoir des droits d'accès et un manifeste
  dont les fichiers existent — c'est le contrôle qui dira, au moment de sortir
  un sous-système du module, qu'une extraction a oublié une ligne d'ACL ou un
  fichier de données.
"""

import os

from odoo.modules.module import get_manifest, get_module_path
from odoo.tests import TransactionCase

MODULE = 'project_knowledge_matrix'


class TestModuleBoundaries(TransactionCase):

    def _modeles_du_module(self):
        data = self.env['ir.model.data'].search([
            ('module', '=', MODULE), ('model', '=', 'ir.model'),
        ])
        return self.env['ir.model'].browse(data.mapped('res_id')).exists()

    def _champs_du_module(self):
        data = self.env['ir.model.data'].search([
            ('module', '=', MODULE), ('model', '=', 'ir.model.fields'),
        ])
        return self.env['ir.model.fields'].browse(data.mapped('res_id')).exists()

    # ------------------------------------------------------------------
    # hr
    # ------------------------------------------------------------------

    def test_no_relational_field_points_at_hr(self):
        """Aucun champ du module ne pointe vers ``hr.*``.

        Deux ``Many2one`` typés vers ``hr.department`` faisaient de ``hr`` une
        dépendance dure pour un champ quasi jamais rempli. Un champ relationnel
        typé suffit à la faire revenir, et ça ne se voit qu'en installation
        neuve.
        """
        champs = self._champs_du_module()
        # Garde-fou : un inventaire vide passerait au vert sans rien éprouver.
        self.assertTrue(champs, 'Aucun champ recensé pour %s' % MODULE)
        fautifs = [
            f'{champ.model}.{champ.name} → {champ.relation}'
            for champ in champs
            if champ.relation and champ.relation.split('.')[0] == 'hr'
        ]
        self.assertFalse(fautifs, "Champs relationnels vers hr : %s" % fautifs)

    def test_hr_is_not_a_declared_dependency(self):
        depends = get_manifest(MODULE).get('depends', [])
        self.assertNotIn('hr', depends)
        self.assertNotIn('hr_timesheet', depends)

    # ------------------------------------------------------------------
    # Catalogue de logiciels — retiré, doit le rester
    # ------------------------------------------------------------------

    def test_software_catalogue_models_are_gone(self):
        """``hosting.software`` fait le travail ; ces deux-là étaient mort-nés."""
        for nom in ('document.software', 'document.software.version'):
            self.assertNotIn(nom, self.env, f'{nom} est réapparu')
            self.assertFalse(
                self.env['ir.model'].search([('model', '=', nom)]),
                f'{nom} traîne encore dans ir_model',
            )

    # ------------------------------------------------------------------
    # Intégrité — les contrôles utiles au découpage
    # ------------------------------------------------------------------

    def test_every_model_of_the_module_has_access_rights(self):
        """Un modèle sans ACL est lisible de personne — et personne ne le voit.

        Odoo se contente d'un avertissement au journal. Au moment de sortir un
        sous-système du module, c'est le contrôle qui signale une ligne
        d'``ir.model.access.csv`` restée derrière.
        """
        modeles = self._modeles_du_module()
        self.assertTrue(modeles, 'Aucun modèle recensé pour %s' % MODULE)
        sans_acl = []
        for modele in modeles:
            registre = self.env.get(modele.model)
            if registre is None or registre._abstract or registre._transient:
                continue
            acl = self.env['ir.model.access'].search_count([
                ('model_id', '=', modele.id),
            ])
            if not acl:
                sans_acl.append(modele.model)
        self.assertFalse(sans_acl, 'Modèles sans droit d\'accès : %s' % sans_acl)

    def test_every_manifest_data_file_exists(self):
        """Le manifeste ne nomme pas de fichier absent.

        Un fichier renommé ou déplacé fait échouer l'installation d'un locataire
        neuf, jamais celle d'un locataire déjà installé — donc le défaut voyage
        jusqu'au prochain client.
        """
        chemin = get_module_path(MODULE)
        manifeste = get_manifest(MODULE)
        self.assertTrue(manifeste.get('data'), 'Manifeste sans fichier de données')
        manquants = [
            fichier
            for cle in ('data', 'demo')
            for fichier in manifeste.get(cle, [])
            if not os.path.exists(os.path.join(chemin, fichier))
        ]
        self.assertFalse(manquants, 'Fichiers absents : %s' % manquants)

    def test_the_encryption_key_lives_in_a_system_parameter(self):
        """La clé de chiffrement ne déménage pas avec le module.

        Sortir ``project.credential`` du module ne casse rien tant que la clé
        vit dans ``ir.config_parameter`` — et pas dans une donnée du module,
        qu'une désinstallation emporterait avec elle.
        """
        # La clé est engendrée à la première utilisation : la provoquer, sinon
        # le test passerait au vert sur une base neuve où rien n'a été chiffré.
        self.env['project.credential']._get_encryption_key()
        cle = self.env['ir.config_parameter'].sudo().get_param(
            'project_credential.encryption_key')
        self.assertTrue(cle, 'Aucune clé de chiffrement en paramètre système')
        self.assertFalse(
            self.env['ir.model.data'].search([
                ('module', '=', MODULE),
                ('model', '=', 'ir.config_parameter'),
            ]),
            'La clé de chiffrement est devenue une donnée du module : une '
            'désinstallation l\'emporterait avec elle.',
        )

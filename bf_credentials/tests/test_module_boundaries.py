"""Frontières du module extrait.

Les mêmes contrôles que ``project_knowledge_matrix`` s'était donnés au chantier
01, appliqués au coffre : un modèle sans droits d'accès n'est lisible de
personne, un manifeste qui nomme un fichier absent n'échoue qu'en installation
neuve, et un gabarit OWL décroché ne dit rien au journal du serveur.
"""

import os
from xml.etree import ElementTree

from odoo.modules.module import get_manifest, get_module_path
from odoo.tests import TransactionCase

MODULE = 'bf_credentials'


class TestModuleBoundaries(TransactionCase):

    def _modeles_du_module(self):
        data = self.env['ir.model.data'].search([
            ('module', '=', MODULE), ('model', '=', 'ir.model'),
        ])
        return self.env['ir.model'].browse(data.mapped('res_id')).exists()

    def test_every_model_of_the_module_has_access_rights(self):
        modeles = self._modeles_du_module()
        self.assertTrue(modeles, 'Aucun modèle recensé pour %s' % MODULE)
        sans_acl = []
        for modele in modeles:
            registre = self.env.get(modele.model)
            if registre is None or registre._abstract:
                continue
            acl = self.env['ir.model.access'].search_count([
                ('model_id', '=', modele.id),
            ])
            if not acl:
                sans_acl.append(modele.model)
        self.assertFalse(sans_acl, "Modèles sans droit d'accès : %s" % sans_acl)

    def test_every_manifest_data_file_exists(self):
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

    def test_every_asset_file_exists(self):
        chemin = get_module_path(MODULE)
        assets = get_manifest(MODULE).get('assets', {})
        self.assertTrue(assets, 'Manifeste sans asset')
        manquants = []
        for fichiers in assets.values():
            for fichier in fichiers:
                prefixe = MODULE + '/'
                self.assertTrue(fichier.startswith(prefixe),
                                'Asset hors du module : %s' % fichier)
                if not os.path.exists(
                        os.path.join(chemin, fichier[len(prefixe):])):
                    manquants.append(fichier)
        self.assertFalse(manquants, 'Assets absents : %s' % manquants)

    def test_cryptography_is_declared(self):
        """La dépendance externe a suivi le coffre.

        Elle vivait dans le manifeste du socle, qui ne chiffrait plus rien une
        fois le coffre parti. Non déclarée ici, une installation neuve
        laisserait ``Fernet`` à ``None`` et le module stockerait EN CLAIR — le
        code se replie sans lever.
        """
        externes = get_manifest(MODULE).get('external_dependencies', {})
        self.assertIn('cryptography', externes.get('python', []))

    def test_the_owl_column_still_finds_its_anchor(self):
        """L'héritage des gabarits OWL se joue dans le NAVIGATEUR.

        Le serveur ne fait que concaténer les fichiers du paquet : un
        ``t-inherit`` qui pointe vers un gabarit disparu ne produit aucune
        erreur au chargement. Ça casse chez l'utilisateur, en silence côté
        serveur. Ce contrôle rejoue la mécanique de
        ``template_inheritance.js``.
        """
        chemin = os.path.join(get_module_path(MODULE), 'static', 'src', 'xml',
                              'credential_dashboard.xml')
        arbre = ElementTree.parse(chemin).getroot()
        gabarits = [n for n in arbre if n.get('t-inherit')]
        self.assertTrue(gabarits, 'Aucun gabarit hérité dans %s' % chemin)
        for gabarit in gabarits:
            parent_nom = gabarit.get('t-inherit')
            module_parent = parent_nom.split('.')[0]
            with self.subTest(gabarit=parent_nom):
                self.assertEqual(gabarit.get('t-inherit-mode'), 'extension')
                fichier = self._fichier_du_gabarit(module_parent, parent_nom)
                self.assertTrue(fichier, 'Gabarit parent introuvable : %s' % parent_nom)
                parent = ElementTree.parse(fichier).getroot()
                cible = [n for n in parent if n.get('t-name') == parent_nom]
                self.assertEqual(len(cible), 1,
                                 '%s n\'est pas défini une seule fois' % parent_nom)
                for operation in gabarit:
                    self.assertEqual(operation.tag, 'xpath')
                    expr = operation.get('expr')
                    trouve = [cible[0]] if expr == '.' else cible[0].findall(expr)
                    self.assertTrue(
                        trouve,
                        "L'expression %r ne désigne rien dans %s" % (expr, parent_nom))

    def _fichier_du_gabarit(self, module, nom_gabarit):
        chemin = get_module_path(module)
        for fichiers in get_manifest(module).get('assets', {}).values():
            for fichier in fichiers:
                if not fichier.endswith('.xml'):
                    continue
                absolu = os.path.join(chemin, fichier.split('/', 1)[1])
                if not os.path.exists(absolu):
                    continue
                if ('t-name="%s"' % nom_gabarit) in open(absolu, encoding='utf-8').read():
                    return absolu
        return None

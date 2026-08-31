"""Frontières du module extrait.

Les mêmes contrôles que ``project_knowledge_matrix`` s'était donnés au chantier
01, appliqués cette fois au module qui vient d'en sortir : un modèle sans droits
d'accès n'est lisible de personne, et un manifeste qui nomme un fichier absent
n'échoue qu'en installation neuve — donc chez le prochain locataire, jamais ici.
"""

import os
from xml.etree import ElementTree

from odoo.modules.module import get_manifest, get_module_path
from odoo.tests import TransactionCase

MODULE = 'bf_corporate_governance'


class TestModuleBoundaries(TransactionCase):

    def _modeles_du_module(self):
        data = self.env['ir.model.data'].search([
            ('module', '=', MODULE), ('model', '=', 'ir.model'),
        ])
        return self.env['ir.model'].browse(data.mapped('res_id')).exists()

    def test_every_model_of_the_module_has_access_rights(self):
        modeles = self._modeles_du_module()
        # Garde-fou : un inventaire vide passerait au vert sans rien éprouver.
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
        """Un fichier d'asset absent ne casse pas le serveur, il casse la page.

        Le bloc de gouvernance du tableau de bord tient dans deux fichiers
        statiques : un gabarit et le correctif qui porte ses cinq clics. L'un
        sans l'autre donne un bloc muet ou une erreur au navigateur, et rien
        au journal du serveur.
        """
        chemin = get_module_path(MODULE)
        manifeste = get_manifest(MODULE)
        assets = manifeste.get('assets', {})
        self.assertTrue(assets, 'Manifeste sans asset')
        manquants = []
        for fichiers in assets.values():
            for fichier in fichiers:
                prefixe = MODULE + '/'
                self.assertTrue(
                    fichier.startswith(prefixe),
                    "Asset hors du module : %s" % fichier,
                )
                relatif = fichier[len(prefixe):]
                if not os.path.exists(os.path.join(chemin, relatif)):
                    manquants.append(fichier)
        self.assertFalse(manquants, 'Assets absents : %s' % manquants)

    def test_no_template_reads_the_brand_fields_directly(self):
        """La marque se lit par `_pkm_brand()`, jamais par le champ.

        `report_brand_*` vient de `bf_onboarding_base` 18.0.2.0.0 ; sa 1.0.0 ne
        l'a pas, et les deux tournent en production. Le PDF de résolution lisait
        le champ onze fois.
        """
        chemin = get_module_path(MODULE)
        fautifs = []
        for dossier in ('data', 'report'):
            racine = os.path.join(chemin, dossier)
            if not os.path.isdir(racine):
                continue
            for fichier in sorted(os.listdir(racine)):
                if not fichier.endswith('.xml'):
                    continue
                for ligne in open(os.path.join(racine, fichier), encoding='utf-8'):
                    if 'report_brand_' in ligne and '_pkm_brand' not in ligne \
                            and not ligne.strip().startswith('<!--') \
                            and 'AttributeError' not in ligne:
                        fautifs.append('%s/%s: %s' % (dossier, fichier, ligne.strip()[:80]))
        self.assertFalse(fautifs, 'Lectures directes de report_brand_* : %s' % fautifs)

    def test_the_owl_block_still_finds_its_anchor(self):
        """L'héritage des gabarits OWL se joue dans le NAVIGATEUR.

        Le serveur ne fait que concaténer les fichiers du paquet : un
        ``t-inherit`` qui pointe vers un gabarit disparu, ou un ``xpath`` qui
        ne résout plus, ne produit aucune erreur au chargement. Ça casse chez
        l'utilisateur, et le journal du serveur reste muet.

        Ce contrôle rejoue la mécanique de ``template_inheritance.js`` : le
        gabarit parent doit exister sous le nom cité, et l'expression du
        ``xpath`` doit y désigner un nœud.
        """
        chemin = os.path.join(
            get_module_path(MODULE), 'static', 'src', 'xml',
            'corporate_dashboard.xml',
        )
        arbre = ElementTree.parse(chemin).getroot()
        gabarits = [n for n in arbre if n.get('t-inherit')]
        self.assertTrue(gabarits, 'Aucun gabarit hérité dans %s' % chemin)

        for gabarit in gabarits:
            parent_nom = gabarit.get('t-inherit')
            module_parent, _, nom = parent_nom.partition('.')
            with self.subTest(gabarit=parent_nom):
                self.assertEqual(gabarit.get('t-inherit-mode'), 'extension')
                fichier = self._fichier_du_gabarit(module_parent, parent_nom)
                self.assertTrue(
                    fichier,
                    'Gabarit parent introuvable dans les assets de %s : %s'
                    % (module_parent, parent_nom),
                )
                parent = ElementTree.parse(fichier).getroot()
                cible = [n for n in parent if n.get('t-name') == parent_nom]
                self.assertEqual(
                    len(cible), 1,
                    'Le gabarit %s n\'est pas défini une et une seule fois'
                    % parent_nom,
                )
                for operation in gabarit:
                    self.assertEqual(operation.tag, 'xpath')
                    expr = operation.get('expr')
                    trouve = (
                        [cible[0]] if expr == '.' else cible[0].findall(expr)
                    )
                    self.assertTrue(
                        trouve,
                        "L'expression %r ne désigne rien dans %s"
                        % (expr, parent_nom),
                    )

    def _fichier_du_gabarit(self, module, nom_gabarit):
        """Le fichier d'asset de ``module`` qui définit ``nom_gabarit``."""
        chemin = get_module_path(module)
        for fichiers in get_manifest(module).get('assets', {}).values():
            for fichier in fichiers:
                if not fichier.endswith('.xml'):
                    continue
                relatif = fichier.split('/', 1)[1]
                absolu = os.path.join(chemin, relatif)
                if not os.path.exists(absolu):
                    continue
                if ('t-name="%s"' % nom_gabarit) in open(
                        absolu, encoding='utf-8').read():
                    return absolu
        return None

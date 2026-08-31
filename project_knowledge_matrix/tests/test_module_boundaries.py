"""Frontières du module — ce que le découpage ne doit pas casser.

Trois familles d'invariants :

* la dépendance ``hr``, retirée le 19 août, ne doit pas revenir par la bande ;
* les deux modèles du catalogue de logiciels, supprimés, doivent rester morts ;
* tout modèle porté par le module doit avoir des droits d'accès et un manifeste
  dont les fichiers existent — c'est le contrôle qui dira, aux chantiers 06 et
  07, qu'une extraction a oublié une ligne d'ACL ou un fichier de données.
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
        dépendance dure, pour un champ rempli sur 1 document sur 205. Un champ
        relationnel typé suffit à la faire revenir, et ça ne se voit qu'en
        installation neuve.
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
    # Gouvernance corporative — extraite, doit le rester
    # ------------------------------------------------------------------

    def test_no_relational_field_points_at_the_corporate_models(self):
        """La 12.0.0 a sorti les cinq modèles corporatifs du module.

        Un champ relationnel typé vers ``corporate.*`` rendrait la dépendance
        circulaire : ``bf_corporate_governance`` dépend du socle, le socle ne
        peut pas dépendre de lui. Comme pour ``hr`` au 19 août, ça ne se voit
        qu'en installation neuve — chez le prochain locataire, jamais ici.
        """
        champs = self._champs_du_module()
        self.assertTrue(champs, 'Aucun champ recensé pour %s' % MODULE)
        fautifs = [
            f'{champ.model}.{champ.name} → {champ.relation}'
            for champ in champs
            if champ.relation and champ.relation.split('.')[0] == 'corporate'
        ]
        self.assertFalse(fautifs, 'Champs relationnels vers corporate : %s' % fautifs)

    def test_the_module_declares_no_corporate_model(self):
        modeles = self._modeles_du_module()
        self.assertTrue(modeles, 'Aucun modèle recensé pour %s' % MODULE)
        fautifs = [m.model for m in modeles if m.model.startswith('corporate.')]
        self.assertFalse(fautifs, 'Modèles corporatifs restés dans le socle : %s' % fautifs)

    def test_the_dashboard_offers_an_extension_point(self):
        """Le bloc corporatif s'accroche à un gabarit nommé plutôt qu'à un
        xpath posé sur une ligne voisine, qui se décrocherait à la première
        retouche du tableau de bord.

        Le gabarit est un fichier d'asset, invisible au serveur : sans ce
        contrôle, le renommer casserait le tableau de bord du module qui
        s'accroche dessus, et rien ne le dirait ici.
        """
        chemin = os.path.join(
            get_module_path(MODULE), 'static', 'src', 'xml',
            'knowledge_dashboard.xml',
        )
        gabarit = open(chemin, encoding='utf-8').read()
        point = 'project_knowledge_matrix.DashboardExtraRows'
        self.assertIn('t-name="%s"' % point, gabarit, 'Point d\'extension absent')
        self.assertIn('t-call="%s"' % point, gabarit, 'Point d\'extension jamais appelé')

    # ------------------------------------------------------------------
    # Intégrité — les contrôles utiles au découpage
    # ------------------------------------------------------------------

    def test_every_model_of_the_module_has_access_rights(self):
        """Un modèle sans ACL est lisible de personne — et personne ne le voit.

        Odoo se contente d'un avertissement au journal. C'est le contrôle qui a
        signalé,, une ligne d'``ir.model.access.csv`` restée
        derrière — et celui qui le dira encore.
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

    def test_no_relational_field_points_at_the_credential_models(self):
        """La 13.0.0 a sorti le coffre du module.

        ``project.project.credential_ids`` était un ``One2many`` TYPÉ vers
        ``project.credential`` : tant qu'il restait ici, le socle dépendait
        durement du coffre et l'extraction rendait la dépendance circulaire.
        """
        champs = self._champs_du_module()
        self.assertTrue(champs, 'Aucun champ recensé pour %s' % MODULE)
        fautifs = [
            f'{champ.model}.{champ.name} → {champ.relation}'
            for champ in champs
            if champ.relation and champ.relation.startswith('project.credential')
        ]
        self.assertFalse(fautifs, 'Champs relationnels vers le coffre : %s' % fautifs)

    def test_no_template_reads_the_brand_fields_directly(self):
        """La marque se lit par `_pkm_brand()`, jamais par le champ.

        `report_brand_*` vient de `bf_onboarding_base` 18.0.2.0.0. Sa 1.0.0 ne
        l'a pas, et les deux tournent en production. Une lecture directe lève
        une AttributeError là où le champ manque — la garde
        « company and company.champ » teste la société, pas le champ.

        Le contrôle porte sur la SOURCE et non sur le registre : il vaut donc
        quel que soit le locataire qui l'exécute, y compris celui où le champ
        se trouve être présent.
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
                contenu = open(os.path.join(racine, fichier), encoding='utf-8').read()
                for ligne in contenu.splitlines():
                    if 'report_brand_' in ligne and '_pkm_brand' not in ligne \
                            and not ligne.strip().startswith('<!--') \
                            and 'AttributeError' not in ligne:
                        fautifs.append('%s/%s: %s' % (dossier, fichier, ligne.strip()[:80]))
        self.assertFalse(
            fautifs,
            'Lectures directes de report_brand_* (passer par _pkm_brand) : %s'
            % fautifs)

    def test_the_mail_templates_in_database_use_the_brand_helper(self):
        """Le contrôle qui manquait : ce que la BASE porte, pas le fichier.

        Les six gabarits sont `noupdate` EN BASE, quoi qu'en dise le fichier de
        données — `document_mail_templates.xml` déclare pourtant `noupdate="0"`.
        Une mise à niveau ordinaire ne réécrit donc PAS `body_html`, et un
        correctif apporté au fichier reste invisible en production
        indéfiniment.

        Le test précédent lit la SOURCE et passait au vert pendant que la base
        rendait encore l'ancienne version. Celui-ci lit la base.
        """
        gabarits = self.env['ir.model.data'].search([
            ('module', '=', MODULE), ('model', '=', 'mail.template'),
        ])
        self.assertTrue(gabarits, 'Aucun gabarit de courriel recensé')
        fautifs = []
        for data in gabarits:
            gabarit = self.env['mail.template'].browse(data.res_id).exists()
            if not gabarit:
                continue
            corps = gabarit.with_context(lang='en_US').body_html or ''
            if 'report_brand_' in corps and '_pkm_brand' not in corps:
                fautifs.append(data.name)
        self.assertFalse(
            fautifs,
            'Gabarits encore sur une lecture directe en base — la passe de '
            'migration 18.0.13.1.0 ne les a pas rafraîchis : %s' % fautifs)

    def test_cryptography_is_no_longer_a_declared_dependency(self):
        """Le coffre était le seul endroit du module qui chiffrait.

        Une installation neuve du socle ne demande plus ``cryptography``.
        """
        externes = get_manifest(MODULE).get('external_dependencies', {})
        self.assertNotIn('cryptography', externes.get('python', []))

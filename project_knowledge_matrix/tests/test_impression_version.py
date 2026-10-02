"""Le PDF d'un document imprime la version publiée, pas le brouillon.

Le défaut, constaté sur une démo : la politique de
prévention du harcèlement avait une 2.0 publiée et une 2.1 en approbation. Son
PDF disait « VERSION 2.0 » et imprimait le texte de la 2.1. Le rapport lisait
les sections VIVANTES du document (le brouillon de travail) et prenait son
étiquette dans ``current_version`` (la dernière version publiée) : deux sources
différentes pour le corps et pour le numéro.

Les mêmes sections vivantes passaient par ``_report_sections()`` au portail
d'accusé de réception, à la signature d'une distribution et à l'affichage d'une
procédure au poste de travail : le destinataire lisait, signait ou affichait un
texte que personne n'avait approuvé.

Règle éprouvée ici :

* le PDF par défaut d'un document imprime l'instantané figé de la version
  publiée en vigueur, avec le numéro de cette version ;
* le brouillon ne s'imprime que marqué comme tel, avec son propre numéro ;
* une version donnée s'imprime avec son propre contenu et son propre numéro ;
* jamais un numéro d'une version sur le corps d'une autre.
"""

import re

from odoo.exceptions import UserError
from odoo.tests import TransactionCase

RAPPORT_DOCUMENT = 'project_knowledge_matrix.report_document_body'
RAPPORT_BROUILLON = 'project_knowledge_matrix.report_document_draft'
RAPPORT_VERSION = 'project_knowledge_matrix.report_document_version_body'

TEXTE_V1 = 'TEXTE-PUBLIE-UN accusé de réception sous deux jours ouvrables.'
TEXTE_V2 = 'TEXTE-PUBLIE-DEUX décision sur la recevabilité sous dix jours.'
TEXTE_BROUILLON = 'TEXTE-BROUILLON personne-ressource externe accréditée.'

FILIGRANE_BROUILLON = 'BROUILLON, NON APPROUVÉ'


class TestImpressionVersion(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.type_politique = cls.env['project.document.type'].create({
            'name': 'Politique d\'essai',
            'code': 'ESSAI-IMPRESSION',
            'is_internal': True,
        })
        cls.doc = cls.env['project.document'].create({
            'name': 'Politique de prévention du harcèlement (essai)',
            'code': 'ESSAI-POL',
            'type_id': cls.type_politique.id,
            'body_source': 'internal',
        })
        Section = cls.env['project.document.section']
        cls.sec_politique = Section.create({
            'document_id': cls.doc.id,
            'code': 'POLITIQUE',
            'name': 'Politique',
            'sequence': 10,
            'content': '<p>%s</p>' % TEXTE_V1,
        })
        cls.sec_gouvernance = Section.create({
            'document_id': cls.doc.id,
            'code': 'GOUVERNANCE',
            'name': 'Gouvernance',
            'sequence': 90,
            'content_kind': 'computed',
            'render_key': 'governance',
        })

    # ------------------------------------------------------------------
    # Outils
    # ------------------------------------------------------------------

    def _ecrire(self, texte):
        self.sec_politique.content = '<p>%s</p>' % texte

    def _version(self, numero, etat='draft'):
        version = self.env['project.document.version'].create({
            'document_id': self.doc.id,
            'version_number': numero,
        })
        if etat == 'released':
            version.action_release()
        elif etat != 'draft':
            version.state = etat
        return version

    def _html(self, rapport, records):
        html, _genre = self.env['ir.actions.report']._render_qweb_html(
            rapport, records.ids)
        return html.decode()

    def _etiquette(self, html):
        """Le numéro imprimé dans la case « Version » de l'en-tête."""
        trouve = re.search(r'>Version</td>\s*<td[^>]*>(.*?)</td>', html, re.S)
        self.assertTrue(trouve, "Pas de case « Version » dans le PDF")
        return ' '.join(re.sub(r'<[^>]+>', ' ', trouve.group(1)).split())

    def _gouvernance(self, html):
        """La ligne « Version » du bloc de gouvernance imprimé."""
        trouve = re.search(
            r'o_pkm_governance.*?<strong>Version</strong></td>\s*<td>(.*?)</td>',
            html, re.S)
        return trouve and trouve.group(1).strip()

    # ------------------------------------------------------------------
    # Le PDF par défaut du document
    # ------------------------------------------------------------------

    def test_sans_version_publiee_le_pdf_est_un_brouillon_marque(self):
        html = self._html(RAPPORT_DOCUMENT, self.doc)
        self.assertIn(TEXTE_V1, html)
        self.assertIn('Brouillon', self._etiquette(html))
        self.assertIn(FILIGRANE_BROUILLON, html)

    def test_le_pdf_imprime_la_version_publiee_pas_la_suivante_en_approbation(self):
        """Le cas d'origine : 2.0 publiée, 2.1 en approbation, corps vivant en avance."""
        self._version('1.0', 'released')
        self._ecrire(TEXTE_V2)
        self._version('2.0', 'released')
        self._ecrire(TEXTE_BROUILLON)
        self._version('2.1', 'review')

        html = self._html(RAPPORT_DOCUMENT, self.doc)
        self.assertIn(TEXTE_V2, html)
        self.assertNotIn(TEXTE_BROUILLON, html)
        self.assertEqual(self._etiquette(html), '2.0')
        self.assertEqual(self._gouvernance(html), '2.0')
        self.assertNotIn(FILIGRANE_BROUILLON, html)

    def test_une_retouche_sans_nouvelle_version_ne_change_pas_le_pdf(self):
        self._version('1.0', 'released')
        self._ecrire(TEXTE_BROUILLON)
        html = self._html(RAPPORT_DOCUMENT, self.doc)
        self.assertIn(TEXTE_V1, html)
        self.assertNotIn(TEXTE_BROUILLON, html)
        self.assertEqual(self._etiquette(html), '1.0')

    def test_une_version_approuvee_non_publiee_ne_remplace_pas_la_publiee(self):
        self._version('1.0', 'released')
        self._ecrire(TEXTE_BROUILLON)
        self._version('1.1', 'approved')
        html = self._html(RAPPORT_DOCUMENT, self.doc)
        self.assertIn(TEXTE_V1, html)
        self.assertNotIn(TEXTE_BROUILLON, html)
        self.assertEqual(self._etiquette(html), '1.0')

    def test_les_sections_servies_aux_modules_compagnons_sont_publiees(self):
        """Portail d'accusé, signature et poste de travail lisent ce corps-là."""
        self._version('1.0', 'released')
        self._ecrire(TEXTE_BROUILLON)
        self._version('1.1', 'review')
        corps = ''.join(str(s['html']) for s in self.doc._report_sections())
        self.assertIn(TEXTE_V1, corps)
        self.assertNotIn(TEXTE_BROUILLON, corps)

    # ------------------------------------------------------------------
    # L'aperçu du brouillon
    # ------------------------------------------------------------------

    def test_l_apercu_du_brouillon_porte_son_numero_et_le_filigrane(self):
        self._version('1.0', 'released')
        self._ecrire(TEXTE_BROUILLON)
        self._version('1.1', 'review')

        html = self._html(RAPPORT_BROUILLON, self.doc)
        self.assertIn(TEXTE_BROUILLON, html)
        self.assertNotIn(TEXTE_V1, html)
        etiquette = self._etiquette(html)
        self.assertIn('1.1', etiquette)
        self.assertIn('Brouillon', etiquette)
        self.assertIn(FILIGRANE_BROUILLON, html)
        # Le bloc de gouvernance du brouillon ne se réclame pas de la 1.0.
        self.assertIn('1.1', self._gouvernance(html))

    def test_l_apercu_sans_version_en_preparation_ne_prend_pas_le_numero_publie(self):
        self._version('1.0', 'released')
        self._ecrire(TEXTE_BROUILLON)
        html = self._html(RAPPORT_BROUILLON, self.doc)
        self.assertIn(TEXTE_BROUILLON, html)
        self.assertNotIn('1.0', self._etiquette(html))
        self.assertNotEqual(self._gouvernance(html), '1.0')
        self.assertIn(FILIGRANE_BROUILLON, html)

    # ------------------------------------------------------------------
    # Une version imprimée explicitement
    # ------------------------------------------------------------------

    def test_une_version_remplacee_imprime_son_propre_contenu(self):
        v1 = self._version('1.0', 'released')
        self._ecrire(TEXTE_V2)
        self._version('2.0', 'released')
        self._ecrire(TEXTE_BROUILLON)
        self.assertEqual(v1.state, 'superseded')

        html = self._html(RAPPORT_VERSION, v1)
        self.assertIn(TEXTE_V1, html)
        self.assertNotIn(TEXTE_V2, html)
        self.assertNotIn(TEXTE_BROUILLON, html)
        self.assertIn('1.0', self._etiquette(html))
        self.assertEqual(self._gouvernance(html), '1.0')
        self.assertIn('REMPLACÉE', html)

    def test_la_version_publiee_imprimee_explicitement_est_celle_du_document(self):
        self._version('1.0', 'released')
        self._ecrire(TEXTE_V2)
        v2 = self._version('2.0', 'released')
        self._ecrire(TEXTE_BROUILLON)
        html = self._html(RAPPORT_VERSION, v2)
        self.assertIn(TEXTE_V2, html)
        self.assertNotIn(TEXTE_BROUILLON, html)
        self.assertEqual(self._etiquette(html), '2.0')
        self.assertNotIn(FILIGRANE_BROUILLON, html)

    def test_une_version_en_approbation_s_imprime_en_brouillon_avec_son_numero(self):
        self._version('1.0', 'released')
        self._ecrire(TEXTE_BROUILLON)
        v11 = self._version('1.1', 'review')
        html = self._html(RAPPORT_VERSION, v11)
        self.assertIn(TEXTE_BROUILLON, html)
        self.assertNotIn(TEXTE_V1, html)
        self.assertIn('1.1', self._etiquette(html))
        self.assertIn(FILIGRANE_BROUILLON, html)

    def test_une_version_approuvee_non_publiee_s_imprime_marquee(self):
        self._version('1.0', 'released')
        self._ecrire(TEXTE_BROUILLON)
        v11 = self._version('1.1', 'approved')
        html = self._html(RAPPORT_VERSION, v11)
        self.assertIn(TEXTE_BROUILLON, html)
        etiquette = self._etiquette(html)
        self.assertIn('1.1', etiquette)
        self.assertIn('non publiée', etiquette)
        self.assertIn('APPROUVÉE, NON PUBLIÉE', html)

    def test_une_version_remplacee_sans_instantane_n_imprime_pas_le_brouillon(self):
        """Publiée quand le corps vivait encore sur Nextcloud : rien de figé ici."""
        externe = self.env['project.document'].create({
            'name': 'Procédure externe puis rédigée', 'code': 'ESSAI-EXT',
            'type_id': self.type_politique.id,
        })
        ancienne = self.env['project.document.version'].create({
            'document_id': externe.id, 'version_number': '1.0'})
        ancienne.action_release()
        nouvelle = self.env['project.document.version'].create({
            'document_id': externe.id, 'version_number': '2.0'})
        nouvelle.action_release()
        externe.body_source = 'internal'
        self.env['project.document.section'].create({
            'document_id': externe.id, 'code': 'POLITIQUE', 'name': 'Politique',
            'content': '<p>%s</p>' % TEXTE_BROUILLON,
        })
        html = self._html(RAPPORT_VERSION, ancienne)
        self.assertNotIn(TEXTE_BROUILLON, html)

    # ------------------------------------------------------------------
    # Version publiée avant la rédaction dans Odoo
    # ------------------------------------------------------------------

    def test_version_publiee_sans_instantane_le_dit(self):
        """Cas hérité : publié en externe, rédigé dans Odoo ensuite.

        Odoo n'a jamais figé leur corps. On imprime le texte vivant sous le
        numéro publié, mais le PDF le dit : ce n'est pas un instantané.
        """
        externe = self.env['project.document'].create({
            'name': 'Procédure rédigée après publication', 'code': 'ESSAI-LEG',
            'type_id': self.type_politique.id,
        })
        version = self.env['project.document.version'].create({
            'document_id': externe.id, 'version_number': '2025.11'})
        version.action_release()
        self.assertFalse(version.section_ids)
        externe.body_source = 'internal'
        self.env['project.document.section'].create({
            'document_id': externe.id, 'code': 'POLITIQUE', 'name': 'Politique',
            'content': '<p>%s</p>' % TEXTE_V1,
        })
        html = self._html(RAPPORT_DOCUMENT, externe)
        self.assertIn(TEXTE_V1, html)
        self.assertEqual(self._etiquette(html), '2025.11')
        self.assertIn('pas été figé', html)
        self.assertNotIn(FILIGRANE_BROUILLON, html)

    # ------------------------------------------------------------------
    # Boutons
    # ------------------------------------------------------------------

    def test_les_boutons_ouvrent_le_bon_rapport(self):
        v1 = self._version('1.0', 'released')
        self._ecrire(TEXTE_BROUILLON)
        # Sans mise en page configurée, l'administrateur reçoit d'abord
        # l'assistant de mise en page au lieu du rapport.
        doc = self.doc.with_context(discard_logo_check=True)
        v1 = v1.with_context(discard_logo_check=True)
        self.assertEqual(doc.action_generate_body_pdf()['report_name'],
                         RAPPORT_DOCUMENT)
        self.assertEqual(doc.action_generate_draft_pdf()['report_name'],
                         RAPPORT_BROUILLON)
        self.assertEqual(v1.action_generate_body_pdf()['report_name'],
                         RAPPORT_VERSION)

    def test_une_ancienne_version_jamais_figee_refuse_l_impression(self):
        externe = self.env['project.document'].create({
            'name': 'Procédure externe', 'code': 'ESSAI-EXT2',
            'type_id': self.type_politique.id,
        })
        ancienne = self.env['project.document.version'].create({
            'document_id': externe.id, 'version_number': '1.0'})
        ancienne.action_release()
        nouvelle = self.env['project.document.version'].create({
            'document_id': externe.id, 'version_number': '2.0'})
        nouvelle.action_release()
        externe.body_source = 'internal'
        self.env['project.document.section'].create({
            'document_id': externe.id, 'code': 'POLITIQUE', 'name': 'Politique',
            'content': '<p>%s</p>' % TEXTE_BROUILLON,
        })
        with self.assertRaisesRegex(UserError, 'pas de contenu figé'):
            ancienne.action_generate_body_pdf()

    def test_le_gel_fige_la_gouvernance_de_la_version_gelee(self):
        """L'instantané porte le numéro de la version qu'on gèle, pas un autre."""
        v1 = self._version('1.0', 'released')
        gouvernance = v1.section_ids.filtered(lambda s: s.code == 'GOUVERNANCE')
        self.assertIn('1.0', gouvernance.content)

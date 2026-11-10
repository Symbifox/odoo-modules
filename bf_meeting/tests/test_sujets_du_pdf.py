"""Les sujets d'un compte rendu doivent partir avec le PDF, même sans JSON structuré.

Le corps du courriel recopiait les sujets et leurs points : mesuré le 2026-11-04 sur dix
comptes rendus réels, une médiane de 1206 mots et 60 puces par courriel, jusqu'à 1726 mots
et 97 puces. Il renvoie maintenant au PDF joint. Or le PDF tire ses sujets de
`structured_notes_json`, pas des enregistrements `meeting.topic` que le courriel lisait :
deux sources pour la même chose. Sur 950 comptes rendus de trois bases vivantes, 714
portaient des sujets au courriel et 3 n'en avaient aucun au PDF : leur PDF partait déjà
incomplet.

Trois promesses :
* le JSON structuré garde la main quand il porte des sujets ;
* sans lui, les sujets de la fiche prennent le relais, puces comprises ;
* le courriel ne recopie plus les sujets, et le PDF les porte.

⚠️ « Tâches existantes discutées » RESTE au courriel : le client doit lire la même liste
partout, et `test_discussed_tasks` l'épingle.
"""

from odoo.tests import TransactionCase, tagged

PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)


@tagged('post_install', '-at_install')
class TestSujetsDuPdf(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.meeting_logo = PNG_1PX
        projet = cls.env['project.project'].create({'name': 'Projet des sujets du PDF'})
        cls.commun = {
            'room_name': 'Séance', 'date': '2026-11-04 18:30:00',
            'project_id': projet.id,
        }
        # ⚠️ Ne PAS imposer `lang`. `meeting.record.lang` est une Selection bâtie sur
        # `res.lang.get_installed()` : sur une base neuve, qui ne porte que `en_US`,
        # `create({'lang': 'fr_CA'})` lève « Wrong value for meeting.record.lang ».
        # L'essai doit tenir chez quiconque installe le module, pas seulement ici.
        if cls.env['res.lang'].search_count([('code', '=', 'fr_CA'), ('active', '=', True)]):
            cls.commun['lang'] = 'fr_CA'

    def _rencontre(self, **extra):
        return self.env['meeting.record'].create(dict(self.commun, **extra))

    def test_sujets_de_la_fiche_quand_le_json_est_vide(self):
        """Sans JSON structuré, le PDF prend les sujets de la fiche."""
        cr = self._rencontre(name='Sans JSON')
        self.env['meeting.topic'].create({
            'meeting_id': cr.id, 'sequence': 10, 'name': 'Positionnement défense',
            'points_html': '<ul><li>Double usage confirm&#233;</li>'
                           '<li>Garde c&#244;ti&#232;re en premier</li></ul>',
        })
        sujets = cr._get_report_data()['topics']
        self.assertEqual([s['title'] for s in sujets], ['Positionnement défense'])
        self.assertEqual(sujets[0]['points'],
                         ['Double usage confirmé', 'Garde côtière en premier'])

    def test_le_json_structure_garde_la_main(self):
        """Quand le JSON porte des sujets, la fiche ne s'en mêle pas."""
        cr = self._rencontre(
            name='Avec JSON',
            structured_notes_json='{"topics": [{"title": "Du JSON", "points": ["un point"]}]}',
        )
        self.env['meeting.topic'].create({
            'meeting_id': cr.id, 'name': 'De la fiche',
            'points_html': '<ul><li>autre</li></ul>',
        })
        self.assertEqual([s['title'] for s in cr._get_report_data()['topics']], ['Du JSON'])

    def test_sujet_sans_puce_fait_un_seul_point(self):
        """Un points_html sans <li> n'est pas perdu : il devient un point."""
        cr = self._rencontre(name='Sans puce')
        self.env['meeting.topic'].create({
            'meeting_id': cr.id, 'name': 'Tour de table',
            'points_html': '<p>Rien &#224; signaler ce mois-ci.</p>',
        })
        self.assertEqual(cr._get_report_data()['topics'][0]['points'],
                         ['Rien à signaler ce mois-ci.'])

    def test_ordre_des_sujets(self):
        """Les sujets sortent dans l'ordre de leur séquence."""
        cr = self._rencontre(name='Ordre')
        for seq, titre in ((30, 'Troisième'), (10, 'Premier'), (20, 'Deuxième')):
            self.env['meeting.topic'].create({
                'meeting_id': cr.id, 'sequence': seq, 'name': titre,
                'points_html': '<ul><li>x</li></ul>',
            })
        self.assertEqual([s['title'] for s in cr._get_report_data()['topics']],
                         ['Premier', 'Deuxième', 'Troisième'])

    def test_le_pdf_rendu_porte_le_sujet(self):
        """Bout en bout : le PDF contient le titre du sujet et son point."""
        cr = self._rencontre(name='PDF bout en bout')
        self.env['meeting.topic'].create({
            'meeting_id': cr.id, 'name': 'Veille des achats publics',
            'points_html': '<ul><li>CanadaBuys et SEAO</li></ul>',
        })
        html = self.env['ir.actions.report']._render_qweb_html(
            'bf_meeting.action_report_meeting_record', cr.ids)[0]
        html = html.decode() if isinstance(html, bytes) else html
        self.assertIn('Veille des achats publics', html)
        self.assertIn('CanadaBuys et SEAO', html)

    def test_le_courriel_ne_recopie_plus_les_sujets(self):
        """Le corps du courriel renvoie au PDF au lieu de recopier les sujets."""
        cr = self._rencontre(name='Courriel court', summary='Un résumé.')
        self.env['meeting.topic'].create({
            'meeting_id': cr.id, 'name': 'Un sujet qui ne doit pas être dans le courriel',
            'points_html': '<ul><li>un point qui reste au PDF</li></ul>',
        })
        gabarit = self.env.ref('bf_meeting.meeting_report_mail_template')
        corps = gabarit._render_field('body_html', cr.ids, compute_lang=True)[cr.id]
        self.assertIn('Un résumé.', corps)
        self.assertNotIn('un point qui reste au PDF', corps)
        # Le gabarit rend dans la langue des lecteurs : vérifier les deux titres,
        # et accepter l'une ou l'autre des deux introductions.
        self.assertNotIn('Sujets abordés', corps)
        self.assertNotIn('Topics discussed', corps)
        self.assertTrue(
            'en PDF' in corps or 'as a PDF' in corps,
            "l'introduction ne renvoie pas au PDF joint")

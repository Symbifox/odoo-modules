"""Le logo d'une société secondaire doit survivre au trajet jusqu'au lecteur.

`/web/image/res.company/<id>/<champ>` ne sert la vraie image qu'aux sociétés
que l'usager ANONYME a le droit de lire, soit la seule société du site web.
Pour toute autre, Odoo avale l'AccessError et rend son image grise de
remplacement avec un HTTP 200. Le courriel de compte rendu et la page publique
de contribution perdaient donc leur logo sans qu'aucun code d'erreur ne le dise.

Ces tests parlent en HTTP, sans session, parce que c'est le seul point de vue
où le défaut existe : lu par l'ORM avec des droits d'usager interne, le champ
a toujours été correct, et c'est bien pour ça que le PDF, lui, sortait juste.

Les images d'essai sont trois PNG de 1 px de couleurs différentes : deux
réponses identiques voudraient dire que les deux sont l'image de remplacement,
et c'est exactement ce qu'on veut pouvoir distinguer.
"""

from odoo.tests import HttpCase, tagged

ROUGE = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=="
BLEU = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYPj/HwADAgH/5ncLrgAAAABJRU5ErkJggg=="
VERT = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNg+M/wHwAEAQH/cetH5QAAAABJRU5ErkJggg=="


@tagged('post_install', '-at_install')
class TestPublicCompanyLogo(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.maison = cls.env.company
        cls.maison.logo = ROUGE
        cls.autre = cls.env['res.company'].create({
            'name': 'Société Seconde',
            'logo': BLEU,
            'report_brand_logo': VERT,
        })

    def _get(self, chemin):
        r = self.url_open(chemin)
        return r.status_code, r.content

    def test_la_societe_secondaire_rend_son_propre_logo(self):
        """Le cœur du défaut : ce n'est ni le logo de la maison, ni le gris."""
        code_maison, img_maison = self._get(f'/meeting/logo/{self.maison.id}')
        code_autre, img_autre = self._get(f'/meeting/logo/{self.autre.id}')
        self.assertEqual(code_maison, 200)
        self.assertEqual(code_autre, 200)
        self.assertNotEqual(
            img_maison, img_autre,
            "les deux sociétés rendent la même image : c'est le remplacement")

    def test_la_variante_brand_prend_le_logo_de_marque(self):
        """Les deux appelants n'ont pas le même ordre, la variante le porte."""
        _, defaut = self._get(f'/meeting/logo/{self.autre.id}')
        _, marque = self._get(f'/meeting/logo/{self.autre.id}/brand')
        self.assertNotEqual(defaut, marque)

    def test_meeting_logo_passe_devant_le_logo_ordinaire(self):
        self.autre.meeting_logo = VERT
        _, avec = self._get(f'/meeting/logo/{self.autre.id}')
        self.autre.meeting_logo = False
        _, sans = self._get(f'/meeting/logo/{self.autre.id}')
        self.assertNotEqual(avec, sans)

    def test_une_variante_inconnue_ne_lit_pas_un_champ_arbitraire(self):
        """La liste blanche EST le contrôle d'accès de cette route.

        Elle est en sudo : si le nom du champ voyageait dans l'URL, n'importe
        qui lirait n'importe quel champ de `res.company`.
        """
        for variante in ('favicon', 'name', 'inexistante'):
            with self.subTest(variante=variante):
                code, _ = self._get(f'/meeting/logo/{self.autre.id}/{variante}')
                self.assertEqual(code, 404)

    def test_une_societe_inconnue_rend_404(self):
        code, _ = self._get('/meeting/logo/999999')
        self.assertEqual(code, 404)

    def test_les_gabarits_ne_pointent_plus_web_image(self):
        """Un seul appel direct qui repasse, et le logo redevient gris."""
        for xmlid in ('bf_meeting.meeting_report_mail_template',
                      'bf_meeting.meeting_agenda_mail_template'):
            with self.subTest(gabarit=xmlid):
                corps = self.env.ref(xmlid).body_html or ''
                self.assertNotIn('/web/image/res.company', corps)
                self.assertIn('/meeting/logo/', corps)
        arch = self.env.ref('bf_meeting.agenda_contrib_page').arch_db or ''
        self.assertNotIn('/web/image/res.company', arch)

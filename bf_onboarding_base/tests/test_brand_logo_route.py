"""Le logo d'une société secondaire doit survivre au trajet jusqu'au lecteur.

`/web/image/res.company/<id>/<champ>` ne sert la vraie image qu'aux sociétés que
l'usager ANONYME a le droit de lire, soit la seule société du site web. Pour
toute autre, Odoo avale l'AccessError et rend son image grise de remplacement
avec un HTTP 200. Tout courriel brandé et toute page publique perdaient donc le
logo d'une société secondaire, sans aucun code d'erreur, alors que les PDF,
rendus côté serveur avec les droits d'un usager interne, restaient corrects.

Ces tests parlent en HTTP, sans session : c'est le seul point de vue où le
défaut existe.

Les images d'essai sont trois PNG de 1 px de couleurs différentes. Deux réponses
identiques voudraient dire que les deux sont l'image de remplacement, et c'est
exactement ce qu'on veut pouvoir distinguer sans connaître son empreinte.
"""

from odoo.tests import HttpCase, tagged

ROUGE = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=="
BLEU = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYPj/HwADAgH/5ncLrgAAAABJRU5ErkJggg=="
VERT = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNg+M/wHwAEAQH/cetH5QAAAABJRU5ErkJggg=="


@tagged('post_install', '-at_install')
class TestBrandLogoRoute(HttpCase):

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
        """Le coeur du défaut : ni le logo de la maison, ni le gris."""
        code_maison, img_maison = self._get(f'/brand/logo/{self.maison.id}')
        code_autre, img_autre = self._get(f'/brand/logo/{self.autre.id}')
        self.assertEqual(code_maison, 200)
        self.assertEqual(code_autre, 200)
        self.assertNotEqual(
            img_maison, img_autre,
            "les deux sociétés rendent la même image : c'est le remplacement")

    def test_la_variante_brand_prend_le_logo_de_marque(self):
        _, defaut = self._get(f'/brand/logo/{self.autre.id}')
        _, marque = self._get(f'/brand/logo/{self.autre.id}/brand')
        self.assertNotEqual(defaut, marque)

    def test_la_variante_own_garde_le_logo_ordinaire_devant(self):
        """`own` ressemble à `logo` tant qu'aucune société n'a l'un sans l'autre.

        Le jour où une société n'a QUE son logo de marque, les deux variantes
        divergent, et c'est pour ce jour là qu'elles restent distinctes.
        """
        _, propre = self._get(f'/brand/logo/{self.autre.id}/own')
        _, simple = self._get(f'/brand/logo/{self.autre.id}/logo')
        self.assertEqual(propre, simple)

        sans_logo = self.env['res.company'].create({
            'name': 'Société Sans Logo', 'report_brand_logo': VERT})
        sans_logo.partner_id.image_1920 = False
        _, propre = self._get(f'/brand/logo/{sans_logo.id}/own')
        _, marque = self._get(f'/brand/logo/{sans_logo.id}/brand')
        self.assertEqual(propre, marque)

    def test_une_variante_inconnue_ne_lit_pas_un_champ_arbitraire(self):
        """La liste blanche EST le contrôle d'accès de cette route.

        Elle lit en sudo : si le nom du champ voyageait dans l'URL, n'importe
        qui lirait n'importe quel champ de `res.company`.
        """
        for variante in ('favicon', 'name', 'inexistante'):
            with self.subTest(variante=variante):
                code, _ = self._get(f'/brand/logo/{self.autre.id}/{variante}')
                self.assertEqual(code, 404)

    def test_une_societe_inconnue_rend_404(self):
        code, _ = self._get('/brand/logo/999999')
        self.assertEqual(code, 404)

    def test_la_variante_meeting_ne_casse_pas_sans_bf_meeting(self):
        """`meeting_logo` est déclaré par un module qui dépend de celui ci.

        La route doit répondre quand même, en retombant sur `logo`.
        """
        code, img = self._get(f'/brand/logo/{self.autre.id}/meeting')
        self.assertEqual(code, 200)
        if 'meeting_logo' not in self.env['res.company']._fields:
            _, simple = self._get(f'/brand/logo/{self.autre.id}/logo')
            self.assertEqual(img, simple)

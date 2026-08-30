"""La photo de la page.

Trouvé au QA du 2026-08-30 : servie par `/web/image/bf.linkpage/<id>/avatar`,
elle arrivait au visiteur sous forme d'IMAGE DE REMPLACEMENT, en 200. L'usager
public n'a aucun droit de lecture sur le modèle, et `/web/image` répond alors
par une silhouette générique plutôt que par une erreur. La page s'affichait donc
sans que rien ne signale la panne.

D'où la forme des assertions : elles comparent les OCTETS servis à ceux stockés.
Un contrôle sur le code 200 ou sur l'entête `image/png` aurait été vert pendant
toute la durée du défaut.
"""

import base64
import io

from PIL import Image

from odoo.tests import HttpCase, tagged


def _png(couleur):
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), couleur).save(buf, format="PNG")
    return buf.getvalue()


@tagged("bf_linkpage", "post_install", "-at_install")
class TestAvatar(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brut = _png((41, 171, 225))
        cls.partner = cls.env["res.partner"].create({"name": "Avatar"})
        cls.page = cls.env["bf.linkpage"].create({
            "name": "Avatar", "slug": "avatar-test", "kind": "owner",
            "partner_id": cls.partner.id, "state": "published",
            "avatar": base64.b64encode(cls.brut)})

    def test_la_photo_servie_est_bien_la_photo_stockee(self):
        self.env.flush_all()
        response = self.url_open("/l/avatar-test/avatar", allow_redirects=False, headers={"Accept-Language": "fr-CA,fr;q=0.9"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content, base64.b64decode(self.page.avatar),
            "les octets servis au visiteur doivent être la photo, pas une "
            "image de remplacement",
        )

    def test_web_image_ne_sert_toujours_pas_la_photo(self):
        """Verrouille la raison d'être de la route du module.

        Si `/web/image` se mettait un jour à servir la vraie photo au public,
        la route du module deviendrait superflue — et ce test le dirait. Tant
        qu'il passe, retirer la route casse la page.
        """
        self.env.flush_all()
        response = self.url_open(
            "/web/image/bf.linkpage/%s/avatar" % self.page.id, allow_redirects=False, headers={"Accept-Language": "fr-CA,fr;q=0.9"})
        self.assertNotEqual(
            response.content, base64.b64decode(self.page.avatar),
            "voir le commentaire : /web/image sert une image de remplacement",
        )

    def test_page_hors_ligne_ne_divulgue_pas_sa_photo(self):
        self.page.state = "draft"
        self.env.flush_all()
        response = self.url_open("/l/avatar-test/avatar", allow_redirects=False, headers={"Accept-Language": "fr-CA,fr;q=0.9"})
        self.assertEqual(response.status_code, 404)

    def test_page_sans_photo_rend_404(self):
        self.page.avatar = False
        self.env.flush_all()
        response = self.url_open("/l/avatar-test/avatar", allow_redirects=False, headers={"Accept-Language": "fr-CA,fr;q=0.9"})
        self.assertEqual(response.status_code, 404)

    def test_la_page_pointe_vers_la_route_du_module(self):
        """Le chaînon manquant, trouvé par mutation le 2026-08-30.

        Deux tests vérifiaient chacun une route, mais AUCUN ne vérifiait vers
        laquelle la page pointe. Remettre `/web/image` dans le gabarit
        réintroduisait donc le défaut sans faire rougir personne : les deux
        routes se comportaient toujours comme annoncé, la page ne les appelait
        simplement plus dans le bon ordre.
        """
        self.env.flush_all()
        html = self.url_open("/l/avatar-test", allow_redirects=False, headers={"Accept-Language": "fr-CA,fr;q=0.9"}).text
        self.assertIn("/l/avatar-test/avatar", html,
                      "la page doit servir la photo par la route du module")
        self.assertNotIn("/web/image/bf.linkpage", html,
                         "/web/image sert une image de remplacement au visiteur")

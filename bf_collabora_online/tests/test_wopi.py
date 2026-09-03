import json
import time

from odoo.tests.common import HttpCase, new_test_user, tagged

from odoo.addons.collabora_odoo.utils import jwt as jwt_amont


@tagged("post_install", "-at_install")
class TestReponseWopi(HttpCase):
    """`IsAdminUser` doit dire la vérité, pour chacun.

    On interroge la vraie route WOPI avec un vrai jeton, comme le ferait le
    serveur Collabora. Rien n'est simulé : c'est le contrôleur amont qui répond,
    et notre greffe qui corrige la valeur.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        param = cls.env["ir.config_parameter"].sudo()
        param.set_param("cool_jwt_secret", "secret-de-banc-collabora-25265")
        param.set_param("cool_wopi_host_url", cls.base_url())
        param.set_param("cool_public_url", "https://cool.invalide")
        cls.ordinaire = new_test_user(
            cls.env, login="wopi_ordinaire", groups="base.group_user")
        cls.systeme = new_test_user(
            cls.env, login="wopi_systeme", groups="base.group_user,base.group_system")

    def _piece_pour(self, usager):
        return self.env["ir.attachment"].with_user(usager).create({
            "name": "document.odt",
            "raw": b"contenu du document",
            "mimetype": "application/vnd.oasis.opendocument.text",
        })

    def _check_file_info(self, usager, piece):
        class RequeteFactice:
            env = self.env

        jeton = jwt_amont.make_token(
            RequeteFactice, usager.id, piece.id, int(time.time()) + 3600, True)
        self.assertNotIn("error", jeton, jeton)
        self.env.flush_all()
        reponse = self.url_open(
            "/collabora_odoo/wopi/files/%s?access_token=%s" % (piece.id, jeton["token"]))
        self.assertEqual(reponse.status_code, 200, reponse.text[:300])
        return json.loads(reponse.text)

    def test_usager_ordinaire_n_est_pas_administrateur(self):
        piece = self._piece_pour(self.ordinaire)
        info = self._check_file_info(self.ordinaire, piece)
        self.assertIs(info["IsAdminUser"], False,
                      "l'amont pose True en dur : la greffe doit corriger")

    def test_administrateur_reste_administrateur(self):
        """Le correctif doit discriminer, pas tout mettre à False."""
        piece = self._piece_pour(self.systeme)
        info = self._check_file_info(self.systeme, piece)
        self.assertIs(info["IsAdminUser"], True)

    def test_le_reste_de_la_reponse_est_intact(self):
        """On corrige une valeur, on ne réécrit pas le CheckFileInfo."""
        piece = self._piece_pour(self.ordinaire)
        info = self._check_file_info(self.ordinaire, piece)
        self.assertEqual(info["BaseFileName"], "document.odt")
        self.assertEqual(info["Size"], len(b"contenu du document"))
        self.assertTrue(info["UserCanWrite"])
        self.assertEqual(info["UserId"], self.ordinaire.id)
        self.assertIs(info["IsAnonymousUser"], False)

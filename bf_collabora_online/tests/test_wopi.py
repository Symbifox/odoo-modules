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


@tagged("post_install", "-at_install")
class TestSocietes(HttpCase):
    """🔴 Un document rattaché à une AUTRE société doit s'ouvrir quand même.

    `cool_frame` est déclarée `website=True` en amont, et Odoo force alors
    `allowed_company_ids` à la société du site. Sans le correctif, tout document
    d'une seconde société renvoie 403, et cocher la société dans le sélecteur
    n'y change rien. Mesuré en production le 2026-09-02.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        param = cls.env["ir.config_parameter"].sudo()
        param.set_param("cool_jwt_secret", "secret-de-banc-societes-25265")
        param.set_param("cool_wopi_host_url", cls.base_url())
        param.set_param("cool_public_url", "https://cool.invalide")
        cls.seconde = cls.env["res.company"].create({"name": "Seconde société"})
        cls.personne = new_test_user(
            cls.env, login="soc_personne", groups="base.group_user,base.group_partner_manager")
        cls.personne.write({"company_ids": [(4, cls.seconde.id)]})

    def _piece_dans_la_seconde(self):
        """Une pièce accrochée à un enregistrement de la seconde société."""
        partenaire = self.env["res.partner"].create({
            "name": "Client de la seconde", "company_id": self.seconde.id})
        return self.env["ir.attachment"].sudo().create({
            "name": "contrat.odt", "raw": b"contenu",
            "mimetype": "application/vnd.oasis.opendocument.text",
            "res_model": "res.partner", "res_id": partenaire.id,
        })

    def test_le_contexte_de_societes_est_retabli_depuis_le_temoin(self):
        piece = self._piece_dans_la_seconde()
        self.env.flush_all()
        self.authenticate("soc_personne", "soc_personne")
        # Ce que le navigateur envoie quand les deux cases sont cochées.
        self.opener.cookies.set(
            "cids", "%s-%s" % (self.personne.company_id.id, self.seconde.id))
        reponse = self.url_open("/collabora_odoo/frame/%s/read" % piece.id)
        self.assertNotEqual(
            reponse.status_code, 403,
            "la couche site web écrase les sociétés : le cadre doit les rétablir")

    def test_la_societe_principale_seule_suffit_grace_au_repli(self):
        """Sans témoin, on retombe sur TOUTES les sociétés de la personne.

        C'est ce qu'elle obtiendrait en cochant toutes ses cases, jamais plus.
        """
        piece = self._piece_dans_la_seconde()
        self.env.flush_all()
        self.authenticate("soc_personne", "soc_personne")
        reponse = self.url_open("/collabora_odoo/frame/%s/read" % piece.id)
        self.assertNotEqual(reponse.status_code, 403)

    def test_une_societe_hors_de_ses_droits_n_est_pas_accordee(self):
        """Le correctif ne doit pas se laisser dicter les sociétés par le témoin."""
        etrangere = self.env["res.company"].create({"name": "Société étrangère"})
        partenaire = self.env["res.partner"].create({
            "name": "Hors droits", "company_id": etrangere.id})
        piece = self.env["ir.attachment"].sudo().create({
            "name": "interdit.odt", "raw": b"x",
            "mimetype": "application/vnd.oasis.opendocument.text",
            "res_model": "res.partner", "res_id": partenaire.id,
        })
        self.env.flush_all()
        self.authenticate("soc_personne", "soc_personne")
        self.opener.cookies.set("cids", str(etrangere.id))
        reponse = self.url_open("/collabora_odoo/frame/%s/read" % piece.id)
        self.assertEqual(reponse.status_code, 403,
                         "un témoin bricolé ne doit rien ouvrir de plus")

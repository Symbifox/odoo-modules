"""Les trois portes, jouées en HTTP.

🔴 L'essai qui compte le plus est celui du GET : tant qu'un geste qui écrit
n'est pas parti en POST, l'adresse doit pouvoir être ouverte par un aperçu de
lien, un antipourriel ou un scanner sans que rien ne bouge.
"""
import json
import re

from odoo.tests import HttpCase, tagged

API = "/bf_nfc/mobile/v1"
CLE_USINE = "00" * 16
PICC = "EF963FF7828658A599F3041510671E88"
CMAC = "94EED9EE65337086"
UID = "04DE5F1EACC040"


@tagged("post_install", "-at_install")
class TestPortes(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.geste_open = cls.env.ref("bf_nfc.gesture_open")
        cls.partenaire = cls.env["res.partner"].create({"name": "Cible HTTP"})
        cls.tag_lecture = cls.env["bf.nfc.tag"].create({
            "name": "Lecture seule", "gesture_id": cls.geste_open.id,
            "res_model": "res.partner", "res_id": cls.partenaire.id,
        })
        action = cls.env["ir.actions.server"].create({
            "name": "Marquer la référence",
            "model_id": cls.env["ir.model"]._get_id("res.partner"),
            "state": "code",
            "code": "record.write({'ref': 'TAPE'})",
        })
        cls.geste_ecrit = cls.env["bf.nfc.gesture"].create({
            "name": "Marquer", "code": "essai_marquer", "kind": "server_action",
            "writes": True, "server_action_id": action.id,
        })
        cls.tag_ecrit = cls.env["bf.nfc.tag"].create({
            "name": "Écrit", "gesture_id": cls.geste_ecrit.id,
            "res_model": "res.partner", "res_id": cls.partenaire.id,
        })

    # ------------------------------------------------------------------
    # Porte « navigateur »
    # ------------------------------------------------------------------
    def test_code_inconnu_rend_un_404_franc(self):
        """Pas de redirection silencieuse : une pastille gravée ne se corrige plus."""
        self.authenticate("admin", "admin")
        reponse = self.url_open("/nfc/ZZZZZZZZ", allow_redirects=False)
        self.assertEqual(reponse.status_code, 404)

    def test_sans_session_on_passe_par_la_connexion(self):
        reponse = self.url_open("/nfc/%s" % self.tag_lecture.code, allow_redirects=False)
        self.assertIn(reponse.status_code, (302, 303))
        self.assertIn("/web/login", reponse.headers.get("Location", ""))

    def test_geste_sans_ecriture_ouvre_directement(self):
        self.authenticate("admin", "admin")
        reponse = self.url_open("/nfc/%s" % self.tag_lecture.code, allow_redirects=False)
        self.assertIn(reponse.status_code, (302, 303))
        self.assertIn("/mail/view", reponse.headers.get("Location", ""))

    def test_un_get_sur_un_geste_qui_ecrit_n_ecrit_rien(self):
        """🔴 La barrière. Dix GET ne doivent rien produire."""
        self.authenticate("admin", "admin")
        for _ in range(10):
            reponse = self.url_open("/nfc/%s" % self.tag_ecrit.code)
            self.assertEqual(reponse.status_code, 200)
            self.assertIn("Confirmer", reponse.text)
        self.partenaire.invalidate_recordset(["ref"])
        self.assertFalse(self.partenaire.ref)
        self.assertFalse(self.tag_ecrit.tap_ids,
                         "Un GET ne laisse même pas de ligne de journal : rien n'a été tenté.")

    def test_le_post_agit(self):
        self.authenticate("admin", "admin")
        reponse = self.url_open(
            "/nfc/%s/agir" % self.tag_ecrit.code,
            data={"csrf_token": self._csrf_de_la_page(self.tag_ecrit.code)},
            allow_redirects=False,
        )
        self.assertEqual(reponse.status_code, 200)
        self.partenaire.invalidate_recordset(["ref"])
        self.assertEqual(self.partenaire.ref, "TAPE")

    def _csrf_de_la_page(self, code):
        """Le jeton du formulaire, lu comme un navigateur le lirait.

        ⚠️ Pas une recherche de chaîne fixe : avec le module Site web, la page
        rendue à un administrateur glisse des attributs d'édition (`data-oe-*`)
        ENTRE `name` et `value`. L'essai tombait alors que la page était juste.
        """
        page = self.url_open("/nfc/%s" % code).text
        trouve = re.search(r'<input[^>]*name="csrf_token"[^>]*value="([^"]+)"', page)
        self.assertTrue(trouve, "Page de confirmation sans jeton CSRF")
        return trouve.group(1)

    # ------------------------------------------------------------------
    # Porte « application »
    # ------------------------------------------------------------------
    def test_la_sonde_de_capacite_repond_sans_jeton(self):
        """L'app doit pouvoir savoir que le module est là avant de se connecter."""
        reponse = self.url_open(API + "/ping")
        self.assertEqual(reponse.status_code, 200)
        charge = reponse.json()
        self.assertTrue(charge["ok"])
        self.assertEqual(charge["module"], "bf_nfc")
        self.assertGreaterEqual(charge["api"], 1)

    def test_le_ping_porte_la_marque_du_locataire(self):
        """L'application se peint AVANT l'appariement, sinon elle se repeint
        sous les yeux de la personne."""
        self.env.company.write({"primary_color": "#123456", "secondary_color": "#654321"})
        charge = self.url_open(API + "/ping").json()
        self.assertEqual(charge["branding"]["primary"], "#123456")
        self.assertEqual(charge["branding"]["dark"], "#654321")
        self.assertEqual(charge["branding"]["name"], self.env.company.name)

    def test_les_couleurs_d_usine_d_odoo_ne_sont_pas_une_marque(self):
        """🔴 Une société qui porte encore le mauve d'Odoo n'a rien choisi.

        Les prendre pour la marque du locataire peindrait l'application en
        mauve Odoo, ce que ce produit existe justement pour éviter.
        """
        self.env.company.write({"primary_color": "#714B67", "secondary_color": "#875A7B"})
        charge = self.url_open(API + "/ping").json()
        self.assertIsNone(charge["branding"]["primary"])
        self.assertIsNone(charge["branding"]["dark"])

    def test_une_couleur_malformee_ne_casse_pas_le_ping(self):
        self.env.company.write({"primary_color": "bleu", "secondary_color": "#12345"})
        charge = self.url_open(API + "/ping").json()
        self.assertTrue(charge["ok"])
        self.assertIsNone(charge["branding"]["primary"])

    def test_sans_jeton_l_application_recoit_401(self):
        reponse = self.url_open(
            API + "/tap", data=json.dumps({"code": self.tag_lecture.code}),
            headers={"Content-Type": "application/json"})
        self.assertEqual(reponse.status_code, 401)

    def test_jeton_bidon_recoit_401(self):
        reponse = self.url_open(
            API + "/tap", data=json.dumps({"code": self.tag_lecture.code}),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer pas-un-vrai-jeton"})
        self.assertEqual(reponse.status_code, 401)

    def test_le_catalogue_et_la_gravure_exigent_un_jeton(self):
        self.assertEqual(self.url_open(API + "/catalogue").status_code, 401)
        self.assertEqual(self.url_open(
            API + "/pastille", data=json.dumps({"geste": "open"}),
            headers={"Content-Type": "application/json"}).status_code, 401)

    def test_la_route_courte_du_tag_ne_mange_pas_celles_de_l_app(self):
        """⚠️ /nfc/<code> et /bf_nfc/mobile/v1/... doivent rester distincts.

        Un code de pastille ne peut pas ressembler à un chemin de l'app, mais
        c'est le genre de collision qui ne se voit qu'une fois déployé.
        """
        self.authenticate("admin", "admin")
        self.assertEqual(self.url_open("/nfc/ZZZZZZZZ", allow_redirects=False).status_code, 404)
        self.assertEqual(self.url_open(API + "/ping").status_code, 200)

    def test_application_appairee_agit_au_nom_de_la_personne(self):
        """La porte retenue par la maison, jouée de bout en bout.

        L'appareil est apparié par le vrai chemin (code à usage unique plus
        vérificateur PKCE), pas en écrivant une empreinte à la main : c'est ce
        que fait le téléphone, donc c'est ce que doit faire l'essai.
        """
        appareil, jeton = self._appareil_d_essai()
        reponse = self.url_open(
            API + "/tap", data=json.dumps({"code": self.tag_lecture.code}),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer %s" % jeton})
        self.assertEqual(reponse.status_code, 200)
        charge = reponse.json()
        self.assertEqual(charge["statut"], "ok")
        ligne = self.env["bf.nfc.tap"].search([("tag_id", "=", self.tag_lecture.id)], limit=1)
        self.assertEqual(ligne.user_id, appareil.user_id)
        self.assertEqual(ligne.door, "app")

    def test_le_geste_porte_le_nom_de_qui_tape_pas_celui_du_serveur(self):
        """🔴 C'est LA propriété qui a fait retenir cette porte."""
        appareil, jeton = self._appareil_d_essai(login="tapeur-app")
        self.url_open(
            API + "/tap", data=json.dumps({"code": self.tag_lecture.code}),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer %s" % jeton})
        ligne = self.env["bf.nfc.tap"].search(
            [("tag_id", "=", self.tag_lecture.id)], limit=1, order="id desc")
        self.assertEqual(ligne.user_id, appareil.user_id)
        self.assertNotEqual(ligne.user_id, self.env.ref("base.user_root"))

    def test_la_deconnexion_coupe_le_jeton(self):
        _appareil, jeton = self._appareil_d_essai(login="partante")
        entetes = {"Content-Type": "application/json",
                   "Authorization": "Bearer %s" % jeton}
        self.assertEqual(self.url_open(API + "/logout", data="{}",
                                       headers=entetes).status_code, 200)
        self.assertEqual(self.url_open(
            API + "/tap", data=json.dumps({"code": self.tag_lecture.code}),
            headers=entetes).status_code, 401)

    def _appareil_d_essai(self, login="apparie-http"):
        """Un appareil apparié par le vrai chemin."""
        import base64
        import hashlib
        from odoo.tests import new_test_user
        personne = self.env["res.users"].search([("login", "=", login)], limit=1)
        if not personne:
            personne = new_test_user(self.env, login=login, groups="base.group_user")
        verificateur = "verificateur-http-assez-long-pour-etre-serieux"
        condense = hashlib.sha256(verificateur.encode()).digest()
        defi = base64.urlsafe_b64encode(condense).decode().rstrip("=")
        Device = self.env["bf.nfc.device"]
        code = Device._issue_pending(personne.id, name="Pixel HTTP", challenge=defi)
        return Device._exchange(code, verificateur)

    # ------------------------------------------------------------------
    # Porte « pastille signée »
    # ------------------------------------------------------------------
    def _preparer_signee(self, geste=None):
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_nfc.sdm_meta_key", CLE_USINE)
        icp.set_param("bf_nfc.sdm_file_key", CLE_USINE)
        # 🔴 Et la paire RANGÉE, pas seulement les vieux paramètres. Une base qui
        # porte déjà une paire (toute instance où la gestion a posé ses clés)
        # l'emporte sur eux : `_cles_de` lit la ligne d'abord. Sans cette pose,
        # ces essais tombaient sur une base semée, avec un « 0 != 61 » qui se lit
        # comme un compteur non retenu alors que c'est la clé qui n'est pas la
        # bonne. Mesuré le 2026-09-18 en jouant la montée 2.3.1 → 2.4.0.
        self.env["bf.nfc.sdm.key"]._poser(self.env.company, CLE_USINE, CLE_USINE)
        return self.env["bf.nfc.tag"].create({
            "name": "Pastille signée",
            "gesture_id": (geste or self.geste_open).id,
            "res_model": "res.partner", "res_id": self.partenaire.id,
            "sdm_enabled": True, "sdm_uid": UID,
            "user_id": self.env.ref("base.user_admin").id,
        })

    def test_sans_signature_la_porte_signee_refuse(self):
        self._preparer_signee()
        reponse = self.url_open("/nfc/s")
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("signature", reponse.text)

    def test_signature_valide_agit_sans_compte(self):
        """La porte signée rend une page, elle ne redirige jamais.

        ⚠️ Celle ou celui qui tape une pastille signée n'a pas de compte : une
        redirection vers une fiche du client web le déposerait sur un écran de
        connexion. La page de résultat porte le lien, et c'est tout.
        """
        tag = self._preparer_signee()
        reponse = self.url_open(
            "/nfc/s?picc_data=%s&cmac=%s" % (PICC, CMAC), allow_redirects=False)
        self.assertEqual(reponse.status_code, 200)
        tag.invalidate_recordset(["sdm_counter"])
        self.assertEqual(tag.sdm_counter, 61,
                         "Le compteur de la puce doit être retenu, sinon le rejeu passe.")

    def test_rejouer_la_meme_adresse_est_refuse(self):
        """🔴 C'est tout l'intérêt de la puce signée : l'URL ne sert qu'une fois."""
        self._preparer_signee()
        self.url_open("/nfc/s?picc_data=%s&cmac=%s" % (PICC, CMAC), allow_redirects=False)
        rejeu = self.url_open("/nfc/s?picc_data=%s&cmac=%s" % (PICC, CMAC))
        self.assertEqual(rejeu.status_code, 200)
        self.assertIn("déjà servi", rejeu.text)

    def test_cmac_altere_refuse(self):
        self._preparer_signee()
        reponse = self.url_open("/nfc/s?picc_data=%s&cmac=%s" % (PICC, "94EED9EE65337087"))
        self.assertIn("signature", reponse.text)

    def test_pastille_signee_sans_compte_designe_refuse(self):
        tag = self._preparer_signee()
        tag.user_id = False
        reponse = self.url_open("/nfc/s?picc_data=%s&cmac=%s" % (PICC, CMAC))
        self.assertIn("aucun compte", reponse.text)

    def test_signature_valide_avec_les_cles_posees_par_la_gestion(self):
        """Le chemin de la 2.3.0 : la paire chiffrée de ``bf.nfc.sdm.key``, sans paramètre système."""
        tag = self._preparer_signee()
        icp = self.env["ir.config_parameter"].sudo()
        icp.search([("key", "in", ("bf_nfc.sdm_meta_key", "bf_nfc.sdm_file_key"))]).unlink()
        self.env["bf.nfc.sdm.key"]._poser(self.env.company, CLE_USINE, CLE_USINE)
        reponse = self.url_open(
            "/nfc/s?picc_data=%s&cmac=%s" % (PICC, CMAC), allow_redirects=False)
        self.assertEqual(reponse.status_code, 200)
        tag.invalidate_recordset(["sdm_counter"])
        self.assertEqual(tag.sdm_counter, 61)

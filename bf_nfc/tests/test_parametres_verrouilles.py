"""Ce qu'une pastille fait se décide à sa création, jamais au tapotement.

🔴 Jusqu'à la 2.2.0, la chaîne de requête du navigateur et le dict ``params`` de
l'application s'ajoutaient par-dessus les paramètres de la pastille. Ces essais
jouent les trois canaux par lesquels quelqu'un pouvait les remplacer : l'adresse
de la page, le corps envoyé par l'application, et l'appel direct.

Et un second défaut du même lot : « Ouvrir une adresse » tapée par le navigateur
restait sur le domaine de l'instance, parce que ``request.redirect`` d'Odoo 18
est local par défaut.
"""
import base64
import hashlib
import json

from odoo.tests import HttpCase, new_test_user, tagged

API = "/bf_nfc/mobile/v1"
ADRESSE = "https://symbifox.com/procedure"
PIEGE = "https://piege.example/hameconnage"


@tagged("post_install", "-at_install")
class TestParametresVerrouilles(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("bf_nfc.fenetre_doublon_secondes", "0")
        cls.tag_adresse = cls.env["bf.nfc.tag"].create({
            "name": "Procédure d'urgence",
            "gesture_id": cls.env.ref("bf_nfc.gesture_url").id,
            "params": json.dumps({"url": ADRESSE}),
        })

    def test_les_parametres_sont_ceux_de_la_pastille_seulement(self):
        self.assertEqual(self.tag_adresse._params(), {"url": ADRESSE})

    def test_taper_n_accepte_plus_de_parametres(self):
        """L'appel direct ne connaît plus ce canal : il lève au lieu d'obéir."""
        with self.assertRaises(TypeError):
            self.tag_adresse.taper("app", params={"url": PIEGE})

    def test_le_navigateur_suit_l_adresse_hors_de_l_instance(self):
        """🔴 Avant : ``https://symbifox.com/procedure`` devenait ``/procedure``."""
        self.authenticate("admin", "admin")
        reponse = self.url_open("/nfc/%s" % self.tag_adresse.code, allow_redirects=False)
        self.assertIn(reponse.status_code, (302, 303))
        self.assertEqual(reponse.headers.get("Location"), ADRESSE)

    def test_la_chaine_de_requete_ne_remplace_pas_l_adresse(self):
        """🔴 Avant : ``?url=`` remplaçait l'adresse gravée."""
        self.authenticate("admin", "admin")
        reponse = self.url_open(
            "/nfc/%s?url=%s" % (self.tag_adresse.code, PIEGE), allow_redirects=False)
        self.assertEqual(reponse.headers.get("Location"), ADRESSE)

    def test_le_corps_de_l_application_ne_remplace_pas_l_adresse(self):
        """🔴 Avant : ``{"params": {"url": …}}`` remplaçait l'adresse gravée."""
        personne = new_test_user(self.env, login="parametres-app", groups="base.group_user")
        verificateur = "verificateur-parametres-assez-long-pour-etre-serieux"
        defi = base64.urlsafe_b64encode(
            hashlib.sha256(verificateur.encode()).digest()).decode().rstrip("=")
        Device = self.env["bf.nfc.device"]
        code = Device._issue_pending(personne.id, name="Pixel", challenge=defi)
        _appareil, jeton = Device._exchange(code, verificateur)
        reponse = self.url_open(
            API + "/tap",
            data=json.dumps({"code": self.tag_adresse.code, "params": {"url": PIEGE}}),
            headers={"Content-Type": "application/json", "Authorization": "Bearer %s" % jeton})
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.json()["url"], ADRESSE)

    def test_une_adresse_locale_reste_locale(self):
        """Ouvrir une fiche rend un chemin : il ne doit pas gagner d'hôte en route."""
        partenaire = self.env["res.partner"].create({"name": "Fiche locale"})
        tag = self.env["bf.nfc.tag"].create({
            "name": "Fiche", "gesture_id": self.env.ref("bf_nfc.gesture_open").id,
            "res_model": "res.partner", "res_id": partenaire.id,
        })
        self.authenticate("admin", "admin")
        reponse = self.url_open("/nfc/%s" % tag.code, allow_redirects=False)
        self.assertIn("/mail/view?model=res.partner&res_id=%s" % partenaire.id,
                      reponse.headers.get("Location", ""))

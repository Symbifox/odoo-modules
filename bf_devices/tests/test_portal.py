# -*- coding: utf-8 -*-
"""La page elle-même : elle s'affiche, elle liste, elle retire.

⚠️ Les tests de modèle ne voient pas une page. Un gabarit cassé, une entrée de
portail mal accrochée ou un jeton anti-rejeu absent du formulaire rendent une
page morte sans qu'aucune assertion sur l'ORM ne bronche.
"""
import hashlib
import re
import secrets

from odoo.tests import HttpCase, new_test_user, tagged


@tagged("bf_devices", "post_install", "-at_install")
class TestPortailAppareils(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Registre = cls.env["bf.device.registry"]
        cls.sources = cls.Registre._sources_presentes()
        cls.personne = new_test_user(cls.env, login="appareils_portail")

    def setUp(self):
        super().setUp()
        if not self.sources:
            self.skipTest("aucun module d'appariement sur ce banc")

    #: Ce qu'un registre exige en plus, et que ce fichier ne peut pas deviner.
    EXIGENCES = {
        ("bf.desktop.device", "app"): "stickynotes",
    }

    def _poser(self, nom):
        """Un appareil de la première source présente.

        🔴 Le champ du jeton n'a pas le même nom partout : les registres de
        téléphones portent `device_token`, ceux à empreinte ne portent que
        `token_hash`. Le supposer faisait tomber tous les essais de ce fichier
        dès que la première source présente était un registre à empreinte —
        c'est-à-dire chez qui installe ce module sans module de téléphone.
        On demande donc au modèle ce qu'il sait porter.
        """
        source = self.sources[0]
        Modele = self.env[source["model"]].sudo()
        valeurs = {"user_id": self.personne.id, "name": nom}
        jeton = secrets.token_urlsafe(32)
        if "device_token" in Modele._fields:
            valeurs["device_token"] = jeton
        if "token_hash" in Modele._fields:
            valeurs["token_hash"] = hashlib.sha256(jeton.encode()).hexdigest()
        for champ_nom, champ in Modele._fields.items():
            if (champ.required and champ_nom not in valeurs
                    and champ.default is None
                    and champ.type in ("char", "text", "selection")):
                cle = (Modele._name, champ_nom)
                self.assertIn(
                    cle, self.EXIGENCES,
                    "le registre %s exige « %s » : ajouter sa valeur d'essai à "
                    "EXIGENCES" % (Modele._name, champ_nom))
                valeurs[champ_nom] = self.EXIGENCES[cle]
        return Modele.create(valeurs)

    def test_la_page_s_affiche_et_nomme_mes_appareils(self):
        self._poser("Pixel essai")
        self.authenticate("appareils_portail", "appareils_portail")
        reponse = self.url_open("/my/appareils", timeout=30)
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("Pixel essai", reponse.text)

    def test_la_page_le_dit_quand_il_n_y_a_rien(self):
        self.authenticate("appareils_portail", "appareils_portail")
        reponse = self.url_open("/my/appareils", timeout=30)
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("Aucun appareil", reponse.text)

    def test_le_bouton_retire_vraiment_l_acces(self):
        appareil = self._poser("Téléphone à retirer")
        self.authenticate("appareils_portail", "appareils_portail")
        page = self.url_open("/my/appareils", timeout=30)
        jeton = re.search(r'name="csrf_token"\s+value="([^"]+)"', page.text)
        self.assertTrue(jeton, "le formulaire ne porte pas de jeton anti-rejeu")

        reponse = self.url_open("/my/appareils/retirer", timeout=30, data={
            "csrf_token": jeton.group(1),
            "cle": "%s,%s" % (appareil._name, appareil.id),
        })

        self.assertEqual(reponse.status_code, 200)
        self.assertFalse(appareil.sudo().active)

    def test_sans_jeton_anti_rejeu_rien_ne_bouge(self):
        """🔴 Sans cette garde, une image dans un courriel couperait les
        téléphones de la personne au simple affichage du message."""
        appareil = self._poser("Téléphone protégé")
        self.authenticate("appareils_portail", "appareils_portail")

        reponse = self.url_open("/my/appareils/retirer", timeout=30, data={
            "cle": "%s,%s" % (appareil._name, appareil.id),
        })

        self.assertEqual(reponse.status_code, 400)
        self.assertTrue(appareil.sudo().active)

"""L'appairage bout en bout, et ce qu'un jeton de bureau ouvre vraiment.

Les essais du modèle (`test_pairing`) prouvent le flux. Ceux-ci prouvent les
deux choses qu'un modèle ne peut pas prouver :

  1. le flux marche par HTTP, avec la page de consentement et sa session ;
  2. le jeton qui en sort ouvre le bloc-notes — c'est-à-dire que la
     reconnaissance croisée de `bf_bloc_notes._DEVICE_MODELS` fonctionne,
     alors qu'AUCUN des deux modules ne dépend de l'autre.

⚠️ Le deuxième groupe se saute quand `bf_bloc_notes` n'est pas installé : le
socle doit se poser seul chez un locataire qui n'a pas le bloc-notes.
"""
import json
import re
import unittest

from odoo.tests.common import HttpCase, new_test_user, tagged

BASE = "/bf_desktop_pairing/v1"
NOTES = "/bf_bloc_notes/mobile/v1"


@tagged("post_install", "-at_install", "bf_desktop_pairing")
class TestAppairageHttp(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(
            cls.env, login="alice_appairage_http", password="alice_appairage_http",
            groups="base.group_user", email="aliceappairagehttp@example.com")

    def _post(self, route, payload, token=None, content_type="application/json"):
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
        if token:
            headers["Authorization"] = "Bearer " + token
        return self.url_open(route, data=json.dumps(payload), headers=headers)

    def _appairer(self):
        """Le flux complet, comme le widget le joue. Rend le jeton porteur."""
        demarre = self._post(f"{BASE}/auth/device/start",
                             {"app": "stickynotes", "name": "Poste d'essai"})
        self.assertEqual(demarre.status_code, 201)
        ouvert = demarre.json()

        # La personne approuve dans son navigateur : il faut une session.
        self.authenticate("alice_appairage_http", "alice_appairage_http")
        page = self.url_open(ouvert["verification_uri_complete"])
        self.assertEqual(page.status_code, 200)
        # La page offre trois nombres : celui du widget, et deux leurres.
        self.assertIn(ouvert["match_number"], page.text)

        # Le jeton CSRF se lit SUR la page, comme un navigateur le ferait :
        # le calculer à côté éprouverait notre arithmétique, pas la page.
        jeton_csrf = re.search(
            r'name="csrf_token" value="([^"]+)"', page.text)
        self.assertTrue(jeton_csrf, "la page de consentement porte un CSRF")

        accorde = self.url_open(
            f"{BASE}/auth/device/consent",
            data={"code": ouvert["device_code"],
                  # On clique le nombre que le widget affiche.
                  "decision": str(int(ouvert["match_number"])),
                  "csrf_token": jeton_csrf.group(1)})
        self.assertEqual(accorde.status_code, 200)

        livre = self._post(f"{BASE}/auth/device/poll",
                           {"device_code": ouvert["device_code"],
                            "device_secret": ouvert["device_secret"]})
        self.assertEqual(livre.status_code, 200)
        return livre.json()["token"]

    # ── Le flux ───────────────────────────────────────────────────────────

    def test_ping_annonce_le_flux(self):
        reponse = self.url_open(f"{BASE}/ping")
        self.assertEqual(reponse.status_code, 200)
        corps = reponse.json()
        self.assertEqual(corps["flow"], "device_code")
        self.assertIn("stickynotes", corps["apps"])

    def test_le_ping_dit_qui_repond_et_sous_quelle_adresse(self):
        """🔴 Pourquoi l'adresse canonique compte : c'est elle que la page de
        consentement ouvrira. Un locataire joignable par un domaine convivial
        distinct de son canonique faisait casser l'appairage au milieu, et le
        widget n'avait aucun moyen de le voir venir. Le NOM, lui, s'affiche
        avant qu'on approuve : on voit à qui on s'apparie."""
        corps = self.url_open(f"{BASE}/ping").json()
        attendu = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        self.assertEqual(corps["canonical_url"], (attendu or "").rstrip("/"))
        self.assertFalse(corps["canonical_url"].endswith("/"),
                         "le widget compare à une adresse normalisée, sans barre")
        self.assertEqual(corps["tenant"], self.env.company.name)
        self.assertIn("brand", corps)
        self.assertEqual(set(corps["brand"]), {"accent", "dark"})

    def test_le_ping_ne_laisse_sortir_que_des_couleurs_bien_formees(self):
        """⚠️ Ces champs sont des `Char` libres, remplis à la main locataire par
        locataire. Ce qui sort d'ici est peint sur le poste de quelqu'un.

        🔴 Première version de cet essai : elle salissait le PREMIER champ de
        marque et attendait que le ping ne rende rien. Il rendait `#1f84af` —
        et il avait raison : la lecture se replie sur les champs suivants, et
        `primary_color` portait cette valeur. L'essai décrivait une mécanique
        qui n'existe pas. Ce qu'il faut exiger n'est pas « rien ne sort » mais
        « rien de MAL FORMÉ ne sort », quel que soit le champ qui a répondu.
        """
        company = self.env.company.sudo()
        champs = [n for n in ("report_brand_primary", "report_brand_color",
                              "bf_brand_color", "primary_color")
                  if n in company._fields]
        if not champs:
            self.skipTest("aucun champ de marque sur cette base")
        anciens = {n: company[n] for n in champs}
        try:
            for nom in champs:
                company[nom] = "javascript:alert(1)"
            self.assertIsNone(self.url_open(f"{BASE}/ping").json()["brand"]["accent"],
                              "aucune valeur mal formée ne doit sortir")
            company[champs[0]] = "#29ABE2"
            self.assertEqual(
                self.url_open(f"{BASE}/ping").json()["brand"]["accent"], "#29ABE2")
        finally:
            for nom, valeur in anciens.items():
                company[nom] = valeur

    def test_la_couleur_rendue_est_toujours_lisible_par_un_widget(self):
        """Quoi que porte la base, ce qui sort est un hexadécimal ou rien.
        C'est la forme sur laquelle le widget compte pour peindre."""
        corps = self.url_open(f"{BASE}/ping").json()
        for cle in ("accent", "dark"):
            valeur = corps["brand"][cle]
            if valeur is not None:
                self.assertRegex(valeur, r"^#[0-9A-Fa-f]{6}$", cle)

    def test_le_ping_reste_muet_sur_le_reste_du_locataire(self):
        """La route est `auth="public"` : elle ne doit rien dire de plus que ce
        qui est déjà sur le site web de la société."""
        corps = self.url_open(f"{BASE}/ping").json()
        interdits = {"email", "phone", "vat", "street", "users", "databases",
                     "company_id", "partner_id"}
        self.assertFalse(interdits & set(corps),
                         "une route publique n'est pas une fenêtre sur l'annuaire")

    def test_la_page_d_approbation_exige_une_session(self):
        """🔴 C'est `auth="user"` qui porte toute l'authentification du flux :
        mot de passe, SSO Authentik et TOTP passent par là."""
        demarre = self._post(f"{BASE}/auth/device/start",
                             {"app": "stickynotes"})
        url = demarre.json()["verification_uri_complete"]
        reponse = self.url_open(url, allow_redirects=False)
        self.assertIn(reponse.status_code, (302, 303))
        self.assertIn("/web/login", reponse.headers.get("Location", ""))

    def test_la_demande_ne_rend_jamais_le_jeton_dans_une_url(self):
        demarre = self._post(f"{BASE}/auth/device/start",
                             {"app": "stickynotes"})
        corps = demarre.json()
        self.assertNotIn("token", corps,
                         "le jeton ne sort qu'au poll, dans un corps")
        self.assertNotIn(corps["device_secret"],
                         corps["verification_uri_complete"],
                         "le secret du widget ne voyage pas dans l'adresse")

    def test_le_poll_attend_avant_l_approbation(self):
        demarre = self._post(f"{BASE}/auth/device/start",
                             {"app": "stickynotes"})
        corps = demarre.json()
        attente = self._post(f"{BASE}/auth/device/poll",
                             {"device_code": corps["device_code"],
                              "device_secret": corps["device_secret"]})
        self.assertEqual(attente.status_code, 202)
        self.assertEqual(attente.json()["error"], "authorization_pending")

    def test_le_poll_trop_rapide_est_freine(self):
        demarre = self._post(f"{BASE}/auth/device/start",
                             {"app": "stickynotes"})
        corps = demarre.json()
        self._post(f"{BASE}/auth/device/poll",
                   {"device_code": corps["device_code"],
                    "device_secret": corps["device_secret"]})
        tot = self._post(f"{BASE}/auth/device/poll",
                         {"device_code": corps["device_code"],
                          "device_secret": corps["device_secret"]})
        self.assertEqual(tot.status_code, 429)
        self.assertEqual(tot.json()["error"], "slow_down")

    def test_surface_inconnue_refusee(self):
        reponse = self._post(f"{BASE}/auth/device/start", {"app": "tout-ouvrir"})
        self.assertEqual(reponse.status_code, 400)
        self.assertEqual(reponse.json()["error"], "unknown_app")

    def test_code_inconnu_refuse(self):
        reponse = self._post(f"{BASE}/auth/device/poll",
                             {"device_code": "invente", "device_secret": "x"})
        self.assertEqual(reponse.status_code, 400)
        self.assertEqual(reponse.json()["error"], "expired_token")

    def test_un_corps_sans_type_json_est_refuse(self):
        """🔴 Ce qui fermait la porte à la page web piégée.

        Un POST en `text/plain` est une « requête simple » au sens CORS :
        n'importe quelle page pouvait faire ouvrir une demande d'appairage
        depuis l'adresse de chacun de ses visiteurs, sans préflight, et le
        frein par appelant ne protégeait plus rien.
        """
        for type_contenu in ("text/plain", "application/x-www-form-urlencoded",
                             None):
            reponse = self._post(f"{BASE}/auth/device/start",
                                 {"app": "stickynotes"},
                                 content_type=type_contenu)
            self.assertEqual(reponse.status_code, 400,
                             "refusé pour %s" % type_contenu)

    def test_un_mauvais_nombre_detruit_la_demande(self):
        """Laisser réessayer rendrait les trois nombres inutiles."""
        demarre = self._post(f"{BASE}/auth/device/start",
                             {"app": "stickynotes"})
        ouvert = demarre.json()
        self.authenticate("alice_appairage_http", "alice_appairage_http")
        page = self.url_open(ouvert["verification_uri_complete"])
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)

        mauvais = (int(ouvert["match_number"]) + 1) % 100
        refus = self.url_open(f"{BASE}/auth/device/consent",
                              data={"code": ouvert["device_code"],
                                    "decision": str(mauvais),
                                    "csrf_token": csrf})
        self.assertEqual(refus.status_code, 200)
        # La demande est morte : le poll ne rend plus rien.
        suite = self._post(f"{BASE}/auth/device/poll",
                           {"device_code": ouvert["device_code"],
                            "device_secret": ouvert["device_secret"]})
        self.assertEqual(suite.status_code, 400)
        self.assertEqual(suite.json()["error"], "expired_token")

    def test_le_nom_du_poste_vient_du_demandeur_et_est_presente_comme_tel(self):
        """Le libellé est choisi par qui demande : la page ne doit pas le
        présenter comme un fait, et il ne doit jamais s'échapper en HTML."""
        demarre = self._post(f"{BASE}/auth/device/start",
                             {"app": "stickynotes",
                              "name": "<b>poste-pirate</b>"})
        ouvert = demarre.json()
        self.authenticate("alice_appairage_http", "alice_appairage_http")
        page = self.url_open(ouvert["verification_uri_complete"])
        self.assertNotIn("<b>poste-pirate</b>", page.text,
                         "le nom fourni doit être échappé")
        self.assertIn("&lt;b&gt;poste-pirate&lt;/b&gt;", page.text)

    def test_un_compte_de_portail_ne_peut_pas_apparier(self):
        portail = new_test_user(
            self.env, login="portail_appairage", password="portail_appairage",
            groups="base.group_portal", email="portailappairage@example.com")
        demarre = self._post(f"{BASE}/auth/device/start",
                             {"app": "stickynotes"})
        ouvert = demarre.json()
        self.authenticate("portail_appairage", "portail_appairage")
        page = self.url_open(ouvert["verification_uri_complete"])
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
        if csrf:
            reponse = self.url_open(
                f"{BASE}/auth/device/consent",
                data={"code": ouvert["device_code"],
                      "decision": str(int(ouvert["match_number"])),
                      "csrf_token": csrf.group(1)})
            self.assertEqual(reponse.status_code, 200)
        # Dans tous les cas : aucun appareil livré à ce compte.
        self.assertFalse(
            self.env["bf.desktop.device"].sudo().search(
                [("user_id", "=", portail.id), ("delivered", "=", True)]),
            "un compte de portail n'a pas de bureau")

    def test_consentement_sans_csrf_refuse(self):
        """La page porte un jeton CSRF : un POST monté ailleurs ne passe pas."""
        demarre = self._post(f"{BASE}/auth/device/start",
                             {"app": "stickynotes"})
        code = demarre.json()["device_code"]
        self.authenticate("alice_appairage_http", "alice_appairage_http")
        reponse = self.url_open(f"{BASE}/auth/device/consent",
                                data={"code": code, "decision": "allow"})
        self.assertEqual(reponse.status_code, 400)

    # ── Ce que le jeton ouvre ─────────────────────────────────────────────

    def test_le_jeton_de_bureau_ouvre_le_bloc_notes(self):
        if "bf.note" not in self.env:
            raise unittest.SkipTest("bf_bloc_notes n'est pas installé")
        jeton = self._appairer()
        reponse = self.url_open(
            f"{NOTES}/notes", headers={"Authorization": "Bearer " + jeton})
        self.assertEqual(reponse.status_code, 200)
        self.assertTrue(reponse.json()["ok"])

    def test_sans_jeton_le_bloc_notes_refuse(self):
        if "bf.note" not in self.env:
            raise unittest.SkipTest("bf_bloc_notes n'est pas installé")
        reponse = self.url_open(f"{NOTES}/notes")
        self.assertEqual(reponse.status_code, 401)

    def test_les_routes_de_synchronisation_repondent(self):
        if "bf.note" not in self.env:
            raise unittest.SkipTest("bf_bloc_notes n'est pas installé")
        jeton = self._appairer()
        entetes = {"Authorization": "Bearer " + jeton}
        for route in (f"{NOTES}/notes/stamp", f"{NOTES}/notes/ids",
                      f"{NOTES}/notes/stamp?shared=1", f"{NOTES}/notes/ids?shared=1"):
            reponse = self.url_open(route, headers=entetes)
            self.assertEqual(reponse.status_code, 200, route)
            self.assertTrue(reponse.json()["ok"], route)

    def test_le_ping_du_bloc_notes_annonce_les_capacites_du_bureau(self):
        if "bf.note" not in self.env:
            raise unittest.SkipTest("bf_bloc_notes n'est pas installé")
        corps = self.url_open(f"{NOTES}/ping").json()
        for capacite in ("shared_notes", "stamp", "visible_ids"):
            self.assertIn(capacite, corps["features"])

    def test_un_jeton_revoque_ferme_le_bloc_notes(self):
        """Le geste de « Mes appareils » doit couper la porte du bureau."""
        if "bf.note" not in self.env:
            raise unittest.SkipTest("bf_bloc_notes n'est pas installé")
        # ⚠️ Et le registre commun des appareils, qui porte le geste de
        # révocation, vit dans `bf_devices` — dont ce module ne dépend PAS
        # (c'est voulu : un socle d'appairage ne tire pas la page qui le
        # montre). Sans lui, cet essai n'a rien à mesurer.
        if "bf.device.registry" not in self.env:
            raise unittest.SkipTest("bf_devices n'est pas installé")
        jeton = self._appairer()
        entetes = {"Authorization": "Bearer " + jeton}
        self.assertEqual(self.url_open(f"{NOTES}/notes", headers=entetes)
                         .status_code, 200)
        appareil = self.env["bf.desktop.device"].sudo().search(
            [("user_id", "=", self.alice.id)], limit=1)
        self.env["bf.device.registry"]._revoquer(
            "bf.desktop.device,%s" % appareil.id, usager=self.alice)
        self.assertEqual(self.url_open(f"{NOTES}/notes", headers=entetes)
                         .status_code, 401)

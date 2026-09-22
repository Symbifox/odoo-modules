"""La page `/capture`, jouée par HTTP.

Ce qui s'éprouve ici n'est pas le rendu (une page se regarde), mais les trois
choses qui la font vivre ou mourir en silence : l'authentification, l'en-tête
qui autorise l'agent de service à revendiquer sa portée, et le contrat JSON du
dépôt. Une page installable dont l'agent ne peut pas prendre `/capture` n'est
pas installable, et rien dans les journaux ne le dit.
"""

import base64
import json

from unittest.mock import patch

from odoo.tests.common import HttpCase, tagged

from ..controllers.page_pwa import _encre_sur


@tagged("post_install", "-at_install", "bf_capture")
class TestCapturePage(HttpCase):

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("bf_capture.folder", "Transcriptions")

    # ── La page ───────────────────────────────────────────────────────

    def test_page_exige_une_session(self):
        reponse = self.url_open("/capture", allow_redirects=False)
        self.assertIn(reponse.status_code, (302, 303))
        self.assertIn("/web/login", reponse.headers.get("Location", ""))

    def test_page_sert_sa_coquille(self):
        self.authenticate("admin", "admin")
        reponse = self.url_open("/capture")
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("/capture/manifest.webmanifest", reponse.text)
        self.assertIn("/bf_capture/static/src/capture/capture.js", reponse.text)
        # 🔴 Odoo sert ses statiques avec `max-age=604800`. Sans la marque de
        # version dans l'adresse, un correctif n'atteint pas une page déjà
        # ouverte pendant une semaine, et l'écran ment en silence.
        version = self.env["ir.module.module"].sudo().search(
            [("name", "=", "bf_capture")], limit=1).installed_version.replace(".", "-")
        self.assertIn("capture.css?v=%s" % version, reponse.text)
        self.assertIn("capture.js?v=%s" % version, reponse.text)
        # Une page authentifiée n'a rien à faire dans un cache partagé.
        self.assertIn("no-store", reponse.headers.get("Cache-Control", ""))

    def test_page_annonce_quand_la_dictee_manque(self):
        self.authenticate("admin", "admin")
        with patch.object(type(self.env["bf.capture"]),
                          "transcription_disponible", lambda self: False):
            reponse = self.url_open("/capture")
        self.assertIn("sans texte", reponse.text)

    # ── Le contraste ──────────────────────────────────────────────────

    @staticmethod
    def _contraste(fond, encre):
        def luminance(couleur):
            canaux = [int(couleur[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]
            lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
                   for c in canaux]
            return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
        a, b = luminance(fond), luminance(encre)
        clair, sombre = max(a, b), min(a, b)
        return (clair + 0.05) / (sombre + 0.05)

    def test_l_encre_se_lit_sur_tous_les_accents_du_parc(self):
        """🔴 Trouvé au banc : l'accent d'origine d'Odoo est un prune FONCÉ.

        Une encre fixe convient au bleu de la marque et rend le bouton
        illisible ailleurs. Le seuil est celui de WCAG AA pour du texte gras.
        """
        accents = {
            "#29ABE2": "bleu Blue Fox",
            "#714B67": "prune d'origine d'Odoo",
            "#000000": "noir",
            "#ffffff": "blanc",
            "#f6c344": "jaune",
            "#2E3132": "anthracite de la marque",
        }
        for accent, nom in accents.items():
            with self.subTest(accent=nom):
                ratio = self._contraste(accent, _encre_sur(accent))
                self.assertGreaterEqual(
                    ratio, 4.5,
                    "%s : %.2f:1 avec l'encre %s" % (nom, ratio, _encre_sur(accent)))

    def test_l_encre_voyage_jusqu_a_la_page(self):
        self.authenticate("admin", "admin")
        reponse = self.url_open("/capture")
        self.assertIn("--brand-ink:", reponse.text)

    def test_une_couleur_illisible_ne_fait_pas_tomber_la_page(self):
        self.assertEqual(_encre_sur("pas une couleur"), "#ffffff")
        self.assertEqual(_encre_sur(""), "#ffffff")
        # Trois chiffres, la forme courte : #abc vaut #aabbcc.
        self.assertEqual(_encre_sur("#fff"), _encre_sur("#ffffff"))

    def test_les_phrases_du_script_viennent_du_serveur(self):
        """🔴 Écrites en dur dans le `.js`, elles restaient françaises en anglais.

        La page se rendait alors à moitié dans chaque langue : l'en-tête
        traduit, l'état de l'enregistrement non.
        """
        self.authenticate("admin", "admin")
        reponse = self.url_open("/capture")
        self.assertIn('id="i18n"', reponse.text)
        bloc = reponse.text.split('id="i18n">', 1)[1].split("</script>", 1)[0]
        mots = json.loads(bloc)
        for cle in ("en_cours", "pret", "plafond_atteint", "micro_refuse",
                    "note_sans_texte", "session_expiree"):
            self.assertIn(cle, mots)
            self.assertTrue(mots[cle].strip(), cle)

    def test_la_page_suit_la_langue_de_la_session(self):
        """La langue vient du CONTEXTE DE SESSION, figé à la connexion.

        ⚠️ Changer la langue d'un usager ne change donc rien tant que sa session
        vit : il faut se reconnecter. C'est le comportement d'Odoo, pas celui du
        module, et c'est ce qui a fait croire à une page non traduite en
        préparant les captures de la vitrine.
        """
        anglais = self.env.ref("base.lang_en_CA", raise_if_not_found=False)
        if not anglais:
            self.skipTest("en_CA absent de cette base")
        anglais.sudo().active = True
        self.env.ref("base.user_admin").sudo().lang = "en_CA"
        self.authenticate("admin", "admin")       # session NEUVE : elle prend la langue
        reponse = self.url_open("/capture")
        self.assertIn('<html lang="en-CA">', reponse.text)

    # ── L'installabilité ──────────────────────────────────────────────

    def test_manifeste_porte_son_identite_et_ses_icones(self):
        reponse = self.url_open("/capture/manifest.webmanifest")
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("application/manifest+json", reponse.headers.get("Content-Type", ""))
        manifeste = json.loads(reponse.text)
        # Sans `id`, le navigateur le dérive de `start_url` : l'adresse bouge un
        # jour et le lanceur installe une deuxième icône à côté de la première.
        self.assertEqual(manifeste["id"], "/capture")
        self.assertEqual(manifeste["scope"], "/capture")
        version = self.env["ir.module.module"].sudo().search(
            [("name", "=", "bf_capture")], limit=1).installed_version.replace(".", "-")
        # 🔴 Sans marque de version, une page installée garde l'ancienne icône
        # une semaine : Odoo sert ses statiques avec `max-age=604800`.
        for icone in manifeste["icons"]:
            self.assertIn("?v=%s" % version, icone["src"], icone["src"])
        tailles = {i["sizes"] for i in manifeste["icons"]}
        usages = {i["purpose"] for i in manifeste["icons"]}
        self.assertEqual(tailles, {"192x192", "512x512"})
        self.assertEqual(usages, {"any", "maskable"})

    def test_le_cache_de_l_agent_porte_la_version(self):
        """🔴 Un nom de cache fixe garde l'ancienne feuille de style pour toujours.

        L'agent sert la coquille depuis son cache : sans version dans le nom,
        un correctif posé au serveur n'atteint jamais une page déjà installée.
        """
        version = self.env["ir.module.module"].sudo().search(
            [("name", "=", "bf_capture")], limit=1).installed_version
        reponse = self.url_open("/capture/sw.js")
        self.assertIn("bf-capture-%s" % version.replace(".", "-"), reponse.text)

    def test_agent_de_service_peut_revendiquer_sa_portee(self):
        """🔴 Sans `Service-Worker-Allowed`, la page n'est pas installable."""
        reponse = self.url_open("/capture/sw.js")
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.headers.get("Service-Worker-Allowed"), "/capture")
        self.assertIn("javascript", reponse.headers.get("Content-Type", ""))

    # ── Le dépôt ──────────────────────────────────────────────────────

    def _memo(self, **params):
        return self.url_open(
            "/capture/memo",
            data=json.dumps({"jsonrpc": "2.0", "method": "call", "params": params}),
            headers={"Content-Type": "application/json"},
        )

    def test_memo_exige_une_session(self):
        reponse = self._memo(audio_b64="", mimetype="audio/webm")
        # Une route JSON refuse en levant, pas en redirigeant.
        self.assertIn("error", reponse.json())

    def test_son_illisible_revient_en_phrase(self):
        self.authenticate("admin", "admin")
        reponse = self._memo(audio_b64="pas du base64 !", mimetype="audio/webm")
        self.assertEqual(reponse.json()["result"]["error"], "Son illisible.")

    def test_base64_abime_est_refuse_plutot_que_rafistole(self):
        """🔴 Sans `validate=True`, Python JETTE les caractères hors alphabet.

        Un transfert abîmé donnerait alors un fichier audio amputé, que Whisper
        transcrirait en charabia plausible. Refuser est la seule réponse utile.
        """
        self.authenticate("admin", "admin")
        # Décodable en silence sans validation (« hello world »), refusé avec.
        reponse = self._memo(audio_b64="aG VsbG8gd29ybGQ=", mimetype="audio/webm")
        self.assertEqual(reponse.json()["result"]["error"], "Son illisible.")

    def test_memo_vide_est_refuse(self):
        self.authenticate("admin", "admin")
        reponse = self._memo(audio_b64="", mimetype="audio/webm")
        self.assertIn("Aucun son", reponse.json()["result"]["error"])

    def test_memo_cree_la_note_et_rend_son_lien(self):
        self.authenticate("admin", "admin")
        son = base64.b64encode(b"x" * 4000).decode()
        with patch.object(type(self.env["bf.capture"]),
                          "transcription_disponible", lambda self: False):
            reponse = self._memo(audio_b64=son, mimetype="audio/webm;codecs=opus",
                                 titre="Depuis la page")
        resultat = reponse.json()["result"]
        self.assertTrue(resultat["ok"])
        note = self.env["bf.note"].browse(resultat["note_id"])
        self.assertTrue(note.exists())
        self.assertEqual(note.name, "Depuis la page")
        # Le schéma d'adresse du client web d'Odoo 18.
        self.assertEqual(resultat["url"], "/odoo/m-bf.note/%s" % note.id)

    def test_le_type_du_navigateur_choisit_l_extension(self):
        """Safari rend du MP4, Chrome du WebM : la pièce jointe doit suivre."""
        self.authenticate("admin", "admin")
        son = base64.b64encode(b"x" * 4000).decode()
        with patch.object(type(self.env["bf.capture"]),
                          "transcription_disponible", lambda self: False):
            webm = self._memo(audio_b64=son, mimetype="audio/webm;codecs=opus").json()
            mp4 = self._memo(audio_b64=son, mimetype="audio/mp4").json()
        for charge, attendu in ((webm, ".webm"), (mp4, ".m4a")):
            note = self.env["bf.note"].browse(charge["result"]["note_id"])
            piece = self.env["ir.attachment"].search(
                [("res_model", "=", "bf.note"), ("res_id", "=", note.id)], limit=1)
            self.assertTrue(piece.name.endswith(attendu), piece.name)

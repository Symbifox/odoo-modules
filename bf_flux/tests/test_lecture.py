# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from odoo.addons.bf_flux.models.flux_source import FluxErreur, analyser
from odoo.addons.bf_flux.models.flux_element import texte_de_page

from .common import FluxCase, fixture


@tagged("post_install", "-at_install")
class TestLecture(FluxCase):

    def test_rss_identifiant_prioritaire(self):
        recs = analyser(fixture("defense.xml"))
        self.assertEqual(len(recs), 5)
        self.assertEqual(recs[0]["cle"], "1001")
        self.assertEqual(recs[0]["emetteur"], "Lockheed Martin")
        self.assertEqual(recs[0]["resume"], "Lockheed Martin today announced a new plant.")
        self.assertEqual(recs[0]["langue"], "en")
        # Code ISIN et symbole boursier : des classifications, pas des sujets.
        self.assertEqual(recs[0]["sujets"], ["Business Contracts"])

    def test_atom(self):
        (rec,) = analyser(fixture("atom.xml"))
        self.assertEqual(rec["cle"], "tag:blogue.example.com,2026:loi-25")
        # Le lien d'enclosure n'écrase pas le lien de l'article.
        self.assertEqual(rec["lien"], "https://blogue.example.com/loi-25")
        self.assertEqual(rec["emetteur"], "Équipe conformité")
        self.assertEqual(rec["sujets"], ["Vie privée"])
        self.assertEqual(str(rec["date"]), "2026-09-22 10:00:00")

    def test_entite_externe_jamais_resolue(self):
        recs = analyser(fixture("entite.xml"))
        self.assertNotIn("root:", "".join(r["titre"] for r in recs))

    def test_pas_un_flux(self):
        with self.assertRaises(FluxErreur) as ctx:
            analyser(b"<html><body>Nope</body></html>")
        self.assertFalse(ctx.exception.passager)

    def test_dedoublonnage_entre_flux(self):
        with self.reseau(self.flux_exemple()):
            self.src_defense._flux_relever()
            self.src_aero._flux_relever()
        Elem = self.env["bf.flux.element"]
        self.assertEqual(Elem.search_count([("cle", "=", "1001")]), 1)
        lockheed = Elem.search([("cle", "=", "1001")])
        self.assertEqual(lockheed.source_ids, self.src_defense | self.src_aero)
        self.assertEqual(self.src_aero.derniers_nouveaux, 0)

    def test_version_francaise_remplace_anglaise(self):
        with self.reseau(self.flux_exemple()):
            self.src_defense._flux_relever()
            elem = self.env["bf.flux.element"].search([("cle", "=", "1002")])
            self.assertEqual(elem.langue, "en")
            touches = self.src_aero._flux_relever()
        self.assertIn(elem, touches)
        self.assertEqual(elem.langue, "fr")
        self.assertIn("/fr/", elem.lien)
        self.assertTrue(elem.titre.startswith("Bombardier Défense signe"))

    def test_francaise_non_remplacee_par_anglaise(self):
        with self.reseau(self.flux_exemple()):
            self.src_aero._flux_relever()
            self.src_defense._flux_relever()
        elem = self.env["bf.flux.element"].search([("cle", "=", "1002")])
        self.assertEqual(elem.langue, "fr")
        self.assertIn("/fr/", elem.lien)
        self.assertEqual(elem.source_ids, self.src_defense | self.src_aero)

    def test_echec_passager_revient_vite(self):
        err = FluxErreur("HTTP 429", passager=True)
        with self.reseau({"https://flux.example.com/defense": err}):
            self.src_defense._flux_relever()
        self.assertEqual(self.src_defense.dernier_etat, "passager")
        self.assertEqual(self.src_defense.echecs_consecutifs, 1)
        ecart = self.src_defense.prochaine_releve - self.src_defense.derniere_releve
        self.assertLessEqual(ecart.total_seconds(), 1800)

    def test_panne_qui_dure_alerte_une_fois(self):
        err = FluxErreur("The read operation timed out", passager=True)
        with self.reseau({"https://flux.example.com/defense": err}):
            for _i in range(8):
                self.src_defense._flux_relever()
        self.assertEqual(self.src_defense.echecs_consecutifs, 8)
        activites = self.src_defense.activity_ids
        self.assertEqual(len(activites), 1)
        self.assertEqual(activites.user_id, self.u_gestion)
        self.assertIn("timed out", activites.note)
        # Une relève réussie remet le compteur à zéro.
        with self.reseau(self.flux_exemple()):
            self.src_defense._flux_relever()
        self.assertEqual(self.src_defense.echecs_consecutifs, 0)

    def test_echec_definitif(self):
        with self.reseau({}):
            self.src_defense._flux_relever()
        self.assertEqual(self.src_defense.dernier_etat, "definitif")
        self.assertEqual(self.src_defense.dernier_message, "HTTP 404")

    def test_relever_reserve_a_la_gestion(self):
        """Refusé AVANT d'aller sur le réseau : sans la garde, l'appel échouait
        aussi, mais seulement après avoir téléchargé l'adresse."""
        from odoo.exceptions import AccessError
        with self.reseau(self.flux_exemple()) as appels:
            with self.assertRaises(AccessError):
                self.src_defense.with_user(self.u_atelier).action_relever()
        self.assertEqual(appels, [])

    def test_lien_hors_http_jamais_admis(self):
        brut = (b'<rss version="2.0"><channel><title>x</title>'
                b'<item><title>Pi\xc3\xa8ge</title><link>javascript:alert(1)</link><guid>j1</guid></item>'
                b'<item><title>Bon</title><link>https://ok.example.com/a</link><guid>j2</guid></item>'
                b'</channel></rss>')
        self.assertEqual([r["cle"] for r in analyser(brut)], ["j2"])

    def test_adresses_internes_refusees(self):
        from odoo.addons.bf_flux.models.flux_source import adresse_publique, telecharger
        for url in ("http://127.0.0.1/x", "http://localhost:8069/web", "http://10.0.0.5/",
                    "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data/",
                    "http://[::1]/", "http://0.0.0.0/", "http://100.64.1.1/",
                    "http://100.100.100.100/"):
            self.assertFalse(adresse_publique(url), url)
            with self.assertRaises(FluxErreur):
                telecharger(url, publique_seulement=True)
        self.assertTrue(adresse_publique("http://93.184.215.14/"))

    def test_redirection_vers_l_interne_refusee(self):
        from unittest.mock import MagicMock, patch
        from odoo.addons.bf_flux.models import flux_source
        rep = MagicMock(is_redirect=True, headers={"Location": "http://127.0.0.1/secret"})
        with patch.object(flux_source, "adresse_publique", lambda u: "127.0.0.1" not in u), \
                patch.object(flux_source.requests, "get", return_value=rep) as get:
            with self.assertRaises(FluxErreur):
                flux_source.telecharger("https://public.example.com/a", publique_seulement=True)
        self.assertEqual(get.call_count, 1, "la cible interne n'est jamais demandée")

    def test_url_obligatoirement_http(self):
        with self.assertRaises(ValidationError):
            self.env["bf.flux.source"].create({"name": "x", "url": "file:///etc/passwd"})

    def test_texte_de_page(self):
        texte = texte_de_page(fixture("page.html"))
        self.assertIn("Premier paragraphe du communiqué.", texte)
        self.assertIn("Deuxième paragraphe.", texte)
        for bruit in ("Menu", "Entête", "À lire aussi", "Pied", "var a"):
            self.assertNotIn(bruit, texte)

    def test_coupures(self):
        self.src_defense.write({"texte_complet": True,
                                "coupures": "Une photo accompagnant\n- 30 -"})
        with self.reseau(self.flux_exemple()):
            self.src_defense._flux_relever()
        elem = self.env["bf.flux.element"].search([("cle", "=", "1001")])
        texte = "Corps.\nSuite.\n- 30 -\nRelations médias\nUne photo accompagnant ce communiqué"
        self.assertEqual(elem._flux_nettoyer_texte(texte), "Corps.\nSuite.")
        self.src_defense.coupures = False
        self.assertEqual(elem._flux_nettoyer_texte(texte), texte)

    def test_langue_du_texte_dit_ce_qui_a_ete_ecrit(self):
        """Leçon d'une veille réelle : un échec pendant la bascule vers le français ne
        doit pas faire croire que le texte est en français."""
        self.src_defense.texte_complet = True
        self.src_aero.texte_complet = True
        page_en = "https://www.example.com/news-release/2026/09/21/1002/en/bombardier.html"
        page_fr = "https://www.example.com/news-release/2026/09/21/1002/fr/bombardier.html"
        reponses = dict(self.flux_exemple())
        reponses[page_en] = (fixture("page.html"), "text/html")
        Elem = self.env["bf.flux.element"]
        with self.reseau(reponses):
            self.src_defense._flux_relever()
            elem = Elem.search([("cle", "=", "1002")])
            elem._flux_recuperer_texte()
            self.assertEqual((elem.texte_etat, elem.texte_langue), ("ok", "en"))
            self.src_aero._flux_relever()
            self.assertEqual(elem.texte_etat, "a_faire")
            reponses[page_fr] = FluxErreur("HTTP 429", passager=True)
            elem._flux_recuperer_texte()
        self.assertEqual(elem.langue, "fr")
        self.assertEqual(elem.texte_etat, "passager")
        self.assertEqual(elem.texte_langue, "en")
        self.assertTrue(elem.texte)

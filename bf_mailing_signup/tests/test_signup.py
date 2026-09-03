# -*- coding: utf-8 -*-
"""Ce que ces essais protègent, dans l'ordre où ça ferait mal.

1. Une inscription ne doit JAMAIS naître active. C'est la seule chose qui
   empêche un tiers d'abonner l'adresse de quelqu'un d'autre, la route étant
   forcément ouverte sans jeton CSRF.
2. Une adresse désinscrite ne doit pas se réinscrire par une resoumission du
   formulaire — seul son lien de confirmation le peut.
3. Le formulaire ne doit rien dire. Une réponse différente selon que l'adresse
   est connue ou non transforme le point d'entrée en oracle.
"""

from datetime import date
from unittest.mock import patch

from odoo.addons.base.models.ir_mail_server import IrMailServer
from odoo.tests import tagged
from odoo.tests.common import HttpCase

from ..controllers import main as ctl


@tagged("post_install", "-at_install")
class TestMailingSignup(HttpCase):

    def setUp(self):
        super().setUp()
        # Le seau de limitation est un état de MODULE : sans ce ménage, le
        # quatrième essai de la classe échoue pour une raison qui n'a rien à
        # voir avec ce qu'il vérifie.
        ctl._bucket_data.clear()
        # 🔴 Le SMTP est coupé ICI, pas dans un seul essai. Sans ça les essais
        # ENVOIENT pour de vrai : le contrôleur appelle `mail.send()`, et un
        # `env.cr.rollback()` ne rappelle pas un courriel parti. Mesuré sur le
        # banc, dix confirmations expédiées vers example.com au premier passage.
        # Le mouchard sert aussi d'assertion : il voit le message que le SMTP
        # aurait reçu, là où `mail.mail` a déjà disparu (`auto_delete=True`).
        self.envois = []
        correctif = patch.object(
            IrMailServer, "send_email",
            lambda serveur, message, *a, **kw: self.envois.append(message) or "essai")
        correctif.start()
        self.addCleanup(correctif.stop)
        self.liste = self.env["mailing.list"].create({"name": "Essai — nouveautés"})
        self.env["ir.config_parameter"].sudo().set_param(
            ctl.ICP_LIST, str(self.liste.id))
        self.adresse = "essai.inscription@example.com"

    # ------------------------------------------------------------------ outils

    def _poster(self, **champs):
        donnees = {"courriel": self.adresse, "lang": "fr"}
        donnees.update(champs)
        return self.url_open("/infolettre", data=donnees, allow_redirects=False)

    def _inscription(self):
        contact = self.env["mailing.contact"].search(
            [("email_normalized", "=", self.adresse)], limit=1)
        if not contact:
            return None
        return self.env["mailing.subscription"].search(
            [("contact_id", "=", contact.id), ("list_id", "=", self.liste.id)], limit=1)

    def _jeton(self):
        return ctl._token(self.env, self.liste.id, self.adresse,
                          date.today().toordinal())

    # ------------------------------------------------------------------ essais

    def test_inscription_naît_desactivee(self):
        r = self._poster()
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["Location"], "/infolettre-merci.html")
        sub = self._inscription()
        self.assertTrue(sub, "l'inscription devrait exister")
        self.assertTrue(sub.opt_out, "elle ne doit PAS naître active")

    def test_confirmation_active(self):
        self._poster()
        r = self.url_open(
            f"/infolettre/confirmer?e={self.adresse}&j={self._jeton()}&lang=fr",
            allow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["Location"], "/infolettre-confirme.html")
        self.assertFalse(self._inscription().opt_out)

    def test_jeton_faux_n_active_rien(self):
        self._poster()
        r = self.url_open(
            f"/infolettre/confirmer?e={self.adresse}&j={'0' * 32}&lang=fr",
            allow_redirects=False)
        self.assertEqual(r.headers["Location"], "/#infolettre")
        self.assertTrue(self._inscription().opt_out)

    def test_jeton_perime_n_active_rien(self):
        self._poster()
        vieux = ctl._token(self.env, self.liste.id, self.adresse,
                           date.today().toordinal() - ctl.CONFIRM_DAYS - 1)
        self.url_open(f"/infolettre/confirmer?e={self.adresse}&j={vieux}&lang=fr",
                      allow_redirects=False)
        self.assertTrue(self._inscription().opt_out)

    def test_resoumission_ne_reactive_pas_une_desinscription(self):
        """Le cas qui compte : quelqu'un s'est désinscrit, un tiers resoumet."""
        self._poster()
        sub = self._inscription()
        sub.write({"opt_out": False})
        sub.write({"opt_out": True})          # la personne se désinscrit
        self._poster()                        # un tiers resoumet le formulaire
        self.assertTrue(self._inscription().opt_out,
                        "une resoumission ne doit pas réactiver une désinscription")

    def test_pot_de_miel_n_inscrit_personne(self):
        r = self._poster(site_web="https://robot.example")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["Location"], "/infolettre-merci.html",
                         "le robot doit voir la même page que tout le monde")
        self.assertFalse(self._inscription())

    def test_adresse_invalide_n_inscrit_personne(self):
        for mauvaise in ("", "sans-arobase", "a@b", "a b@example.com",
                         "x" * 250 + "@example.com"):
            self.adresse = mauvaise
            self._poster()
            self.assertFalse(self._inscription(), f"« {mauvaise} » ne devrait rien créer")

    def test_la_reponse_ne_dit_rien(self):
        """Connue, inconnue, refusée : même code, même destination."""
        vues = set()
        for champs in ({}, {}, {"site_web": "x"}, {"courriel": "nawak"}):
            r = self._poster(**champs)
            vues.add((r.status_code, r.headers.get("Location")))
        self.assertEqual(len(vues), 1, f"le point d'entrée est un oracle : {vues}")

    def test_seau_de_limitation(self):
        for _ in range(ctl._SIGNUP_MAX):
            self._poster()
        avant = self.env["mailing.contact"].search_count([])
        self.adresse = "apres.plafond@example.com"
        r = self._poster()
        self.assertEqual(r.status_code, 303, "même au plafond, la page reste normale")
        self.assertEqual(self.env["mailing.contact"].search_count([]), avant,
                         "au-delà du plafond, plus rien ne s'écrit")

    def test_un_seul_courriel_et_il_porte_le_lien(self):
        """⚠️ On regarde ce que le SMTP a reçu, pas `mail.mail`.

        Le message porte `auto_delete=True` : dès qu'il part, sa ligne et son
        `mail.message` disparaissent. Un essai qui compte les lignes trouve zéro
        et conclut « rien n'est parti », alors que tout est parti.
        """
        self._poster()
        self.assertEqual(len(self.envois), 1,
                         "exactement un courriel, celui de confirmation")
        corps = self.envois[0].as_string()
        self.assertIn("/infolettre/confirmer", corps)
        self.assertIn(self._jeton(), corps)

    def test_rien_ne_part_sans_adresse_valable(self):
        self._poster(site_web="robot")
        self.adresse = "pas-une-adresse"
        self._poster()
        self.assertEqual(self.envois, [], "aucun courriel ne doit partir")

    def test_la_confirmation_n_envoie_rien(self):
        """Le lien confirme, il ne déclenche pas un deuxième courriel."""
        self._poster()
        self.envois.clear()
        self.url_open(f"/infolettre/confirmer?e={self.adresse}&j={self._jeton()}&lang=fr",
                      allow_redirects=False)
        self.assertEqual(self.envois, [])

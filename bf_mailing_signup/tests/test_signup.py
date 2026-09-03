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

import re
from datetime import date
from email.header import decode_header, make_header
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
        # ⚠️ Le contrôleur ne pose PAS `email_from`, exprès : la confirmation
        # part de l'adresse d'envoi de l'instance, la seule alignée en SPF et
        # DKIM. Encore faut-il que l'instance en ait une. Depuis la 17, elle
        # vient d'un `mail.alias.domain` rattaché à la société, et le module
        # `mail` n'en livre AUCUN par défaut : sur une base neuve — la CI en
        # fabrique une par lot — `mail_mail._send` s'arrêtait donc AVANT le
        # mouchard de `send_email`, sur « You must either provide a sender
        # address explicitly ». Le mouchard ne voyait rien passer et six essais
        # tombaient sur un `IndexError` muet sur la cause. Sur une base de
        # travail le domaine existait et masquait tout.
        domaine = self.env["mail.alias.domain"].search([], limit=1)
        if not domaine:
            domaine = self.env["mail.alias.domain"].create({
                "name": "example.com",
                "bounce_alias": "bounce",
                "catchall_alias": "catchall",
                "default_from": "notifications",
            })
        self.env.company.alias_domain_id = domaine
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

    def _corps_html(self, message):
        """Le HTML tel que le destinataire le recevra, décodé.

        ⚠️ Ne PAS lire `as_string()`. Dès que le corps dépasse la longueur de
        ligne d'un courriel, il part en quoted-printable et le codage insère
        des `=\\n` au beau milieu de ce qu'on cherche : un jeton coupé en deux
        fait échouer l'essai pour une raison qui n'a rien à voir avec ce qu'il
        vérifie. Tant que le corps tenait en trois `<p>` nus, le piège dormait.
        """
        partie = message.get_body(preferencelist=("html",))
        self.assertIsNotNone(partie, "le courriel doit porter une partie HTML")
        return partie.get_content()

    def _sujet(self, message):
        return str(make_header(decode_header(message["Subject"])))

    def _habiller(self, marque="Symbifox", sigle="https://exemple.test/sigle.png",
                  logo="https://exemple.test/logo.png"):
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param(ctl.ICP_BRAND, marque)
        icp.set_param(ctl.ICP_MARK, sigle)
        icp.set_param(ctl.ICP_LOGO, logo)

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
        corps = self._corps_html(self.envois[0])
        self.assertIn("/infolettre/confirmer", corps)
        self.assertIn(self._jeton(), corps)
        self.assertEqual(corps.count(self._jeton()), 3,
                         "le lien vit à trois endroits : la cible du bouton, "
                         "celle du repli, et le repli écrit en clair pour qui "
                         "ne voit ni bouton ni lien")

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

    # -------------------------------------------------------------- habillage

    def test_le_courriel_est_brande(self):
        """La confirmation est le PREMIER courriel reçu : il porte la marque."""
        self._habiller()
        self._poster()
        corps = self._corps_html(self.envois[0])
        self.assertIn("Symbifox", corps)
        self.assertIn("https://exemple.test/sigle.png", corps)
        self.assertIn("https://exemple.test/logo.png", corps)
        self.assertIn(ctl.MARINE, corps, "l'en-tête marine du bandeau")
        self.assertIn(ctl.BLEU, corps, "le bleu du bouton")
        self.assertIn("Lexend", corps)
        self.assertNotIn("<style", corps,
                         "les clients courriel jettent les feuilles de style")

    def test_le_cadre_exterieur_reste_clair(self):
        """🔴 Le piège qui a déjà rendu un courriel Blue Fox illisible.

        Plusieurs clients jettent le `background-color` de la carte intérieure.
        Avec un cadre sombre, le texte foncé se retrouve sur le fond foncé du
        cadre et plus rien ne se lit. Le cadre doit donc être clair.
        """
        self._habiller()
        self._poster()
        corps = self._corps_html(self.envois[0])
        self.assertIn(f'<body style="margin:0;padding:0;background-color:{ctl.GLACE}"',
                      corps)
        self.assertIn(f'bgcolor="{ctl.GLACE}"', corps)
        # Les seuls aplats sombres sont posés en attribut `bgcolor`, que
        # personne ne retire — jamais en `background-color` seul.
        self.assertIn(f'bgcolor="{ctl.MARINE}"', corps)
        self.assertNotIn(f"background-color:{ctl.MARINE}", corps)

    def test_habillage_sans_images_ne_casse_rien(self):
        """Un locataire qui ne pose aucune URL reçoit un courriel sobre.

        C'est le comportement voulu, et c'est aussi ce qui permet de publier le
        module : sans valeur par défaut, personne n'hérite de NOS images, donc
        personne ne fait relever les ouvertures de SES abonnés par NOTRE
        serveur.
        """
        icp = self.env["ir.config_parameter"].sudo()
        for cle in (ctl.ICP_BRAND, ctl.ICP_MARK, ctl.ICP_LOGO):
            icp.set_param(cle, "")
        self._poster()
        corps = self._corps_html(self.envois[0])
        # ⚠️ Il reste UNE image : le pixel d'ouverture qu'Odoo colle lui-même à
        # la fin de tout `mail.mail`. Il n'est pas de notre habillage, et un
        # `assertNotIn("<img")` sec échouerait sur lui.
        images = re.findall(r"<img[^>]*>", corps)
        self.assertTrue(all("mail/tracking/open" in i for i in images),
                        f"aucune image de l'habillage sans URL réglée : {images}")
        self.assertIn(self.env.company.name, corps, "repli sur le nom de la société")
        self.assertIn(self._jeton(), corps, "et le lien est toujours là")

    def test_la_marque_va_dans_le_sujet(self):
        self._habiller(marque="Marque d'essai")
        self._poster()
        self.assertEqual(self._sujet(self.envois[0]),
                         "Confirmez votre inscription à Marque d'essai")

    def test_version_anglaise(self):
        self._habiller()
        self._poster(lang="en")
        message = self.envois[0]
        self.assertEqual(self._sujet(message), "Confirm your Symbifox subscription")
        corps = self._corps_html(message)
        self.assertIn("Confirm my subscription", corps)
        self.assertIn('<html lang="en"', corps)
        self.assertIn("lang=en", corps, "le lien garde la langue")

    def test_l_adresse_postale_est_dans_le_pied(self):
        """La LCAP veut une adresse dans un message qui demande un consentement."""
        self.env.company.partner_id.write({
            "street": "1 rue de l'Essai", "city": "Québec", "zip": "G1A 1A1"})
        self._habiller()
        self._poster()
        corps = self._corps_html(self.envois[0])
        self.assertIn("1 rue de l'Essai", corps)
        self.assertIn("G1A 1A1", corps)

    # ------------------------------------------------------------------- avis

    def _avis(self, fragment):
        """Le message d'avis interne dont le sujet porte `fragment`, ou None."""
        for message in self.envois:
            if fragment in self._sujet(message):
                return message
        return None

    def test_avis_a_la_demande(self):
        self.env["ir.config_parameter"].sudo().set_param(
            ctl.ICP_NOTIFY, "veille@example.com")
        self._poster()
        self.assertEqual(len(self.envois), 2,
                         "la confirmation à la personne, l'avis à l'interne")
        avis = self._avis("demande d'inscription")
        self.assertIsNotNone(avis, f"sujets vus : {[self._sujet(m) for m in self.envois]}")
        self.assertEqual(avis["To"], "veille@example.com")
        corps = self._corps_html(avis)
        self.assertIn(self.adresse, corps)
        self.assertIn("en attente de confirmation", corps)
        self.assertIn("/odoo/m-mailing.contact/", corps, "un lien vers la fiche")

    def test_avis_a_la_confirmation(self):
        self.env["ir.config_parameter"].sudo().set_param(
            ctl.ICP_NOTIFY, "veille@example.com")
        self._poster()
        self.envois.clear()
        self.url_open(f"/infolettre/confirmer?e={self.adresse}&j={self._jeton()}&lang=fr",
                      allow_redirects=False)
        avis = self._avis("inscription confirmée")
        self.assertIsNotNone(avis, "la confirmation doit prévenir l'interne")
        self.assertIn("Consentement exprès confirmé", self._corps_html(avis))

    def test_aucun_avis_sans_destinataire(self):
        """Le défaut est le silence — c'est ce qui rend le module publiable."""
        self._poster()
        self.assertEqual(len(self.envois), 1, "seule la confirmation part")

    def test_un_lien_recharge_n_avise_pas_deux_fois(self):
        """Recharger le lien n'est pas une deuxième inscription.

        Prévenir deux fois pour la même personne apprend à ignorer l'avis.
        """
        self.env["ir.config_parameter"].sudo().set_param(
            ctl.ICP_NOTIFY, "veille@example.com")
        self._poster()
        lien = f"/infolettre/confirmer?e={self.adresse}&j={self._jeton()}&lang=fr"
        self.url_open(lien, allow_redirects=False)
        self.envois.clear()
        self.url_open(lien, allow_redirects=False)
        self.assertEqual(self.envois, [], "le deuxième passage n'avise personne")

    def test_un_avis_qui_casse_ne_casse_pas_l_inscription(self):
        """🔴 L'avis est un confort d'exploitation, pas une étape du parcours.

        S'il lève, la personne qui vient de s'inscrire ne doit ni le voir ni
        perdre son inscription, et la confirmation doit être partie quand même.
        """
        self.env["ir.config_parameter"].sudo().set_param(
            ctl.ICP_NOTIFY, "veille@example.com")
        with patch.object(ctl, "_avis_html", side_effect=RuntimeError("boum")):
            r = self._poster()
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["Location"], "/infolettre-merci.html")
        self.assertTrue(self._inscription(), "l'inscription existe quand même")
        self.assertEqual(len(self.envois), 1, "la confirmation est partie")
        self.assertIn(self._jeton(), self._corps_html(self.envois[0]))


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

import json
import re
from datetime import date, timedelta
from email.header import decode_header, make_header
from unittest.mock import patch

from odoo import fields
from odoo.addons.base.models.ir_mail_server import IrMailServer
from odoo.tests import tagged
from odoo.tests.common import HttpCase
from odoo.tools import mute_logger

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
        # Sur une copie de la prod, `notify_email` est posé : l'avis interne
        # ajoutait un courriel et trois essais comptaient faux. Les essais de
        # l'avis le posent eux-mêmes.
        self.env["ir.config_parameter"].sudo().set_param(ctl.ICP_NOTIFY, "")
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

    def _confirmer(self, jeton=None, lang="fr"):
        """Le geste de la personne : le POST du bouton de la page de confirmation."""
        return self.url_open("/infolettre/confirmer", data={
            "e": self.adresse, "j": jeton or self._jeton(), "lang": lang},
            allow_redirects=False)

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
        r = self._confirmer()
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
        self._confirmer(jeton=vieux)
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
        self._confirmer()
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

    def test_le_site_de_la_societe_s_ajoute_a_l_adresse(self):
        """Le lien s'AJOUTE : l'adresse postale reste, la LCAP la veut."""
        self.env.company.partner_id.write({
            "street": "1 rue de l'Essai", "city": "Québec", "zip": "G1A 1A1"})
        self._habiller()
        self.env["ir.config_parameter"].sudo().set_param(
            ctl.ICP_COMPANY_URL, "https://societe.test/")
        self._poster()
        corps = self._corps_html(self.envois[0])
        self.assertIn('href="https://societe.test/"', corps)
        self.assertIn(">societe.test</a>", corps)
        self.assertIn("1 rue de l'Essai", corps)

    def test_sans_site_de_societe_le_pied_garde_le_nom_seul(self):
        self._habiller()
        self.env["ir.config_parameter"].sudo().set_param(ctl.ICP_COMPANY_URL, "")
        self._poster()
        self.assertNotIn(" · <a href=", self._corps_html(self.envois[0]))

    def test_un_site_de_societe_hors_http_est_ignore(self):
        """La valeur finit dans un `href` : ni `javascript:` ni `data:`."""
        self._habiller()
        for valeur in ("javascript:alert(1)", "data:text/html,x", "societe.test"):
            self.env["ir.config_parameter"].sudo().set_param(ctl.ICP_COMPANY_URL, valeur)
            self.assertEqual(ctl._marque(self.env)["site_societe"], "", valeur)

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
        self._confirmer()
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
        self._confirmer()
        self.envois.clear()
        self._confirmer()
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

    # ------------------------------------------------ robots d'analyse de liens
    #
    # 🔴 Ouvrir le lien ne confirme rien, seul le bouton confirme.
    #
    # Les passerelles de courriel ouvrent chaque lien d'un message avant la
    # personne (en GET, parfois en HEAD). Tant que le GET activait l'inscription,
    # un tiers pouvait inscrire l'adresse d'une entreprise dont la passerelle
    # analyse les liens : le double consentement se confirmait tout seul.

    def _lien(self):
        return f"/infolettre/confirmer?e={self.adresse}&j={self._jeton()}&lang=fr"

    def test_le_get_du_lien_n_active_pas(self):
        self._poster()
        r = self.url_open(self._lien(), allow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self._inscription().opt_out, "un robot a confirmé")
        self.assertIn('method="post"', r.text)
        self.assertIn(self._jeton(), r.text)

    def test_le_head_du_lien_n_active_pas(self):
        self._poster()
        self.opener.head(self.base_url() + self._lien(), timeout=10,
                         allow_redirects=False)
        self.assertTrue(self._inscription().opt_out)

    def test_le_get_d_un_lien_faux_renvoie_au_formulaire(self):
        self._poster()
        r = self.url_open(
            f"/infolettre/confirmer?e={self.adresse}&j={'0' * 32}&lang=fr",
            allow_redirects=False)
        self.assertEqual(r.headers["Location"], "/#infolettre")

    def test_le_bouton_confirme(self):
        self._poster()
        r = self._confirmer()
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["Location"], "/infolettre-confirme.html")
        self.assertFalse(self._inscription().opt_out)

    def test_la_page_anglaise_est_en_anglais(self):
        self._poster(lang="en")
        r = self.url_open(
            f"/infolettre/confirmer?e={self.adresse}&j={self._jeton()}&lang=en",
            allow_redirects=False)
        self.assertIn('lang="en"', r.text)
        self.assertTrue(self._inscription().opt_out)

    # ------------------------------------------------------------ relecture adverse

    def test_un_jeton_non_ascii_ne_rend_pas_d_erreur(self):
        """`hmac.compare_digest` levait sur une chaîne non ASCII : une 500, et
        seulement pour une adresse inscrite, donc un oracle."""
        self._poster()
        for jeton in ("é" * 32, "é", "Z" * 32, "0" * 31, "0" * 33):
            r = self.url_open("/infolettre/confirmer",
                              data={"e": self.adresse, "j": jeton, "lang": "fr"},
                              allow_redirects=False)
            self.assertEqual(r.status_code, 303, jeton)
            self.assertEqual(r.headers["Location"], "/#infolettre")
        self.assertTrue(self._inscription().opt_out)

    def test_un_champ_envoye_en_fichier_ne_rend_pas_d_erreur(self):
        for champ in ("courriel", "lang", "site_web"):
            ctl._bucket_data.clear()
            donnees = {"courriel": self.adresse, "lang": "fr"}
            donnees.pop(champ, None)
            r = self.opener.post(self.base_url() + "/infolettre", data=donnees,
                                 files={champ: ("x.txt", b"essai", "text/plain")},
                                 allow_redirects=False, timeout=10)
            self.assertEqual(r.status_code, 303, champ)
        r = self.opener.post(self.base_url() + "/infolettre/confirmer",
                             data={"lang": "fr"}, files={"e": ("x.txt", b"x", "text/plain"),
                                                          "j": ("y.txt", b"y", "text/plain")},
                             allow_redirects=False, timeout=10)
        self.assertEqual(r.status_code, 303)

    def test_un_corps_trop_gros_est_refuse(self):
        r = self.url_open("/infolettre", data={"courriel": self.adresse,
                                               "site_web": "x" * (ctl.MAX_CORPS + 10)},
                          allow_redirects=False)
        self.assertEqual(r.status_code, 413)
        self.assertFalse(self._inscription())

    def test_le_refus_du_seau_se_journalise(self):
        for _i in range(ctl._SIGNUP_MAX):
            self._poster()
        with self.assertLogs("odoo.addons.bf_mailing_signup.controllers.main",
                             level="WARNING") as journal:
            self._poster()
        self.assertTrue(any("seau « signup » plein" in l for l in journal.output))

    def test_la_confirmation_ne_porte_aucun_pixel(self):
        """Ni l'en-tête marqueur ni une image de suivi ne sortent. Si `mail_tracking`
        est installé, son image est retirée ; sinon l'essai vérifie l'en-tête."""
        self._poster()
        message = self.envois[0]
        self.assertNotIn(ctl.ENTETE_SANS_SUIVI, message)
        self.assertNotIn("data-odoo-tracking-email", self._corps_html(message))
        self.assertNotIn("X-Odoo-MailTracking-ID", message)

    def test_build_email_retire_le_pixel_des_seuls_courriels_marques(self):
        pixel = '<img src="https://x.example/t.gif" data-odoo-tracking-email="42"/>'
        serveur = self.env["ir.mail_server"]
        marque = serveur.build_email("a@example.com", ["b@example.com"], "s",
                                     f"<p>corps</p>{pixel}", subtype="html",
                                     headers={ctl.ENTETE_SANS_SUIVI: "1", "X-Autre": "garde"})
        corps = marque.get_body(preferencelist=("html",)).get_content()
        self.assertNotIn("data-odoo-tracking-email", corps)
        self.assertIn("corps", corps)
        self.assertNotIn(ctl.ENTETE_SANS_SUIVI, marque)
        self.assertEqual(marque["X-Autre"], "garde")
        ordinaire = serveur.build_email("a@example.com", ["b@example.com"], "s",
                                        f"<p>corps</p>{pixel}", subtype="html")
        self.assertIn("data-odoo-tracking-email",
                      ordinaire.get_body(preferencelist=("html",)).get_content())

    def test_build_email_retire_sans_rien_ecrire(self):
        """🔴 `build_email` est publique. Elle retire le pixel et les
        en-têtes de `mail_tracking` (posés quand il passe AVANT nous), et n'écrit
        RIEN, même quand le corps et l'en-tête portent des identifiants de relevé.
        Un courriel ordinaire garde les siens (le suivi des autres reste intact)."""
        Notes = self.env["bf.mailing.signup.tracking"].sudo()
        avant = Notes.search_count([])
        pixel = '<img src="https://x.example/t.gif" data-odoo-tracking-email="424242"/>'
        entetes = {"X-Odoo-MailTracking-ID": "424243", "X-Odoo-Database": "essai",
                   "X-Autre": "garde"}
        serveur = self.env["ir.mail_server"]
        marque = serveur.build_email("a@example.com", ["b@example.com"], "s",
                                     f"<p>corps</p>{pixel}", subtype="html",
                                     headers=dict(entetes, **{ctl.ENTETE_SANS_SUIVI: "1"}))
        self.assertEqual(Notes.search_count([]), avant, "build_email n'écrit rien")
        for entete in (ctl.ENTETE_SANS_SUIVI, "X-Odoo-MailTracking-ID", "X-Odoo-Database"):
            self.assertNotIn(entete, marque)
        self.assertNotIn("data-odoo-tracking-email",
                         marque.get_body(preferencelist=("html",)).get_content())
        self.assertEqual(marque["X-Autre"], "garde")
        ordinaire = serveur.build_email("a@example.com", ["b@example.com"], "s",
                                        "<p>corps</p>", subtype="html", headers=dict(entetes))
        self.assertEqual(ordinaire["X-Odoo-MailTracking-ID"], "424243")
        self.assertEqual(Notes.search_count([]), avant)

    def test_portail_par_json_rpc_ne_fait_rien_noter(self):
        """🔴 Rejoué tel quel : un usager PORTAIL appelle
        `/web/dataset/call_kw/ir.mail_server/build_email` avec l'en-tête marqueur et
        cinquante « pixels » d'identifiants choisis. L'appel aboutit (la méthode est
        publique), et aucune ligne n'est écrite pour la purge."""
        self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Portail d'essai", "login": "portail.n1@example.com",
            "email": "portail.n1@example.com", "password": "portail-n1-essai",
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])]})
        Notes = self.env["bf.mailing.signup.tracking"].sudo()
        avant = Notes.search_count([])
        self.authenticate("portail.n1@example.com", "portail-n1-essai")
        corps = "".join(f'<img data-odoo-tracking-email="{i}">'
                        for i in range(10 ** 6, 10 ** 6 + 50))
        r = self.url_open("/web/dataset/call_kw/ir.mail_server/build_email", data=json.dumps({
            "jsonrpc": "2.0", "method": "call", "id": 1, "params": {
                "model": "ir.mail_server", "method": "build_email",
                "args": [[], "a@example.com", ["b@example.com"], "x", corps],
                "kwargs": {"subtype": "html",
                           "headers": {ctl.ENTETE_SANS_SUIVI: "1",
                                       "X-Odoo-MailTracking-ID": "777"}}}}),
            headers={"Content-Type": "application/json"})
        reponse = r.json()
        self.assertNotIn("error", reponse, "l'appel du portail a bien atteint build_email")
        self.assertEqual(Notes.search_count([]), avant, "le portail n'a rien fait noter")

    def test_les_releves_se_notent_par_le_mail_id_des_courriels_marques(self):
        """Notés à l'envoi, par le `mail_id` : le relevé d'un courriel marqué l'est,
        celui d'un courriel ordinaire ne l'est pas (sa purge n'est pas la nôtre)."""
        if "mail.tracking.email" not in self.env:
            self.skipTest("mail_tracking n'est pas installé sur cette base")
        Notes = self.env["bf.mailing.signup.tracking"].sudo()
        avant = set(Notes.search([]).mapped("tracking_email_id"))
        Mail = self.env["mail.mail"].sudo()
        ordinaire = Mail.create({"subject": "Ordinaire", "body_html": "<p>x</p>",
                                 "email_to": "ordinaire.n1@example.com"})
        marque = Mail.create({"subject": "Marqué", "body_html": "<p>x</p>",
                              "email_to": "marque.n1@example.com",
                              "headers": repr({ctl.ENTETE_SANS_SUIVI: "1"})})
        (ordinaire | marque).send()
        Releve = self.env["mail.tracking.email"].sudo()
        r_ordinaire = Releve.search([("recipient", "=", "ordinaire.n1@example.com")])
        r_marque = Releve.search([("recipient", "=", "marque.n1@example.com")])
        self.assertTrue(r_ordinaire and r_marque, "mail_tracking relève les deux envois")
        notes = set(Notes.search([]).mapped("tracking_email_id")) - avant
        self.assertEqual(notes, set(r_marque.ids))

    def test_proxy_sans_x_forwarded_host_fait_un_seau_unique(self):
        """🔴 Le défaut vu en relecture : derrière un proxy qui n'envoie pas
        `X-Forwarded-Host`, Odoo n'applique pas ProxyFix et tous les visiteurs
        partagent l'adresse du proxy. Avec l'en-tête, chacun a son seau."""
        from unittest.mock import patch as patcher
        from odoo.tools import config
        with patcher.dict(config.options, {"proxy_mode": True}), \
                patcher.object(ctl, "_proxy_sans_hote_signale", False):
            ctl._bucket_data.clear()
            with self.assertLogs("odoo.addons.bf_mailing_signup.controllers.main",
                                 level="WARNING") as journal:
                for i in range(ctl._SIGNUP_MAX + 1):
                    self.url_open("/infolettre", data={"courriel": f"v{i}@example.com"},
                                  headers={"X-Forwarded-For": f"203.0.113.{i + 1}"},
                                  allow_redirects=False)
            self.assertTrue(any("sans X-Forwarded-Host" in l for l in journal.output))
            self.assertTrue(any("seau « signup » plein" in l for l in journal.output),
                            "sans X-Forwarded-Host, six visiteurs distincts remplissent UN seau")
            ctl._bucket_data.clear()
            with self.assertNoLogs("odoo.addons.bf_mailing_signup.controllers.main",
                                   level="WARNING"):
                for i in range(ctl._SIGNUP_MAX + 1):
                    self.url_open("/infolettre", data={"courriel": f"w{i}@example.com"},
                                  headers={"X-Forwarded-For": f"198.51.100.{i + 1}",
                                           "X-Forwarded-Host": "www.example.com"},
                                  allow_redirects=False)
            inscrits = self.env["mailing.contact"].search(
                [("email_normalized", "=like", "w_@example.com")])
            self.assertEqual(len(inscrits), ctl._SIGNUP_MAX + 1,
                             "avec X-Forwarded-Host, chaque visiteur a son seau")


    # -------------------------------------------------- deuxième passe de relecture

    def _sujets(self):
        return [str(make_header(decode_header(m["Subject"]))) for m in self.envois]

    def _confirmations_a(self, fragment):
        return [m for m in self.envois if fragment in (m["To"] or "")
                and "Confirmez" in str(make_header(decode_header(m["Subject"])))]

    def test_trois_confirmations_par_boite_sous_adressage_compris(self):
        ips = iter(f"198.51.100.{n}" for n in range(1, 40))
        # Le refus vient du compte, avant toute écriture : jamais l'erreur de la
        # contrainte CHECK, qui ne sert que de seconde ligne.
        with patch.object(ctl, "_client_ip", side_effect=lambda: next(ips)), \
                self.assertNoLogs("odoo.addons.bf_mailing_signup.controllers.main", level="ERROR"), \
                mute_logger("odoo.sql_db"):
            for i in range(6):
                self._poster(courriel=f"cible+{i}@example.com")
            for a in ("c.i.b.l.e@gmail.com", "cible+x@googlemail.com", "CIBLE@gmail.com",
                      "ci.ble@gmail.com"):
                self._poster(courriel=a)
        self.assertEqual(len(self._confirmations_a("@example.com")), 3)
        self.assertEqual(len([m for m in self.envois
                              if (m["To"] or "").endswith(("@gmail.com>", "@gmail.com",
                                                           "@googlemail.com>", "@googlemail.com"))]),
                         3)
        self.assertEqual(len(self.envois), 6)

    def test_la_trace_ne_garde_pas_l_adresse(self):
        self._poster()
        trace = self.env["bf.mailing.signup.trace"].sudo().search([], order="id desc", limit=1)
        self.assertEqual(len(trace.key), 64)
        self.assertNotIn("@", trace.key)
        self.assertNotIn("essai", trace.key)
        self.assertEqual(trace.key, ctl._cle_boite(self.env, "essai.inscription+x@example.com"))

    def test_course_sur_le_plafond_par_boite(self):
        Trace = type(self.env["bf.mailing.signup.trace"])
        self.env["bf.mailing.signup.trace"].sudo().create(
            {"key": ctl._cle_boite(self.env, self.adresse), "day": fields.Date.today(), "rank": 1})
        with patch.object(Trace, "search_count", return_value=0), \
                self.assertLogs("odoo.addons.bf_mailing_signup.controllers.main",
                                level="WARNING") as journal, \
                self.assertNoLogs("odoo.sql_db", level="ERROR"):
            r = self._poster()
        self.assertEqual(r.headers["Location"], "/infolettre-merci.html")
        self.assertTrue(any("en concurrence" in l for l in journal.output))
        self.assertFalse(self.envois, "le perdant n'envoie rien")
        self.assertFalse(self._inscription(), "ni inscription en attente")

    def test_plafond_global_page_occupe_et_un_seul_avis(self):
        self.env["ir.config_parameter"].sudo().set_param(ctl.ICP_NOTIFY, "veille@example.com")
        for constante, page in (("_GLOBAL_HEURE", "/infolettre-occupe.html"),
                                ("_GLOBAL_JOUR", "/en/infolettre-occupe.html")):
            self.envois.clear()
            with patch.object(ctl, constante, 0):
                for i in range(3):
                    ctl._bucket_data.clear()
                    r = self._poster(courriel=f"occupe{i}@example.com",
                                     lang="en" if "JOUR" in constante else "fr")
                    self.assertEqual(r.headers["Location"], page, constante)
            # L'avis interne part en file, marqué (ni pixel, relevé effacé à sept
            # jours) : on la vide comme le ferait la tâche.
            en_file = self.env["mail.mail"].sudo().search(
                [("state", "=", "outgoing"), ("subject", "like", "plafond global")])
            self.assertEqual(len(en_file), 1, constante)
            self.assertIn(ctl.ENTETE_SANS_SUIVI, en_file.headers or "", constante)
            self.env["mail.mail"].sudo().search([("state", "=", "outgoing")]).send()
            avis = [s for s in self._sujets() if "plafond global" in s]
            self.assertEqual(len(avis), 1, constante)
            self.assertFalse(self.env["mailing.contact"].search(
                [("email_normalized", "=like", "occupe%")]), constante)

    def test_mot_encode_rfc2047_refuse(self):
        for adresse in ("=?utf-8?q?attaquant=40pirate.example?=@victime.example",
                        "a=b@example.com", "a?b@example.com", "a,b@example.com"):
            ctl._bucket_data.clear()
            self._poster(courriel=adresse)
        self.assertFalse(self.envois)
        self.assertFalse(self.env["mailing.contact"].search([("email", "ilike", "victime")]))

    def test_origine_etrangere_refusee(self):
        self.env["ir.config_parameter"].sudo().set_param(ctl.ICP_BASE_URL, "https://symbifox.example")
        for entetes in ({"Origin": "https://pirate.example"}, {"Origin": "null"},
                        {"Sec-Fetch-Site": "cross-site"}):
            ctl._bucket_data.clear()
            r = self.url_open("/infolettre", data={"courriel": self.adresse},
                              headers=entetes, allow_redirects=False)
            self.assertEqual(r.status_code, 403, entetes)
        self.assertFalse(self._inscription())
        for lang, entetes in (("fr", {"Origin": "https://symbifox.example",
                                      "Sec-Fetch-Site": "same-origin"}),
                              ("en", {"Origin": "https://symbifox.example"})):
            ctl._bucket_data.clear()
            r = self.url_open("/infolettre", data={"courriel": self.adresse, "lang": lang},
                              headers=entetes, allow_redirects=False)
            self.assertEqual(r.status_code, 303, lang)
        self.assertTrue(self._inscription())

    def test_releves_mail_tracking_notes_et_purges(self):
        if "mail.tracking.email" not in self.env:
            self.skipTest("mail_tracking n'est pas installé sur cette base")
        self.env["ir.config_parameter"].sudo().set_param(ctl.ICP_NOTIFY, "veille@example.com")
        Notes = self.env["bf.mailing.signup.tracking"].sudo()
        avant = Notes.search([]).ids
        self._poster()
        notes = Notes.search([("id", "not in", avant)])
        releves = self.env["mail.tracking.email"].sudo().browse(
            notes.mapped("tracking_email_id")).exists()
        self.assertEqual(set(releves.mapped("recipient")), {self.adresse, "veille@example.com"},
                         "confirmation et avis interne notés")
        for m in self.envois:
            self.assertNotIn("data-odoo-tracking-email", self._corps_html(m))
        trace = self.env["bf.mailing.signup.trace"].sudo().search([], order="id desc", limit=1)
        self.env.cr.execute("UPDATE bf_mailing_signup_trace SET create_date = %s WHERE id = %s",
                            [fields.Datetime.now() - timedelta(hours=49), trace.id])
        self.env.invalidate_all()
        Notes._cron_purger()
        self.assertFalse(trace.exists(), "trace de plus de 48 h effacée")
        self.assertTrue(releves.exists(), "relevés de moins de sept jours gardés")
        self.env.cr.execute("UPDATE bf_mailing_signup_tracking SET create_date = %s WHERE id IN %s",
                            [fields.Datetime.now() - timedelta(days=8), tuple(notes.ids)])
        self.env.invalidate_all()
        Notes._cron_purger()
        self.assertFalse(releves.exists())
        self.assertFalse(notes.exists())

    # ------------------------------------------------- troisième passe de relecture

    def _verrou_tenu(self, cle):
        """Vrai si cette transaction tient le verrou consultatif `cle`."""
        self.env.cr.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND pid = pg_backend_pid() AND classid = %s::bigint::oid "
            "AND objid = %s::bigint::oid AND objsubid = 1",
            [(cle >> 32) & 0xFFFFFFFF, cle & 0xFFFFFFFF])
        return bool(self.env.cr.fetchone()[0])

    def test_les_plafonds_se_comptent_sous_verrou(self):
        """Le compte-puis-écrit des plafonds prend le verrou consultatif de
        l'infolettre (tenu jusqu'à la fin de la transaction). Que le compte se fasse
        sur une transaction neuve, seul un envoi concurrent le montre : le parcours
        `--proxy-mode` le vérifie (24 envois simultanés, 20 lignes au plus)."""
        self.assertFalse(self._verrou_tenu(ctl.VERROU_PLAFONDS))
        self._poster()
        self.assertTrue(self._verrou_tenu(ctl.VERROU_PLAFONDS))
        self.assertTrue(self._inscription())

    def test_un_avis_qui_casse_en_sql_ne_perd_pas_l_inscription(self):
        """Un avis qui lève une erreur SQL (transaction en échec) : sous son
        `savepoint`, l'inscription et sa confirmation tiennent quand même."""
        self.env["ir.config_parameter"].sudo().set_param(ctl.ICP_NOTIFY, "veille@example.com")

        def avis_casse(env, *_a, **_k):
            env.cr.execute("SELECT 1 / 0")

        with patch.object(ctl, "_avis_html", side_effect=avis_casse), \
                mute_logger("odoo.sql_db", "odoo.addons.bf_mailing_signup.controllers.main"):
            r = self._poster()
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["Location"], "/infolettre-merci.html")
        self.assertTrue(self._inscription(), "l'inscription existe")
        self.assertTrue(self.env["bf.mailing.signup.trace"].sudo().search(
            [("key", "=", ctl._cle_boite(self.env, self.adresse))]), "et sa trace")
        self.assertEqual(len(self.envois), 1, "la confirmation est partie")

    def _vieillir_contact(self, contact, jours=8, datetime_desinscription=None):
        """Recule un contact et ses inscriptions de `jours` jours, en SQL."""
        quand = fields.Datetime.now() - timedelta(days=jours)
        self.env.cr.execute("UPDATE mailing_contact SET create_date = %s WHERE id = %s",
                            [quand, contact.id])
        self.env.cr.execute(
            "UPDATE mailing_subscription SET create_date = %s, opt_out_datetime = "
            "CASE WHEN opt_out THEN %s ELSE NULL END WHERE contact_id = %s",
            [quand, datetime_desinscription or quand, contact.id])
        self.env.invalidate_all()

    def test_purge_des_contacts_jamais_confirmes(self):
        """Seul part le contact que le formulaire a créé et que personne n'a
        confirmé depuis plus de sept jours. Tout le reste est gardé."""
        Contact = self.env["mailing.contact"].sudo()
        cas = ("vieux", "recent", "confirme", "desinscrit", "mixte", "raison", "liste_neuve")
        adresses = {k: f"f7-{k.replace('_', '-')}@example.com" for k in cas}
        for k in cas:
            ctl._bucket_data.clear()
            self._poster(courriel=adresses[k])
        for k in ("confirme", "desinscrit"):
            self.adresse = adresses[k]
            self._confirmer()
        contacts = {k: Contact.search([("email_normalized", "=", a)]) for k, a in adresses.items()}
        self.assertTrue(all(c.create_uid == self.env.ref("base.public_user") for c in
                            contacts.values()), "créés par la route publique")
        autre = self.env["mailing.list"].create({"name": "Essai — autre liste"})
        self.env["mailing.subscription"].sudo().create(
            {"contact_id": contacts["mixte"].id, "list_id": autre.id, "opt_out": False})
        interne = Contact.create({"email": "f7-interne@example.com"})
        public = self.env.ref("base.public_user")
        sans_inscription = Contact.with_user(public).sudo().create(
            {"email": "f7-sans-inscription@example.com"})
        self.env["mailing.subscription"].sudo().create(
            {"contact_id": interne.id, "list_id": self.liste.id, "opt_out": True})
        for k in ("vieux", "confirme", "mixte", "raison", "liste_neuve"):
            self._vieillir_contact(contacts[k])
        self._vieillir_contact(interne)
        self._vieillir_contact(sans_inscription)
        # Un abonnement actif dont la date de désinscription vaut celle de création
        # (une donnée importée) : actif, donc gardé, quelle que soit cette date.
        self.env.cr.execute("UPDATE mailing_subscription SET opt_out_datetime = create_date "
                            "WHERE contact_id = %s AND list_id = %s",
                            [contacts["mixte"].id, autre.id])
        # Désinscrit trois jours après son inscription : son refus doit rester.
        contacts["desinscrit"].subscription_ids.write({"opt_out": True})
        self._vieillir_contact(contacts["desinscrit"], datetime_desinscription=(
            fields.Datetime.now() - timedelta(days=5)))
        raison = self.env["mailing.subscription.optout"].sudo().search([], limit=1) or \
            self.env["mailing.subscription.optout"].sudo().create({"name": "Essai"})
        self.env.cr.execute("UPDATE mailing_subscription SET opt_out_reason_id = %s "
                            "WHERE contact_id = %s", [raison.id, contacts["raison"].id])
        # Contact ancien, inscription récente (une autre liste) : pas encore sept jours.
        self.env["mailing.subscription"].sudo().create(
            {"contact_id": contacts["liste_neuve"].id, "list_id": autre.id, "opt_out": True})
        self.env.invalidate_all()
        self.env["bf.mailing.signup.tracking"]._cron_purger()
        self.assertFalse(contacts["vieux"].exists(), "en attente depuis huit jours : effacé")
        self.assertFalse(self.env["mailing.subscription"].search(
            [("contact_id", "=", contacts["vieux"].id)]), "avec son inscription")
        for k in ("recent", "confirme", "desinscrit", "mixte", "raison", "liste_neuve"):
            self.assertTrue(contacts[k].exists(), k)
        self.assertTrue(interne.exists(), "créé par un usager interne : jamais visé")
        self.assertTrue(sans_inscription.exists(), "sans inscription : pas « en attente »")

    def test_desinstallation_efface_les_releves_notes(self):
        """La table des relevés à purger part avec le module ; le crochet de
        désinstallation efface donc tout de suite les relevés notés, quel que soit
        leur âge, et laisse les autres."""
        if "mail.tracking.email" not in self.env:
            self.skipTest("mail_tracking n'est pas installé sur cette base")
        from .. import uninstall_hook
        Releve = self.env["mail.tracking.email"].sudo()
        notes = Releve.create([{"name": f"Noté {i}", "recipient": f"r2-{i}@example.com"}
                               for i in range(2)])
        libre = Releve.create({"name": "Pas noté", "recipient": "r2-libre@example.com"})
        self.env["bf.mailing.signup.tracking"].sudo().create(
            [{"tracking_email_id": i} for i in notes.ids])
        uninstall_hook(self.env)
        self.assertFalse(notes.exists())
        self.assertTrue(libre.exists())


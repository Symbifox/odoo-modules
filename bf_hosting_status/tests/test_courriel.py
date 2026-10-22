# -*- coding: utf-8 -*-
from datetime import timedelta

from markupsafe import Markup

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.bf_hosting_status.models.mail_mail import masquer_jetons


@tagged("post_install", "-at_install")
class TestCourriel(TransactionCase):
    """Le lien d'état dans l'avis : masqué au chatter, recalculé à l'envoi."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Les libellés anglais de l'avis : en_US n'est pas actif sur toutes les bases.
        cls.env["res.lang"]._activate_lang("en_US")
        logiciel = cls.env["hosting.software"].create({
            "name": "Nuage Courriel", "code": "nuage-essai-courriel", "software_type": "self_hosted"})
        Partner = cls.env["res.partner"]
        cls.client_a = Partner.create({"name": "Érablière Alpha Courriel", "is_company": True,
                                       "lang": "en_US"})
        cls.client_b = Partner.create({"name": "Brasserie Bêta Courriel", "is_company": True})
        cls.ana = Partner.create({"name": "Ana Alpha", "parent_id": cls.client_a.id,
                                  "email": "ana@alpha-courriel.example", "lang": "en_US"})
        cls.carl = Partner.create({"name": "Carl Alpha", "parent_id": cls.client_a.id,
                                   "email": "carl@alpha-courriel.example", "lang": "en_US"})
        Service = cls.env["hosting.service"]
        cls.service_a = Service.create({
            "name": "Nuage Alpha", "software_id": logiciel.id, "state": "active",
            "partner_id": cls.client_a.id, "server_url": "https://nuage.alpha-courriel.example"})
        Service.create({
            "name": "Nuage Bêta", "software_id": logiciel.id, "state": "active",
            "partner_id": cls.client_b.id, "server_url": "https://nuage.beta-courriel.example"})
        cls.client_a.hosting_recipient_ids = cls.ana
        cls.client_a.action_hosting_status_publish()
        cls.client_b.action_hosting_status_publish()
        maintenant = fields.Datetime.now()
        cls.fenetre = cls.env["hosting.maintenance.window"].create({
            "name": "Mise à niveau", "date_start": maintenant + timedelta(days=3),
            "date_end": maintenant + timedelta(days=3, hours=1),
            "impact": "Environ 30 minutes d'interruption.",
            "service_ids": [Command.set(cls.service_a.ids)]})
        cls.fenetre.action_announce()
        cls.gabarit = cls.env.ref("hosting_management.mail_template_client_maintenance_notice")
        cls.interne = new_test_user(cls.env, login="essai_courriel_interne",
                                    groups="base.group_user,base.group_partner_manager")
        cls.contacts = new_test_user(
            cls.env, login="essai_courriel_contacts",
            groups="base.group_user,base.group_partner_manager,base.group_erp_manager")

    def _avis(self, destinataire, **valeurs):
        """Le chemin exact de la skill `plan-maintenance-window`, phase 3 : un avis programmé."""
        ctx = self.fenetre._hosting_notice_context(destinataire)
        email_values = {
            "subject": "Avis d'essai",
            "email_to": False,
            "recipient_ids": [Command.link(destinataire.id)],
            "auto_delete": False,
            "scheduled_date": fields.Datetime.now() + timedelta(days=2),
        }
        email_values.update(valeurs)
        mail_id = self.gabarit.with_context(**ctx).send_mail(destinataire.id, email_values=email_values)
        return self.env["mail.mail"].browse(mail_id)

    @staticmethod
    def _corps_envoye(mail, partenaire=None):
        """Les corps que le serveur de courriel recevrait, par destinataire."""
        envois = mail._prepare_outgoing_list()
        if partenaire is None:
            return [e["body"] for e in envois]
        return [e["body"] for e in envois if e["partner_id"] == partenaire]

    # ------------------------------------------------------------------
    # 1. Le chatter
    # ------------------------------------------------------------------

    def test_le_chatter_ne_montre_pas_le_jeton(self):
        jeton = self.client_a.hosting_status_token
        mail = self._avis(self.ana)
        self.assertIn(jeton, mail.body_html)
        message = mail.mail_message_id
        self.assertEqual((message.model, message.res_id), ("res.partner", self.ana.id))
        self.assertNotIn(jeton, message.body)
        self.assertIn("/etat/%s/jeton-masque" % self.client_a.id, message.body)
        # Ce que lit un interne sans le groupe hébergement, au chatter de la fiche.
        corps = self.ana.with_user(self.interne).message_ids.mapped("body")
        self.assertTrue(corps)
        self.assertFalse([c for c in corps if jeton in c])
        # Et le courriel, lui, reste hors de sa portée.
        with self.assertRaises(AccessError):
            mail.with_user(self.interne).read(["body_html"])

    def test_le_courriel_part_avec_le_lien(self):
        jeton = self.client_a.hosting_status_token
        mail = self._avis(self.ana)
        [corps] = self._corps_envoye(mail, self.ana)
        self.assertIn("/etat/%s/%s" % (self.client_a.id, jeton), corps)
        self.assertIn("o_hosting_status_link", corps)

    # ------------------------------------------------------------------
    # 2. Le lien relu à l'envoi
    # ------------------------------------------------------------------

    def test_nouvelle_adresse_le_rappel_porte_la_nouvelle(self):
        ancien = self.client_a.hosting_status_token
        rappel = self._avis(self.ana)
        self.client_a.action_hosting_status_regenerate_token()
        nouveau = self.client_a.hosting_status_token
        self.assertNotEqual(ancien, nouveau)
        [corps] = self._corps_envoye(rappel, self.ana)
        self.assertIn("/etat/%s/%s" % (self.client_a.id, nouveau), corps)
        self.assertNotIn(ancien, corps)
        # La page s'ouvre bien par le lien donné.
        self.assertTrue(self.client_a.hosting_status_published)

    def _sans_paragraphe(self, corps):
        self.assertNotIn("/etat/", corps)
        self.assertNotIn("o_hosting_status_link", corps)
        self.assertNotIn("status page", corps)
        # Le reste de l'avis est là.
        self.assertIn("Nuage Alpha", corps)
        self.assertIn("minimize the impact", corps)

    def test_depubliee_le_paragraphe_disparait(self):
        rappel = self._avis(self.ana)
        self.client_a.action_hosting_status_unpublish()
        [corps] = self._corps_envoye(rappel, self.ana)
        self._sans_paragraphe(corps)

    def test_destinataire_retire_deplace_ou_archive(self):
        for cas in ("retire", "deplace", "archive"):
            with self.subTest(cas=cas):
                self.ana.active = True
                self.ana.parent_id = self.client_a
                self.client_a.hosting_recipient_ids = self.ana
                rappel = self._avis(self.ana)
                [corps] = self._corps_envoye(rappel, self.ana)
                self.assertIn(self.client_a.hosting_status_token, corps)
                if cas == "retire":
                    self.client_a.hosting_recipient_ids = False
                elif cas == "deplace":
                    self.ana.parent_id = self.client_b
                else:
                    self.ana.active = False
                [corps] = self._corps_envoye(rappel, self.ana)
                self._sans_paragraphe(corps)

    def test_une_copie_en_email_to_n_a_pas_le_lien(self):
        """La copie d'essai de la phase 2 (adresse en `email_to`, personne nommée)."""
        copie = self._avis(self.ana, email_to="coordination@essai.example", recipient_ids=[],
                           model=False, res_id=False)
        [corps] = self._corps_envoye(copie)
        self._sans_paragraphe(corps)

    def test_plusieurs_destinataires_chacun_son_droit(self):
        mail = self._avis(self.ana, recipient_ids=[Command.set((self.ana | self.carl).ids)])
        [pour_ana] = self._corps_envoye(mail, self.ana)
        [pour_carl] = self._corps_envoye(mail, self.carl)
        self.assertIn(self.client_a.hosting_status_token, pour_ana)
        self._sans_paragraphe(pour_carl)

    def test_le_lien_d_un_autre_client_perd_son_jeton(self):
        """Un lien d'état collé hors du paragraphe de l'avis, vers un client qui n'est pas
        celui du destinataire : le jeton ne part pas."""
        lien_b = self.client_b._hosting_status_url()
        mail = self.env["mail.mail"].create({
            "subject": "Lien collé", "body_html": "<p>Voyez <a href='%s'>ici</a> et %s</p>" % (lien_b, lien_b),
            "recipient_ids": [Command.link(self.ana.id)]})
        [corps] = self._corps_envoye(mail, self.ana)
        self.assertNotIn(self.client_b.hosting_status_token, corps)
        self.assertIn("/etat/%s/jeton-masque" % self.client_b.id, corps)
        self.assertIn("Voyez", corps)

    def test_une_autre_adresse_en_etat_reste_intacte(self):
        texte = "<p>https://gouv.example/etat/12/court et /etat/3/abc</p>"
        self.assertEqual(masquer_jetons(self.env, texte), texte)
        mail = self.env["mail.mail"].create({
            "subject": "Autre", "body_html": texte, "recipient_ids": [Command.link(self.ana.id)]})
        [corps] = self._corps_envoye(mail, self.ana)
        self.assertIn("/etat/12/court", corps)
        self.assertIn("/etat/3/abc", corps)

    # ------------------------------------------------------------------
    # 3. En français, la marque du paragraphe survit à la traduction
    # ------------------------------------------------------------------

    def test_en_francais(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        self.ana.lang = "fr_CA"
        mail = self._avis(self.ana)
        self.assertIn("votre page d'état", mail.body_html)
        self.assertIn("o_hosting_status_link", mail.body_html)
        self.assertNotIn(self.client_a.hosting_status_token, mail.mail_message_id.body)
        self.client_a.action_hosting_status_unpublish()
        [corps] = self._corps_envoye(mail, self.ana)
        self.assertNotIn("page d'état", corps)
        self.assertNotIn("/etat/", corps)

    # ------------------------------------------------------------------
    # 4. L'assistant du portail ne change pas le courriel d'un destinataire nommé
    # ------------------------------------------------------------------

    def test_l_assistant_portail_passe_par_la_garde(self):
        assistant = self.env["portal.wizard"].with_user(self.contacts).create(
            {"partner_ids": [Command.set(self.ana.ids)]})
        ligne = assistant.user_ids
        ligne.email = "pirate@ailleurs.example"
        with self.assertRaises(AccessError):
            ligne.action_grant_access()
        self.assertEqual(self.ana.email, "ana@alpha-courriel.example")
        self.assertFalse(self.ana.user_ids)

    # ------------------------------------------------------------------
    # Réponse citée, copie conforme, client archivé, adresse tierce
    # ------------------------------------------------------------------

    def test_la_reponse_citee_du_client_est_masquee(self):
        """Le client répond à l'avis en le citant ; la réponse est rangée au chatter."""
        jeton = self.client_a.hosting_status_token
        citation = '<p>Merci, noté.</p><blockquote>Suivez <a href="%s">votre page</a></blockquote>' % (
            self.client_a._hosting_status_url())
        message = self.ana.message_post(body=Markup(citation), message_type="email")
        self.assertNotIn(jeton, message.body)
        self.assertIn("/etat/%s/jeton-masque" % self.client_a.id, message.body)
        message.write({"body": Markup(citation)})
        self.assertNotIn(jeton, message.body)

    def test_le_masque_repart_entier_a_qui_y_a_droit(self):
        """Un courriel rendu depuis un message masqué (notification d'un message qui cite le lien) :
        le jeton courant au destinataire nommé, le masque aux autres."""
        jeton = self.client_a.hosting_status_token
        masque = self.client_a._hosting_status_url().replace(jeton, "jeton-masque")
        mail = self.env["mail.mail"].create({
            "subject": "Notification", "body_html": '<p>La page : <a href="%s">ici</a></p>' % masque,
            "recipient_ids": [Command.set((self.ana | self.carl).ids)]})
        [pour_ana] = self._corps_envoye(mail, self.ana)
        [pour_carl] = self._corps_envoye(mail, self.carl)
        self.assertIn(self.client_a._hosting_status_url(), pour_ana)
        self.assertNotIn(jeton, pour_carl)
        self.assertIn(masque, pour_carl)

    def test_une_copie_conforme_n_a_pas_le_lien(self):
        mail = self._avis(self.ana, email_cc="cc@essai.example")
        envois = mail._prepare_outgoing_list()
        self.assertEqual(len(envois), 2)
        for e in envois:
            if e["partner_id"] == self.ana:
                self.assertIn(self.client_a.hosting_status_token, e["body"])
            else:
                self._sans_paragraphe(e["body"])

    def test_un_client_archive_perd_le_lien(self):
        rappel = self._avis(self.ana)
        self.client_a.active = False
        [corps] = self._corps_envoye(rappel, self.ana)
        self._sans_paragraphe(corps)

    def test_une_adresse_tierce_au_numero_du_client(self):
        """Même numéro que le client du destinataire, autre site : ni masqué ni « réparé »."""
        tierce = "https://site.gouv.example/etat/%s/rapport-annuel-de-gestion-2024-2025-complet" % (
            self.client_a.id)
        message = self.ana.message_post(body=Markup('<p><a href="%s">rapport</a></p>' % tierce))
        self.assertIn(tierce, message.body)
        mail = self.env["mail.mail"].create({
            "subject": "Tierce", "body_html": '<p><a href="%s">rapport</a></p>' % tierce,
            "recipient_ids": [Command.link(self.ana.id)]})
        [corps] = self._corps_envoye(mail, self.ana)
        self.assertIn(tierce, corps)
        self.assertNotIn(self.client_a.hosting_status_token, corps)

    # ------------------------------------------------------------------
    # Toutes les formes de l'adresse, numéro démesuré
    # ------------------------------------------------------------------

    def test_toutes_les_formes_de_l_adresse_sont_masquees(self):
        jeton = self.client_a.hosting_status_token
        formes = [
            "https://odoo-sans-www.example/etat/%s/%s" % (self.client_a.id, jeton),
            "http://www.autre-forme.example/etat/%s/%s" % (self.client_a.id, jeton),
            "https://eur01.safelinks.protection.outlook.com/?url=https%%3A%%2F%%2Fx.example%%2Fetat%%2F%s%%2F%s&data=1"
            % (self.client_a.id, jeton),
        ]
        for forme in formes:
            with self.subTest(forme=forme[:40]):
                message = self.ana.message_post(body=Markup('<p><a href="%s">la page</a> %s</p>' % (forme, forme)))
                self.assertNotIn(jeton, message.body)

    def test_un_jeton_neuf_est_masque_tout_de_suite(self):
        """Le cache des jetons suit une nouvelle adresse."""
        self.ana.message_post(body="avant")
        self.client_a.action_hosting_status_regenerate_token()
        jeton = self.client_a.hosting_status_token
        message = self.ana.message_post(body=Markup("<p>%s</p>" % self.client_a._hosting_status_url()))
        self.assertNotIn(jeton, message.body)

    def test_un_numero_demesure_ne_fait_rien_planter(self):
        corps = "<p>https://x.example/etat/%s/%s</p>" % ("9" * 5000, "a" * 40)
        message = self.ana.message_post(body=Markup(corps))
        self.assertTrue(message)
        mail = self.env["mail.mail"].create({
            "subject": "Démesuré", "body_html": corps, "recipient_ids": [Command.link(self.ana.id)]})
        [envoye] = self._corps_envoye(mail, self.ana)
        self.assertIn("9" * 5000, envoye)

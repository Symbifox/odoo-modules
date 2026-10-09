"""Les envois automatiques dans l'historique d'un contact.

Ce qu'on éprouve : la capture au moment exact où Odoo va supprimer le courriel,
le propriétaire selon la règle du propriétaire, les dossiers et le téléphone qui ne les
voient pas, les règles qui ne s'y appliquent pas, et le dédoublonnage dans les
deux sens (une copie déjà là, une copie qui arrive après).
"""
import importlib.util
from pathlib import Path
from unittest.mock import patch

from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestAvis(MobileApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = cls.env["res.partner"].create(
            {"name": "Client Avis", "email": "client.avis@exemple.test"})

    def _message(self, auteur=None, type_="email_outgoing", **kw):
        vals = {
            "message_type": type_, "subject": "Lien de transfert", "body": "<p>Voici le lien.</p>",
            "model": "res.partner", "res_id": self.client.id,
            "email_from": "service@exemple.test", "reply_to": "service@exemple.test",
            "message_id": "<avis-%s@test.invalid>" % self.env["mail.message"].search_count([]),
        }
        vals.update(kw)
        # `with_user` puis `sudo` : l'uid reste l'auteur (donc `create_uid`),
        # sans lui demander le droit d'écrire sur la fiche.
        Message = self.env["mail.message"]
        if auteur:
            Message = Message.with_user(auteur)
        return Message.sudo().create(vals)

    def _envoyer(self, message, email_to="client.avis@exemple.test", **kw):
        mail = self.env["mail.mail"].sudo().create({
            "mail_message_id": message.id, "email_to": email_to,
            "state": "sent", "auto_delete": True, **kw})
        mail._postprocess_sent_message(success_pids=[])
        return mail

    def _avis(self, message):
        return self.env["bf.email"].sudo().with_context(active_test=False).search(
            [("mail_message_id", "=", message.id), ("direction", "=", "notice")])

    # -- la capture --------------------------------------------------------
    def test_un_envoi_automatique_devient_un_avis_du_declencheur(self):
        message = self._message(auteur=self.owner)
        mail = self._envoyer(message)
        avis = self._avis(message)
        self.assertEqual(len(avis), 1)
        self.assertEqual(avis.user_id, self.owner)
        self.assertEqual((avis.status, avis.is_handled), ("read", True))
        self.assertEqual(avis.partner_id, self.client)
        self.assertFalse(avis.message_id_header)
        self.assertEqual(avis.thread_root_id, message.message_id)
        self.assertIn("client.avis@exemple.test", avis.participant_ids.mapped("address"))
        self.assertFalse(mail.exists(), "le courriel auto_delete part quand même")

    def test_rien_pour_un_echec_un_commentaire_ou_des_collegues(self):
        echec = self._message(auteur=self.owner)
        mail = self.env["mail.mail"].sudo().create(
            {"mail_message_id": echec.id, "email_to": "client.avis@exemple.test", "state": "exception"})
        mail._postprocess_sent_message(success_pids=[], failure_type="mail_smtp")
        self.assertFalse(self._avis(echec))
        commentaire = self._message(auteur=self.owner, type_="comment")
        self._envoyer(commentaire)
        self.assertFalse(self._avis(commentaire), "la projection du chatter s'en charge")
        interne = self._message(auteur=self.owner)
        self._envoyer(interne, email_to="stranger@test.invalid")
        self.assertFalse(self._avis(interne), "seulement des collègues")

    def test_sans_declencheur_le_responsable_puis_le_repli(self):
        bot = self.env.ref("base.user_root")
        self.client.user_id = self.stranger
        message = self._message(auteur=bot)
        self._envoyer(message)
        self.assertEqual(self._avis(message).user_id, self.stranger)
        self.client.user_id = False
        self.owner.groups_id = [(4, self.env.ref("base.group_system").id)]
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email.avis_repli_user_id", str(self.owner.id))
        autre = self._message(auteur=bot)
        self._envoyer(autre)
        self.assertEqual(self._avis(autre).user_id, self.owner)

    def test_un_repli_qui_n_est_pas_administrateur_est_ignore(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email.avis_repli_user_id", str(self.stranger.id))
        message = self._message(auteur=self.env.ref("base.user_root"))
        self._envoyer(message)
        self.assertEqual(self._avis(message).user_id, self.env.ref("base.user_admin"))

    def test_le_repli_par_defaut_est_l_administrateur_principal(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_email.avis_repli_user_id", "")
        message = self._message(auteur=self.env.ref("base.user_root"))
        self._envoyer(message)
        self.assertEqual(self._avis(message).user_id, self.env.ref("base.user_admin"))

    def test_une_infolettre_n_est_pas_un_avis(self):
        if "mailing_id" not in self.env["mail.mail"]._fields:
            self.skipTest("module d'envoi de masse absent")
        infolettre = self.env["mailing.mailing"].sudo().create({
            "subject": "Infolettre d'essai",
            "mailing_model_id": self.env.ref("base.model_res_partner").id})
        message = self._message(auteur=self.owner)
        self._envoyer(message, mailing_id=infolettre.id)
        self.assertFalse(self._avis(message))

    def test_une_panne_de_capture_n_empeche_pas_l_envoi_de_se_clore(self):
        message = self._message(auteur=self.owner)
        with patch.object(type(self.env["bf.email"]), "_bf_capter_avis",
                          side_effect=RuntimeError("panne")):
            mail = self._envoyer(message)
        self.assertFalse(mail.exists())
        self.assertFalse(self._avis(message))

    # -- jamais en double ----------------------------------------------------
    def test_une_capture_ne_double_rien(self):
        message = self._message(auteur=self.owner)
        self._envoyer(message)
        self._envoyer(message)
        self.assertEqual(len(self._avis(message)), 1)

    def test_une_copie_deja_la_et_une_copie_qui_arrive_apres(self):
        deja = self._message(auteur=self.owner)
        self.env["bf.email"].sudo().create({
            "subject": "copie IMAP", "direction": "in", "status": "new", "source": "imap",
            "date": "2026-10-01 12:00:00",
            "user_id": self.owner.id, "message_id_header": deja.message_id})
        self._envoyer(deja)
        self.assertFalse(self._avis(deja), "le Message-ID est déjà là pour cet usager")
        apres = self._message(auteur=self.owner)
        self._envoyer(apres)
        self.assertTrue(self._avis(apres))
        self.env["bf.email"].sudo().create({
            "subject": "copie IMAP tardive", "direction": "in", "status": "new", "source": "imap",
            "date": "2026-10-01 12:00:00",
            "user_id": self.owner.id, "message_id_header": apres.message_id})
        self.assertFalse(self._avis(apres), "la vraie copie remplace l'avis")

    # -- où il paraît, où il ne paraît pas ----------------------------------
    def test_ni_boite_ni_envoyes_ni_telephone_mais_dans_l_historique(self):
        message = self._message(auteur=self.owner)
        self._envoyer(message)
        avis = self._avis(message)
        Email = self.as_owner()
        for dossier in ("inbox", "unread", "to_reply", "sent"):
            ids = [m["id"] for m in Email.inbox_get_messages(folder=dossier, limit=200)["messages"]]
            self.assertNotIn(avis.id, ids, dossier)
        self.assertIn(avis.id, [m["id"] for m in Email.inbox_get_messages(folder="all", limit=200)["messages"]])
        par_contact = Email.inbox_get_messages(folder="all", search="contact:#%s" % self.client.id)
        self.assertIn(avis.id, [m["id"] for m in par_contact["messages"]])
        self.assertIn(avis, Email.search(Email._search_domain_from_query("est:avis")))
        for filtre in ("all", "handled", "inbox"):
            sql, params = Email._mobile_filter_sql(filtre)
            self.env.cr.execute("SELECT id FROM bf_email WHERE id = %s AND " + sql, [avis.id] + params)
            self.assertFalse(self.env.cr.fetchall(), filtre)

    def test_les_regles_ne_s_appliquent_pas_a_un_avis(self):
        regle = self.env["bf.email.rule"].create({
            "name": "Tout en client", "scope": "user", "user_id": self.owner.id,
            "set_category": "client",
            "condition_ids": [(0, 0, {"kind": "condition", "field_name": "subject",
                                      "operator": "contains", "value": "a"})]})
        message = self._message(auteur=self.owner)
        self._envoyer(message)
        avis = self._avis(message)
        temoin = self.inbound.sudo()
        (avis | temoin)._apply_rules(rules=regle)
        self.assertEqual(temoin.category, "client", "la règle s'applique bien ailleurs")
        self.assertEqual(avis.category, "notification")

    def test_un_avis_ne_se_signale_pas_comme_pourriel(self):
        """Son expéditeur est notre propre adresse d'envoi. Sans fiche, le garde
        « classé sur une fiche » ne le protégeait pas."""
        from odoo.exceptions import UserError
        message = self._message(auteur=self.owner, model=False, res_id=False)
        self._envoyer(message)
        avis = self._avis(message)
        self.assertTrue(avis)
        with self.assertRaises(UserError):
            avis.with_user(self.owner).action_report_spam()

    def test_l_auteur_du_message_passe_avant_celui_du_courriel(self):
        """La file d'envoi ou une tâche planifiée peut créer le courriel sous
        un autre compte interne que la personne qui a agi."""
        message = self._message(auteur=self.owner)
        mail = self.env["mail.mail"].with_user(self.stranger).sudo().create({
            "mail_message_id": message.id, "email_to": "client.avis@exemple.test",
            "state": "sent", "auto_delete": True})
        mail._postprocess_sent_message(success_pids=[])
        self.assertEqual(self._avis(message).user_id, self.owner)

    def test_un_courriel_qu_odoo_supprime_ne_laisse_que_l_enveloppe(self):
        """🔴 `bf_sign` envoie son invitation en `mail.mail`
        NU (son message naît et meurt avec lui), `auto_delete`, pour qu'aucun
        jeton ne soit conservé. Odoo ne garde ni le courriel ni le message."""
        mail = self.env["mail.mail"].with_user(self.owner).sudo().create({
            "subject": "Signature demandée", "email_to": "client.avis@exemple.test",
            "email_from": "service@exemple.test", "reply_to": "service@exemple.test",
            "body_html": "<p>Signer : /sign/12/jeton-secret-abc</p>",
            "auto_delete": True, "state": "sent"})
        message = mail.mail_message_id
        self.assertFalse(mail.is_notification)
        mail._postprocess_sent_message(success_pids=[])
        avis = self.env["bf.email"].sudo().with_context(active_test=False).search(
            [("direction", "=", "notice"), ("subject", "=", "Signature demandée")])
        self.assertEqual(len(avis), 1)
        self.assertTrue(avis.bf_avis_sans_corps)
        self.assertFalse(message.exists(), "Odoo a bien supprimé le message")
        avis.invalidate_recordset()
        for champ in ("body_html", "body_text", "body_preview"):
            self.assertNotIn("jeton-secret", avis[champ] or "", champ)

    def test_une_notification_au_corps_vide_ne_laisse_que_l_enveloppe(self):
        """Le message garde un corps vide,
        Odoo supprime le courriel, et le rendu de ce courriel porte un lien
        d'accès au portail."""
        message = self._message(auteur=self.owner, body="")
        mail = self.env["mail.mail"].sudo().create({
            "mail_message_id": message.id, "email_to": "client.avis@exemple.test",
            "body_html": "<p><a href='/mail/view?access_token=jeton-portail-xyz'>Voir</a></p>",
            "state": "sent", "auto_delete": True})
        self.assertTrue(mail.is_notification)
        mail._postprocess_sent_message(success_pids=[])
        avis = self._avis(message)
        self.assertTrue(avis.bf_avis_sans_corps)
        avis.invalidate_recordset()
        self.assertNotIn("jeton-portail", avis.body_html or "")

    def test_une_notification_avec_corps_garde_son_corps(self):
        """Le pendant de l'essai précédent : le message garde un corps, et
        l'avis aussi, même si Odoo supprime le courriel."""
        message = self._message(auteur=self.owner)
        self._envoyer(message)
        avis = self._avis(message)
        self.assertFalse(avis.bf_avis_sans_corps)
        self.assertIn("Voici le lien", avis.body_html or "")

    def test_un_courriel_qu_odoo_garde_garde_son_corps(self):
        message = self._message(auteur=self.owner)
        self._envoyer(message, auto_delete=False)
        avis = self._avis(message)
        self.assertFalse(avis.bf_avis_sans_corps)
        self.assertIn("Voici le lien", avis.body_html or "")

    def test_un_visiteur_range_au_repli_ne_laisse_que_l_enveloppe(self):
        """Le code d'un sondage de rendez-vous : déclenché par un visiteur, sur
        une fiche sans responsable."""
        self.client.user_id = False
        message = self._message(auteur=self.env.ref("base.public_user"))
        self._envoyer(message, auto_delete=False)
        avis = self._avis(message)
        self.assertEqual(avis.user_id, self.env.ref("base.user_admin"))
        self.assertTrue(avis.bf_avis_sans_corps)
        self.assertNotIn("Voici le lien", avis.body_html or "")

    def test_un_message_decoupe_en_plusieurs_courriels_fait_un_seul_avis(self):
        message = self._message(auteur=self.owner)
        self._envoyer(message, email_to="client.avis@exemple.test")
        self._envoyer(message, email_to="second.client@exemple.test")
        avis = self._avis(message)
        self.assertEqual(len(avis), 1)
        self.assertEqual(set(avis.participant_ids.filtered(lambda p: p.role == "to").mapped("address")),
                         {"client.avis@exemple.test", "second.client@exemple.test"})

    def test_ce_qui_n_est_jamais_un_avis(self):
        discussion = self._message(auteur=self.owner, type_="notification",
                                   subtype_id=self.env.ref("mail.mt_comment").id)
        self._envoyer(discussion)
        self.assertFalse(self._avis(discussion), "une discussion est un vrai courriel")
        compte = self._message(auteur=self.owner, type_="user_notification",
                               model="res.users", res_id=self.owner.id)
        self._envoyer(compte)
        self.assertFalse(self._avis(compte), "un courriel de compte porte un jeton")
        portail = self._message(auteur=self.owner, model="portal.wizard.user", res_id=1)
        self._envoyer(portail)
        self.assertFalse(self._avis(portail), "l'invitation au portail porte un lien d'inscription")
        visiteur = self._message(auteur=self.env.ref("base.public_user"), model=False, res_id=False)
        self._envoyer(visiteur)
        self.assertFalse(self._avis(visiteur), "sans fiche ni usager interne")

    def test_gen_ne_declenche_rien(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_gen_mail.owner_gen_email", self.stranger.login)
        self.client.user_id = self.owner
        message = self._message(auteur=self.stranger)
        self._envoyer(message)
        self.assertEqual(self._avis(message).user_id, self.owner,
                         "envoyé par Gen : au responsable de la fiche")

    def test_un_echec_partiel_est_capte(self):
        message = self._message(auteur=self.owner)
        mail = self.env["mail.mail"].sudo().create({
            "mail_message_id": message.id, "email_to": "client.avis@exemple.test",
            "state": "sent", "auto_delete": True})
        mail._postprocess_sent_message(success_pids=[], failure_type="mail_email_invalid")
        self.assertTrue(self._avis(message))

    def test_ni_traites_ni_fil_du_telephone(self):
        message = self._message(auteur=self.owner)
        self._envoyer(message)
        avis = self._avis(message)
        Email = self.as_owner()
        traites = [m["id"] for m in Email.inbox_get_messages(folder="handled", limit=200)["messages"]]
        self.assertNotIn(avis.id, traites)
        self.assertNotIn(avis, Email.search(Email._thread_domain(avis.thread_root_id)))

    # -- le passé, et le dossier « À décider » -------------------------------
    def test_le_rattrapage_ne_prend_que_ce_qui_garde_un_destinataire(self):
        garde = self._message(auteur=self.owner)
        self.env["mail.mail"].sudo().create({
            "mail_message_id": garde.id, "email_to": "client.avis@exemple.test",
            "state": "sent", "auto_delete": False})
        perdu = self._message(auteur=self.owner)
        self.env["mail.notification"].sudo().create({
            "mail_message_id": perdu.id, "res_partner_id": self.client.id,
            "notification_type": "email", "notification_status": "sent"})
        perdu.sudo().partner_ids = [(5, 0, 0)]
        self.client.sudo().email = False
        chemin = Path(__file__).parents[1] / "migrations" / "18.0.11.59.0" / "post-migrate.py"
        spec = importlib.util.spec_from_file_location("migration_11590", chemin)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.migrate(self.env.cr, "18.0.11.58.0")
        self.assertTrue(self._avis(garde))
        self.assertFalse(self._avis(perdu))

    def test_a_decider_lit_le_seuil_regle(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_email.ingest_max_age_days", "45")
        res = self.env["bf.email.held"].with_user(self.owner).held_summary()
        self.assertEqual(res["max_age_days"], 45)
        self.assertIsInstance(res["lots"], list)

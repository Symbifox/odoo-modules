"""Le composeur du téléphone, deuxième version.

Ce que la 2.43 ne savait pas faire, et que la note d'usage de l'opérateur du
2026-09-16 a retenu :

- **voir et retoucher les destinataires d'une réponse** : le serveur les
  calculait à l'envoi, l'écran disait seulement « repris du message d'origine » ;
- **l'adresse d'envoi** : tout partait de l'adresse principale, même une
  réponse à un courriel reçu sur une autre boîte, là où le poste répond depuis
  la boîte qui a reçu ;
- **le Cci**, **l'objet d'une réponse**, **l'envoi programmé**.

🔴 L'envoi programmé est la partie qui mord. Un ``mail.scheduled.message`` ne
garde de son composeur que du JSON, relu par le cron à travers une liste
blanche : la portée « destinataires saisis seulement » (S-M6) et la copie
conforme n'y étaient pas. Les essais ci-dessous font partir le programmé par
le cron lui-même et comptent les notifications et les courriels, destinataire
par destinataire.

⚠️ ``mail_notify_force_send=False`` et ``mail_defer_seconds=0`` là où l'on
compte des ``mail.mail`` : un envoi immédiat se supprime aussitôt « parti », et
``mail_post_defer`` ferait attendre trente secondes une notification qu'on
croirait alors absente. Voir ``test_mobile_notifications``.
"""
import json
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.addons.mail.tests.common import MockEmail
from odoo.tests import new_test_user, tagged

from .common import MobileApiCase


def _ms(dt):
    """Datetime naïve UTC → epoch millisecondes, comme l'app les envoie."""
    return int(dt.replace(tzinfo=__import__("pytz").UTC).timestamp() * 1000)


@tagged("post_install", "-at_install")
class TestCompositeurMobile(MobileApiCase, MockEmail):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Identity = cls.env["bf.email.identity"].sudo()
        # Les identités de la personne : l'adresse principale (défaut), et
        # celle d'une deuxième boîte, qui reçoit ses propres courriels.
        cls.account2 = cls.env["bf.email.account"].create({
            "name": "Deuxième boîte",
            "user_id": cls.owner.id,
            "host": "imap.test.invalid", "port": 993,
            "login": "boite2@test.invalid", "password": "x",
            "state": "connected",
        })
        Identity.search([("user_id", "=", cls.owner.id)]).unlink()
        cls.id_principale = Identity.create({
            "user_id": cls.owner.id, "name": "Propriétaire",
            "email": "owner@test.invalid", "verified": True,
            "is_default": True, "account_id": cls.account.id,
            "sequence": 1,
        })
        cls.id_boite2 = Identity.create({
            "user_id": cls.owner.id, "name": "Propriétaire (boîte 2)",
            "email": "boite2@test.invalid", "verified": True,
            "account_id": cls.account2.id, "sequence": 5,
            "signature_html": "<p>-- <br/>Signé depuis la boîte 2</p>",
        })
        cls.id_non_verifiee = Identity.create({
            "user_id": cls.owner.id, "name": "Pas encore vérifiée",
            "email": "attente@test.invalid", "verified": False,
            "sequence": 9,
        })
        cls.id_etrangere = Identity.create({
            "user_id": cls.stranger.id, "name": "Autre usager",
            "email": "stranger@test.invalid", "verified": True,
        })

        cls.recu_boite2 = cls.env["bf.email"].with_user(cls.owner).create(dict(
            cls._vals(
                subject="Demande reçue sur la boîte 2",
                sender='"Tremblay, Noémie" <noemie@client.test>',
                direction="in", status="new", root=False,
                body="Pouvez-vous me rappeler ?", uid="201",
            ),
            account_id=cls.account2.id,
            email_to="boite2@test.invalid, Associé <associe@client.test>",
            email_cc="owner@test.invalid, comptable@client.test",
        ))

        # Une tâche suivie par un client portail et un externe : l'endroit où
        # un envoi qui notifierait ses abonnés se verrait.
        cls.owner.write({"groups_id": [
            (4, cls.env.ref("project.group_project_manager").id)]})
        cls.portal = new_test_user(
            cls.env, login="compositeur.portail@test.invalid",
            name="Client Portail", email="client.portail@acme.test",
            groups="base.group_portal")
        cls.suiveur = cls.env["res.partner"].create({
            "name": "Suiveur Externe", "email": "suiveur@externe.test"})
        cls.projet = cls.env["project.project"].create({
            "name": "Dossier suivi", "privacy_visibility": "portal"})
        cls.tache = cls.env["project.task"].create({
            "name": "Tâche suivie par le client", "project_id": cls.projet.id})
        cls.tache.message_subscribe(
            partner_ids=(cls.portal.partner_id | cls.suiveur).ids)

    # ------------------------------------------------------------ outils
    def _dernier_id(self, table="mail_message"):
        self.env.flush_all()
        self.env.cr.execute("SELECT COALESCE(MAX(id), 0) FROM %s" % table)
        return self.env.cr.fetchone()[0]

    def _messages_depuis(self, avant, model, res_id):
        return self.env["mail.message"].sudo().search([
            ("model", "=", model), ("res_id", "=", res_id),
            ("id", ">", avant), ("message_type", "=", "comment"),
        ])

    def _partenaire(self, courriel):
        return self.env["res.partner"].sudo().search(
            [("email", "=ilike", courriel)], limit=1)

    def _courriels(self, messages):
        self.env.flush_all()
        return self.env["mail.mail"].sudo().search(
            [("mail_message_id", "in", messages.ids)])

    def _notifies(self, messages, partenaire):
        self.env.flush_all()
        return self.env["mail.notification"].sudo().search_count([
            ("mail_message_id", "in", messages.ids),
            ("res_partner_id", "=", partenaire.id),
        ])

    def _tache_courriel(self, uid):
        """Un courriel reçu, classé sur la tâche suivie."""
        return self.as_owner().create(dict(self._vals(
            subject="Soumission du fournisseur", sender="fournisseur@acme.test",
            direction="in", status="new", root=False,
            body="Voici notre prix.", uid=uid,
        ), res_model="project.task", res_id=self.tache.id))

    def _echu(self, programme):
        """Faire arriver l'heure d'un programmé.

        Par SQL : la contrainte du noyau refuse d'ÉCRIRE une date passée, ce
        qui est exactement l'état dans lequel le cron trouve un programmé.
        """
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE mail_scheduled_message SET scheduled_date = now() at time zone "
            "'UTC' - interval '1 minute' WHERE id = %s", (programme.id,))
        programme.invalidate_recordset(["scheduled_date"])

    def _silencieux(self, record):
        return record.with_context(
            mail_notify_force_send=False, mail_defer_seconds=0)

    # ============================================================ /config
    def test_config_annonce_le_composeur_et_les_seules_identites_utilisables(self):
        config = self.as_owner().get_mobile_config()
        self.assertEqual(config["compose_api"], 2)
        par_adresse = {i["email"]: i for i in config["identities"]}
        self.assertEqual(set(par_adresse),
                         {"owner@test.invalid", "boite2@test.invalid"},
                         "ni l'identité non vérifiée, ni celle d'un autre usager")
        self.assertTrue(par_adresse["owner@test.invalid"]["is_default"])
        self.assertFalse(par_adresse["boite2@test.invalid"]["is_default"])
        self.assertEqual(par_adresse["boite2@test.invalid"]["account_id"],
                         self.account2.id)
        self.assertIn("Signé depuis la boîte 2",
                      par_adresse["boite2@test.invalid"]["signature_text"])
        self.assertNotIn("<p>", par_adresse["boite2@test.invalid"]["signature_text"])

    # ================================================== réponse préparée
    def test_repondre_prepare_l_expediteur_la_boite_qui_a_recu_et_l_objet(self):
        prep = self.as_owner().browse(self.recu_boite2.id).mobile_reply_prepare("reply")
        self.assertEqual(prep["to"], [
            {"name": "Tremblay, Noémie", "email": "noemie@client.test"}])
        self.assertEqual(prep["cc"], [])
        self.assertEqual(prep["subject"], "Re: Demande reçue sur la boîte 2")
        self.assertEqual(prep["identity_id"], self.id_boite2.id)

    def test_repondre_a_tous_prepare_les_copies_sans_soi_meme(self):
        prep = self.as_owner().browse(self.recu_boite2.id).mobile_reply_prepare(
            "reply_all")
        adresses_cc = [c["email"] for c in prep["cc"]]
        self.assertEqual(adresses_cc, ["associe@client.test", "comptable@client.test"])
        self.assertNotIn("owner@test.invalid", adresses_cc)
        self.assertNotIn("boite2@test.invalid", adresses_cc,
                         "l'adresse de la boîte qui a reçu est à soi aussi")
        self.assertNotIn("noemie@client.test", adresses_cc, "déjà dans « À »")

    def test_transferer_prepare_sans_destinataire(self):
        prep = self.as_owner().browse(self.inbound.id).mobile_reply_prepare("forward")
        self.assertEqual((prep["to"], prep["cc"]), ([], []))
        self.assertTrue(prep["subject"].startswith("Fwd:"))

    def test_preparer_ne_cree_aucune_fiche_contact(self):
        """Ouvrir un composeur n'est pas envoyer : l'expéditrice inconnue ne
        doit pas devenir un ``res.partner`` parce qu'on a regardé."""
        Partner = self.env["res.partner"].sudo()
        avant = Partner.search_count([])
        self.as_owner().browse(self.recu_boite2.id).mobile_reply_prepare("reply_all")
        self.assertEqual(Partner.search_count([]), avant)

    def test_preparer_nomme_la_fiche_ou_la_reponse_sera_classee(self):
        courriel = self._tache_courriel("202")
        prep = self.as_owner().browse(courriel.id).mobile_reply_prepare("reply")
        self.assertEqual(prep["record"]["model"], "project.task")
        self.assertEqual(prep["record"]["id"], self.tache.id)

    def test_preparer_un_mode_inconnu_est_refuse(self):
        with self.assertRaises(UserError):
            self.as_owner().browse(self.inbound.id).mobile_reply_prepare("teleporter")

    # ==================================================== adresse d'envoi
    def test_une_reponse_part_de_la_boite_qui_a_recu(self):
        """🔴 Le défaut d'origine : l'app ne choisissait rien, et le composeur
        partait de l'adresse du compte Odoo. Sans ``identity_id``, un client
        plus ancien doit maintenant répondre comme le poste."""
        record = self._silencieux(self.as_owner().browse(self.recu_boite2.id))
        model, res_id = record._composer_target()
        avant = self._dernier_id()
        record.mobile_reply(mode="reply", body="Je vous rappelle demain.")
        envoi = self._messages_depuis(avant, model, res_id)
        self.assertEqual(len(envoi), 1)
        self.assertIn("boite2@test.invalid", envoi.email_from)

    def test_l_adresse_choisie_au_telephone_gagne(self):
        record = self._silencieux(self.as_owner().browse(self.recu_boite2.id))
        model, res_id = record._composer_target()
        avant = self._dernier_id()
        record.mobile_reply(mode="reply", body="Depuis l'adresse principale.",
                            identity_id=self.id_principale.id)
        envoi = self._messages_depuis(avant, model, res_id)
        self.assertIn("owner@test.invalid", envoi.email_from)

    def test_une_adresse_qui_n_est_pas_la_sienne_est_refusee_sans_rien_envoyer(self):
        record = self.as_owner().browse(self.recu_boite2.id)
        model, res_id = record._composer_target()
        for identite in (self.id_etrangere, self.id_non_verifiee):
            with self.subTest(identite=identite.email):
                avant = self._dernier_id()
                with self.assertRaises(UserError):
                    record.mobile_reply(mode="reply", body="Usurpation.",
                                        identity_id=identite.id)
                self.assertFalse(self._messages_depuis(avant, model, res_id))

    def test_un_nouveau_courriel_part_de_l_identite_par_defaut(self):
        avant = self._dernier_id()
        self._silencieux(self.as_owner()).mobile_compose(
            to=["client@acme.test"], subject="Neuf", body="Bonjour.",
            res_model="project.task", res_id=self.tache.id)
        envoi = self._messages_depuis(avant, "project.task", self.tache.id)
        self.assertIn("owner@test.invalid", envoi.email_from)

    # ============================================================ objet
    def test_l_objet_d_une_reponse_se_remplace(self):
        record = self._silencieux(self.as_owner().browse(self.recu_boite2.id))
        model, res_id = record._composer_target()
        avant = self._dernier_id()
        record.mobile_reply(mode="reply", body="Voir objet.",
                            subject="Rappel demain matin")
        envoi = self._messages_depuis(avant, model, res_id)
        self.assertEqual(envoi.subject, "Rappel demain matin")

    def test_un_objet_vide_garde_le_re(self):
        record = self._silencieux(self.as_owner().browse(self.recu_boite2.id))
        model, res_id = record._composer_target()
        avant = self._dernier_id()
        record.mobile_reply(mode="reply", body="Objet vide.", subject="   ")
        envoi = self._messages_depuis(avant, model, res_id)
        self.assertEqual(envoi.subject, "Re: Demande reçue sur la boîte 2")

    # ============================================================== Cci
    def test_le_cci_recoit_sans_paraitre_et_les_abonnes_restent_hors_du_coup(self):
        courriel = self._tache_courriel("203")
        avant = self._dernier_id()
        # ⚠️ Ni `mail.mail.email_to` ni `email_cc` ne portent jamais le Cci,
        # même quand il fuit : la fuite ne se lit qu'à la sortie SMTP, voir
        # `TestCciNeFuitPas`. Ici : le Cci est destinataire, les abonnés non.
        self._silencieux(courriel.with_user(self.owner)).mobile_reply(
            mode="reply", body="Merci, c'est noté.",
            to=["fournisseur@acme.test"], cc=["collegue.cc@acme.test"],
            bcc=["discret@acme.test"])
        envoi = self._messages_depuis(avant, "project.task", self.tache.id)
        self.assertEqual(len(envoi), 1)
        discret = self._partenaire("discret@acme.test")
        self.assertTrue(discret)
        self.assertIn(discret, envoi.recipient_bcc_ids)
        self.assertEqual(self._notifies(envoi, discret), 1,
                         "le Cci doit recevoir le courriel")
        for abonne in (self.portal.partner_id, self.suiveur):
            with self.subTest(abonne=abonne.name):
                self.assertEqual(self._notifies(envoi, abonne), 0)

    def test_le_cci_compte_dans_le_plafond(self):
        """Compté AVANT de résoudre : soixante adresses refusées ne laissent
        pas soixante fiches contact derrière elles."""
        record = self.as_owner().browse(self.inbound.id)
        Partner = self.env["res.partner"].sudo()
        avant = Partner.search_count([])
        with self.assertRaises(UserError):
            record.mobile_reply(
                mode="forward", body="Diffusion.", to=["un@test.invalid"],
                bcc=["cache%d@test.invalid" % i for i in range(60)])
        self.assertEqual(Partner.search_count([]), avant)

    def test_un_cci_deja_destinataire_n_est_pas_double(self):
        record = self._silencieux(self.as_owner().browse(self.inbound.id))
        model, res_id = record._composer_target()
        avant = self._dernier_id()
        record.mobile_reply(mode="forward", body="Une fois.",
                            to=["double@test.invalid"],
                            bcc=["double@test.invalid"])
        envoi = self._messages_depuis(avant, model, res_id)
        self.assertFalse(envoi.recipient_bcc_ids)

    # ================================================ classer sur une fiche
    def test_un_nouveau_courriel_se_classe_sur_la_fiche_choisie(self):
        avant = self._dernier_id()
        self._silencieux(self.as_owner()).mobile_compose(
            to=["fournisseur@acme.test"], subject="Question sur la tâche",
            body="Pouvez-vous confirmer ?", res_model="project.task",
            res_id=self.tache.id)
        envoi = self._messages_depuis(avant, "project.task", self.tache.id)
        self.assertEqual(len(envoi), 1)
        for abonne in (self.portal.partner_id, self.suiveur):
            with self.subTest(abonne=abonne.name):
                self.assertEqual(self._notifies(envoi, abonne), 0)

    # ===================================================== envoi programmé
    def _programmer_sur_la_tache(self, uid, **extra):
        courriel = self._tache_courriel(uid)
        quand = fields.Datetime.now() + timedelta(days=2)
        resultat = self._silencieux(courriel.with_user(self.owner)).mobile_reply(
            mode="reply", body="Réponse programmée.",
            to=["fournisseur@acme.test"], cc=["collegue.cc@acme.test"],
            bcc=["discret@acme.test"], identity_id=self.id_boite2.id,
            scheduled_ms=_ms(quand), **extra)
        return courriel, resultat, quand

    def test_programmer_ne_poste_rien_et_rend_l_envoi_programme(self):
        avant = self._dernier_id()
        courriel, resultat, quand = self._programmer_sur_la_tache("204")
        self.assertTrue(resultat["scheduled"])
        programme = self.env["mail.scheduled.message"].sudo().browse(
            resultat["scheduled_id"])
        self.assertTrue(programme.exists())
        self.assertEqual((programme.model, programme.res_id),
                         ("project.task", self.tache.id))
        self.assertLessEqual(abs((programme.scheduled_date - quand).total_seconds()), 1)
        self.assertFalse(programme.bf_is_draft)
        self.assertFalse(self._messages_depuis(avant, "project.task", self.tache.id),
                         "rien ne doit être posté avant l'heure")
        self.assertEqual(courriel.status, "new",
                         "une réponse qui n'est pas partie ne marque pas « répondu »")

    def test_programmer_fige_l_adresse_les_copies_et_la_portee(self):
        _courriel, resultat, _quand = self._programmer_sur_la_tache("205")
        programme = self.env["mail.scheduled.message"].sudo().browse(
            resultat["scheduled_id"])
        params = json.loads(programme.notification_parameters)
        self.assertIn("boite2@test.invalid", params.get("email_from", ""))
        self.assertEqual(
            set(params.get("recipient_cc_ids") or []),
            set(self._partenaire("collegue.cc@acme.test").ids))
        self.assertEqual(
            set(params.get("recipient_bcc_ids") or []),
            set(self._partenaire("discret@acme.test").ids))
        self.assertEqual(params.get("bf_notify_explicit_only"),
                         ["project.task", self.tache.id])

    def test_le_cron_poste_le_programme_aux_seuls_destinataires_avec_copies(self):
        """🔴 Le cœur de l'affaire : le programmé part par le cron, sans aucun
        contexte du composeur, et doit pourtant partir comme l'envoi immédiat
        — Cc et Cci compris, abonnés exclus, depuis l'adresse choisie."""
        _courriel, resultat, _quand = self._programmer_sur_la_tache("206")
        programme = self.env["mail.scheduled.message"].sudo().browse(
            resultat["scheduled_id"])
        self._echu(programme)
        avant = self._dernier_id()
        # Le cron force l'envoi (`mail_notify_force_send=True`) : les courriels
        # partent et s'effacent dans la foulée. On les lit donc à la passerelle
        # SMTP, là où l'en-tête Cc et le destinataire caché existent vraiment.
        with self.mock_mail_gateway():
            self.env["mail.scheduled.message"].sudo()._post_messages_cron()
        self.assertFalse(programme.exists(), "le programmé doit être consommé")
        envoi = self._messages_depuis(avant, "project.task", self.tache.id)
        self.assertEqual(len(envoi), 1)
        self.assertIn("boite2@test.invalid", envoi.email_from)
        fournisseur = self._partenaire("fournisseur@acme.test")
        collegue = self._partenaire("collegue.cc@acme.test")
        discret = self._partenaire("discret@acme.test")
        self.assertEqual(envoi.recipient_cc_ids, collegue)
        self.assertEqual(envoi.recipient_bcc_ids, discret)
        for destinataire in (fournisseur, collegue, discret):
            with self.subTest(destinataire=destinataire.email):
                self.assertEqual(self._notifies(envoi, destinataire), 1)
        for abonne in (self.portal.partner_id, self.suiveur):
            with self.subTest(abonne=abonne.name):
                self.assertEqual(self._notifies(envoi, abonne), 0)
        sortis = [m for m in self._mails if m["subject"] == envoi.subject]
        self.assertTrue(sortis, "aucun courriel n'est sorti par SMTP")

        def adresses(champ):
            """Un champ d'en-tête de tous les envois, liste ou chaîne."""
            morceaux = []
            for m in sortis:
                valeur = m.get(champ) or []
                morceaux.append(valeur if isinstance(valeur, str) else " ".join(valeur))
            return " ".join(morceaux)

        self.assertIn("collegue.cc@acme.test", adresses("email_cc"),
                      "l'en-tête Cc doit porter la copie conforme")
        self.assertNotIn("discret@acme.test", adresses("email_to"))
        self.assertNotIn("discret@acme.test", adresses("email_cc"))
        caches = [m for m in sortis
                  if "discret@acme.test" in ((m.get("headers") or {}).get("X-Odoo-Bcc") or "")]
        self.assertEqual(len(caches), 1, "le Cci reçoit un envoi à part, sans paraître")
        for m in sortis:
            with self.subTest(de=m["email_from"]):
                self.assertIn("boite2@test.invalid", m["email_from"])
        self.assertNotIn("client.portail@acme.test", adresses("email_to"))
        self.assertNotIn("suiveur@externe.test", adresses("email_to"))

    def test_le_cc_d_un_programme_du_poste_part_aussi(self):
        """Le même défaut touchait le poste : un envoi programmé avec copie
        conforme partait sans elle. Contrôle : sans le correctif, la
        copie reçoit zéro notification."""
        collegue = self.env["res.partner"].create({
            "name": "Copie du poste", "email": "copie.poste@acme.test"})
        destinataire = self.env["res.partner"].create({
            "name": "Destinataire du poste", "email": "dest.poste@acme.test"})
        self.env["mail.compose.message"].with_user(self.owner).with_context(
            default_partner_cc_ids=[(6, 0, collegue.ids)],
        ).create({
            "model": "project.task",
            "res_ids": repr([self.tache.id]),
            "composition_mode": "comment",
            "subject": "Programmé au poste",
            "body": "<p>Avec copie.</p>",
            "partner_ids": [(6, 0, destinataire.ids)],
            "partner_cc_ids": [(6, 0, collegue.ids)],
        }).action_schedule_message(
            scheduled_date=fields.Datetime.now() + timedelta(hours=3))
        programme = self.env["mail.scheduled.message"].sudo().search(
            [("subject", "=", "Programmé au poste")])
        self.assertEqual(len(programme), 1)
        self._echu(programme)
        avant = self._dernier_id()
        self.env["mail.scheduled.message"].sudo().with_context(
            mail_notify_force_send=False, mail_defer_seconds=0,
        )._post_messages_cron()
        envoi = self._messages_depuis(avant, "project.task", self.tache.id)
        self.assertEqual(len(envoi), 1)
        self.assertEqual(self._notifies(envoi, collegue), 1)

    def test_une_heure_passee_ou_absurde_est_refusee(self):
        record = self.as_owner().browse(self.inbound.id)
        maintenant = fields.Datetime.now()
        for quand in (maintenant - timedelta(hours=1),
                      maintenant + timedelta(seconds=10),
                      maintenant + timedelta(days=400)):
            with self.subTest(quand=quand):
                with self.assertRaises(UserError):
                    record.mobile_reply(mode="reply", body="X",
                                        scheduled_ms=_ms(quand))

    def test_programmer_avec_une_adresse_interdite_est_refuse(self):
        record = self.as_owner().browse(self.recu_boite2.id)
        avant = self._dernier_id("mail_scheduled_message")
        with self.assertRaises(UserError):
            record.mobile_reply(
                mode="reply", body="X", identity_id=self.id_etrangere.id,
                scheduled_ms=_ms(fields.Datetime.now() + timedelta(days=1)))
        self.assertEqual(self._dernier_id("mail_scheduled_message"), avant)

    def test_programmer_un_nouveau_courriel(self):
        quand = fields.Datetime.now() + timedelta(hours=5)
        resultat = self.as_owner().mobile_compose(
            to=["client@acme.test"], subject="Plus tard", body="Bonjour.",
            scheduled_ms=_ms(quand))
        self.assertTrue(resultat["scheduled"])
        self.assertTrue(self.env["mail.scheduled.message"].sudo().browse(
            resultat["scheduled_id"]).exists())

    # ================================================ liste des programmés
    def test_la_liste_montre_mes_programmes_seulement(self):
        _c, resultat, _q = self._programmer_sur_la_tache("207")
        brouillon = self.env["mail.scheduled.message"].with_user(self.owner).create({
            "model": "project.task", "res_id": self.tache.id,
            "subject": "Un brouillon, pas un programmé",
            "body": "<p>x</p>", "author_id": self.owner.partner_id.id,
            "scheduled_date": "2031-01-01 12:00:00", "bf_is_draft": True,
        })
        liste = self.as_owner().mobile_scheduled()
        ids = [s["id"] for s in liste["scheduled"]]
        self.assertIn(resultat["scheduled_id"], ids)
        self.assertNotIn(brouillon.id, ids)
        self.assertEqual(self.env["bf.email"].with_user(self.stranger)
                         .mobile_scheduled()["scheduled"], [])
        ligne = next(s for s in liste["scheduled"]
                     if s["id"] == resultat["scheduled_id"])
        self.assertIn("fournisseur@acme.test", ligne["to_display"])
        self.assertTrue(ligne["scheduled_ms"])

    def test_retenir_un_programme_le_remet_en_brouillon_sans_rien_perdre(self):
        _c, resultat, _q = self._programmer_sur_la_tache("208")
        programme = self.env["mail.scheduled.message"].sudo().browse(
            resultat["scheduled_id"])
        corps = programme.body
        self.as_owner().mobile_unschedule(programme.id)
        self.assertTrue(programme.bf_is_draft)
        self.assertEqual(programme.body, corps)
        self.assertGreater(programme.scheduled_date,
                           fields.Datetime.now() + timedelta(days=365))
        brouillons = [d["id"] for d in self.as_owner().mobile_drafts()["drafts"]]
        self.assertIn(programme.id, brouillons)
        self.assertNotIn(programme.id, [
            s["id"] for s in self.as_owner().mobile_scheduled()["scheduled"]])
        # Et le cron ne l'enverra plus, même à l'heure prévue au départ.
        self.env["mail.scheduled.message"].sudo()._post_messages_cron()
        self.assertTrue(programme.exists())

    def test_retenir_le_programme_d_un_autre_est_refuse(self):
        _c, resultat, _q = self._programmer_sur_la_tache("209")
        with self.assertRaises(UserError):
            self.env["bf.email"].with_user(self.stranger).mobile_unschedule(
                resultat["scheduled_id"])


    # =================================================== relecture adverse
    def test_l_avis_d_echec_d_un_programme_ne_va_qu_a_son_auteur(self):
        """🔴 L'avis « A scheduled message could not be sent » part depuis
        l'environnement du programmé : il héritait du Cc et du Cci."""
        from unittest.mock import patch
        _c, resultat, _q = self._programmer_sur_la_tache("210")
        programme = self.env["mail.scheduled.message"].sudo().browse(resultat["scheduled_id"])
        self._echu(programme)
        avant = self._dernier_id()
        Task = type(self.env["project.task"])
        with patch.object(Task, "message_post", autospec=True,
                          side_effect=UserError("fiche devenue inaccessible")):
            self.env["mail.scheduled.message"].sudo()._post_messages_cron()
        self.env.flush_all()
        avis = self.env["mail.message"].sudo().search([("id", ">", avant)])
        self.assertTrue(avis, "le noyau doit prévenir l'auteur")
        notifies = self.env["mail.notification"].sudo().search(
            [("mail_message_id", "in", avis.ids)]).res_partner_id
        for copie in ("collegue.cc@acme.test", "discret@acme.test", "fournisseur@acme.test"):
            with self.subTest(copie=copie):
                self.assertNotIn(self._partenaire(copie), notifies)
        self.assertFalse(avis.recipient_cc_ids | avis.recipient_bcc_ids)

    def test_envoyer_maintenant_un_programme_garde_la_copie_conforme(self):
        """« Envoyer maintenant » sans envoi forcé : `mail_post_defer` reportait
        la notification, le cron la rejouait sans le contexte, et la copie
        conforme disparaissait. Pas de `mail_defer_seconds` ici, exprès."""
        _c, resultat, _q = self._programmer_sur_la_tache("211")
        programme = self.env["mail.scheduled.message"].sudo().browse(resultat["scheduled_id"])
        avant = self._dernier_id()
        programme.with_user(self.owner).post_message()
        envoi = self._messages_depuis(avant, "project.task", self.tache.id)
        self.assertEqual(len(envoi), 1)
        self.assertEqual(self._notifies(envoi, self._partenaire("collegue.cc@acme.test")), 1)
        self.assertEqual(self._notifies(envoi, self._partenaire("discret@acme.test")), 1)

    def test_la_citation_signe_de_l_adresse_choisie(self):
        """En mode « brouillon », la citation ouvre sur le bloc signature, dont
        le marqueur empêche le rendu d'en poser une autre : il doit être celui
        de l'adresse choisie, pas celui de la boîte qui a reçu."""
        record = self._silencieux(self.as_owner().browse(self.recu_boite2.id))
        self.assertFalse(record.mail_message_id, "prémisse : une rangée orpheline")
        model, res_id = record._composer_target()
        avant = self._dernier_id()
        record.mobile_reply(mode="forward", body="Pour info.",
                            to=["collegue@test.invalid"],
                            identity_id=self.id_principale.id)
        envoi = self._messages_depuis(avant, model, res_id)
        self.assertNotIn("Signé depuis la boîte 2", envoi.body)
        self.assertIn("owner@test.invalid", envoi.email_from)

    def test_un_programme_rejoue_apres_son_heure_se_lit_doublon(self):
        """La première requête est passée, sa réponse s'est perdue, et la file
        du téléphone la rejoue après l'heure prévue. Le jeton doit parler
        AVANT l'heure : « heure passée » ferait remettre le message en
        brouillon, on le renverrait, et il partirait deux fois."""
        record = self.as_owner().browse(self.inbound.id)
        quand = fields.Datetime.now() + timedelta(hours=2)
        record.mobile_reply(mode="reply", body="Programmé.", client_token="jeton-rejoue",
                            scheduled_ms=_ms(quand))
        passe = fields.Datetime.now() - timedelta(hours=1)
        rejeu = record.mobile_reply(mode="reply", body="Programmé.",
                                    client_token="jeton-rejoue", scheduled_ms=_ms(passe))
        self.assertTrue(rejeu.get("duplicate"))

    def test_les_pieces_d_un_programme_attendent_sur_le_programme(self):
        """Comme au poste : elles n'apparaissent sur la fiche qu'à l'envoi."""
        courriel = self._tache_courriel("212")
        piece = self.env["bf.email"].with_user(self.owner).mobile_stage_upload(
            self.device, filename="devis.txt", content=b"prix", mimetype="text/plain")
        att_id = piece["attachment_id"]
        quand = fields.Datetime.now() + timedelta(days=1)
        resultat = self._silencieux(courriel.with_user(self.owner)).mobile_reply(
            mode="reply", body="Avec pièce.", to=["fournisseur@acme.test"],
            device=self.device, attachment_ids=[att_id], scheduled_ms=_ms(quand))
        att = self.env["ir.attachment"].sudo().browse(att_id)
        self.assertEqual((att.res_model, att.res_id),
                         ("mail.scheduled.message", resultat["scheduled_id"]))
        programme = self.env["mail.scheduled.message"].sudo().browse(resultat["scheduled_id"])
        self._echu(programme)
        with self.mock_mail_gateway():
            self.env["mail.scheduled.message"].sudo()._post_messages_cron()
        self.assertEqual((att.res_model, att.res_id), ("project.task", self.tache.id))


@tagged("post_install", "-at_install")
class TestCciNeFuitPas(MobileApiCase, MockEmail):
    """🔴 L'adresse en Cci ne se lit que dans la copie du destinataire caché.

    Lu à la sortie SMTP simulée (``self.emails``) : pour chaque copie, le
    destinataire de l'enveloppe et la source brute. C'est le seul endroit où
    la fuite existait — ni le chatter, ni ``mail.mail``, ni ``build_email``
    ne la montrent, l'en-tête y est posé après coup sur un dictionnaire commun.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Partner = cls.env["res.partner"]
        cls.alice = Partner.create({"name": "Alice Dest", "email": "alice@dest.test"})
        cls.bruno = Partner.create({"name": "Bruno Dest", "email": "bruno@dest.test"})
        cls.carole = Partner.create({"name": "Carole Copie", "email": "carole@copie.test"})
        cls.denis = Partner.create({"name": "Denis Cache", "email": "denis@cache.test"})
        cls.fiche = Partner.create({"name": "Fiche d'accueil", "email": "fiche@acme.test"})

    def _copies(self, sujet):
        """(destinataire SMTP, message analysé) de chaque copie sortie."""
        import email as email_lib
        from email.header import decode_header, make_header
        copies = []
        for sortie in self.emails:
            message = email_lib.message_from_string(sortie["message"])
            objet = str(make_header(decode_header(message["Subject"] or "")))
            if sujet not in objet:
                continue
            destinataires = sortie["smtp_to_list"]
            self.assertEqual(len(destinataires), 1,
                             "une copie par destinataire : %s" % destinataires)
            copies.append((destinataires[0].lower(), message))
        return copies

    def _verifier(self, sujet):
        copies = self._copies(sujet)
        livres = sorted(d for d, _m in copies)
        self.assertEqual(livres, sorted([
            "alice@dest.test", "bruno@dest.test", "carole@copie.test",
            "denis@cache.test"]), "chaque destinataire reçoit exactement une copie")
        for destinataire, message in copies:
            with self.subTest(destinataire=destinataire):
                source = message.as_string()
                if destinataire == "denis@cache.test":
                    self.assertIn("denis@cache.test", message.get("X-Odoo-Bcc", ""))
                else:
                    self.assertNotIn("denis@cache.test", source,
                                     "l'adresse cachée ne doit figurer nulle part")
                    self.assertIsNone(message.get("X-Odoo-Bcc"))
                self.assertIn("alice@dest.test", message["To"])
                self.assertIn("carole@copie.test", message["Cc"])

    def test_le_composeur_du_poste_ne_montre_pas_le_cci(self):
        with self.mock_mail_gateway():
            self.env["mail.compose.message"].with_context(
                default_partner_cc_ids=[(6, 0, self.carole.ids)],
                default_partner_bcc_ids=[(6, 0, self.denis.ids)],
                mail_create_nosubscribe=True,
            ).create({
                "model": "res.partner",
                "res_ids": repr([self.fiche.id]),
                "composition_mode": "comment",
                "subject": "Cci du poste",
                "body": "<p>Corps.</p>",
                "partner_ids": [(6, 0, (self.alice | self.bruno).ids)],
                "partner_cc_ids": [(6, 0, self.carole.ids)],
                "partner_bcc_ids": [(6, 0, self.denis.ids)],
            })._action_send_mail()
        self._verifier("Cci du poste")

    def test_une_majuscule_dans_une_adresse_ne_decale_pas_les_copies(self):
        """🔴 Relecture adverse du 2026-09-16 : `Alice@Dest.test` en « À ».

        Le noyau ne garde, par copie, que les adresses d'en-tête présentes
        dans `send_validated_to`, en minuscules. En-têtes écrits avec
        l'adresse brute, la copie d'Alice était refusée avant de dépiler la
        file de l'OCA, et chaque copie suivante partait chez le destinataire
        de la précédente — celle du Cci chez Alice, adresse cachée comprise.
        """
        self.alice.email = "Alice@Dest.test"
        self.carole.email = "Carole@COPIE.test"
        with self.mock_mail_gateway():
            self.env["mail.compose.message"].with_context(
                default_partner_cc_ids=[(6, 0, self.carole.ids)],
                default_partner_bcc_ids=[(6, 0, self.denis.ids)],
                mail_create_nosubscribe=True,
            ).create({
                "model": "res.partner",
                "res_ids": repr([self.fiche.id]),
                "composition_mode": "comment",
                "subject": "Cci et majuscules",
                "body": "<p>Corps.</p>",
                "partner_ids": [(6, 0, (self.alice | self.bruno).ids)],
                "partner_cc_ids": [(6, 0, self.carole.ids)],
                "partner_bcc_ids": [(6, 0, self.denis.ids)],
            })._action_send_mail()
        self._verifier("Cci et majuscules")

    def test_le_telephone_ne_montre_pas_le_cci(self):
        with self.mock_mail_gateway():
            self.as_owner().browse(self.inbound.id).mobile_reply(
                mode="forward", body="Transfert avec copie cachée.",
                subject="Cci du téléphone",
                to=["alice@dest.test", "bruno@dest.test"],
                cc=["carole@copie.test"], bcc=["denis@cache.test"])
        self._verifier("Cci du téléphone")

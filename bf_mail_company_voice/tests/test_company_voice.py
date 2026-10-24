"""« Public user » ne signe plus rien : l'entreprise parle à sa place.

Ce qu'une page publique fait écrire (signature, sondage de date, réservation,
rapport de sauvegarde) part sous le contact de l'entreprise. Les essais rejouent
la forme réelle des contrôleurs publics : l'usager public, en sudo.
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestMailPublicAuthor(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.public_user = cls.env.ref("base.public_user")
        cls.public_partner = cls.env.ref("base.public_partner")
        cls.company = cls.env.company
        cls.company.partner_id.email = "bonjour-essai@example.com"
        cls.record = cls.env["res.partner"].create({
            "name": "Fiche essai", "email": "fiche@example.com",
            "company_id": cls.company.id,
        })

    def _public(self, record):
        """Comme un contrôleur public : l'usager public, en sudo."""
        return record.with_user(self.public_user).sudo()

    def test_message_post_public_credite_l_entreprise(self):
        msg = self._public(self.record).message_post(body="Document signé.")
        self.assertEqual(msg.author_id, self.company.partner_id)
        self.assertIn("bonjour-essai@example.com", msg.email_from)
        self.assertIn(self.company.partner_id.name, msg.email_from)

    def test_commentaire_d_un_visiteur_garde_public_user(self):
        # Texte libre posté par un lien de portail, sans contact :
        # jamais sous le nom de l'entreprise.
        followers_avant = self.record.message_partner_ids
        msg = self._public(self.record).message_post(
            body="Texte d'un inconnu.", message_type="comment",
            subtype_xmlid="mail.mt_comment")
        self.assertEqual(msg.author_id, self.public_partner)
        self.assertEqual(self.record.message_partner_ids, followers_avant)

    def test_auteur_public_explicite_remplace(self):
        # La note de création d'Odoo passe l'auteur explicitement.
        msg = self.record.message_post(body="Créé.", author_id=self.public_partner.id)
        self.assertEqual(msg.author_id, self.company.partner_id)
        # Posté par root (inactif) : l'auteur aurait été abonné.
        self.assertNotIn(self.company.partner_id, self.record.message_partner_ids)

    def test_mail_mail_cree_avec_auteur_public(self):
        mail = self.env["mail.mail"].sudo().create({
            "subject": "Avis", "body_html": "<p>Avis</p>", "email_to": "x@example.com",
            "author_id": self.public_partner.id,
            "model": "res.partner", "res_id": self.record.id,
        })
        # L'expéditeur, lui, vient des valeurs par défaut de mail.mail (l'usager
        # de la session) : les gabarits le fixent toujours.
        self.assertEqual(mail.author_id, self.company.partner_id)

    def test_gabarit_envoye_depuis_une_page_publique(self):
        template = self.env["mail.template"].create({
            "name": "Essai voix",
            "model_id": self.env.ref("base.model_res_partner").id,
            "subject": "Document signé",
            "email_from": "{{ object.company_id.email_formatted }}",
            "email_to": "{{ object.email }}",
            "body_html": "<p>Signé</p>",
        })
        mail_id = template.with_user(self.public_user).sudo().send_mail(self.record.id)
        mail = self.env["mail.mail"].sudo().browse(mail_id)
        self.assertEqual(mail.author_id, self.company.partner_id)

    def test_note_de_suivi_publique(self):
        msg = self._public(self.record)._message_log(body="Champ modifié.")
        self.assertEqual(msg.author_id, self.company.partner_id)

    def test_interne_garde_son_nom(self):
        user = self.env["res.users"].create({
            "name": "Interne Essai", "login": "interne_essai",
            "email": "interne@example.com",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("base.group_partner_manager").id])],
        })
        msg = self.record.with_user(user).message_post(body="Note.")
        self.assertEqual(msg.author_id, user.partner_id)

    def test_visiteur_qui_donne_son_courriel_garde_son_courriel(self):
        msg = self._public(self.record).message_post(
            body="Question.", email_from="visiteur@example.com")
        self.assertNotEqual(msg.author_id, self.company.partner_id)
        self.assertIn("visiteur@example.com", msg.email_from)

    def test_reglage_session_ignore_la_societe_de_la_fiche(self):
        # Réglage « session » : la société de la session parle toujours.
        self.env["ir.config_parameter"].sudo().set_param("bf_mail_company_voice.company_voice", "session")
        autre = self.env["res.company"].create({"name": "Seconde société essai"})
        fiche = self.env["res.partner"].create({"name": "Fiche seconde", "company_id": autre.id})
        msg = self._public(fiche).message_post(body="Signé.")
        self.assertEqual(msg.author_id, self.public_user.company_id.partner_id)
        self.assertNotEqual(msg.author_id, autre.partner_id)

    def test_entreprise_de_la_fiche_plutot_que_celle_de_la_session(self):
        autre = self.env["res.company"].create({"name": "Entreprise cliente essai"})
        autre.partner_id.email = "client@example.com"
        fiche = self.env["res.partner"].create({"name": "Fiche cliente", "company_id": autre.id})
        msg = self._public(fiche).message_post(body="Signé.")
        self.assertEqual(msg.author_id, autre.partner_id)


@tagged("post_install", "-at_install")
class TestMailCompanySender(TransactionCase):
    """L'adresse de l'entreprise part sous le nom de l'entreprise."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({"name": "Entreprise Essai", "email": "bonjour-essai@example.com"})
        cls.root = cls.env.ref("base.partner_root")
        cls.root.name = "Assistant"  # le contact root renommé reste un automatisme
        cls.interne = cls.env["res.users"].create({
            "name": "Personne Interne", "login": "personne_essai",
            "email": "personne@example.com",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })

    def _mail(self, email_from, author):
        return self.env["mail.mail"].sudo().create({
            "subject": "Avis", "body_html": "<p>Avis</p>", "email_to": "x@example.com",
            "email_from": email_from, "author_id": author.id,
        })

    def test_adresse_nue_prend_le_nom_de_l_entreprise(self):
        # État des banques d'heures, « Votre journée », transferts : company.email seul.
        mail = self._mail("bonjour-essai@example.com", self.interne.partner_id)
        self.assertEqual(mail.email_from, '"Entreprise Essai" <bonjour-essai@example.com>')

    def test_gen_sur_l_adresse_de_l_entreprise_devient_l_entreprise(self):
        mail = self._mail('"Assistant" <bonjour-essai@example.com>', self.root)
        self.assertEqual(mail.email_from, '"Entreprise Essai" <bonjour-essai@example.com>')

    def test_personne_qui_signe_de_son_nom_le_garde(self):
        mail = self._mail('"Personne Interne" <bonjour-essai@example.com>', self.interne.partner_id)
        self.assertEqual(mail.email_from, '"Personne Interne" <bonjour-essai@example.com>')

    def test_odoobot_devient_l_entreprise(self):
        mail = self._mail('"OdooBot" <bonjour-essai@example.com>', self.root)
        self.assertEqual(mail.email_from, '"Entreprise Essai" <bonjour-essai@example.com>')

    def test_infolettre_envoyee_par_le_cron_garde_le_nom_choisi(self):
        # mass_mailing envoie sous root ; l'expéditeur est celui du publipostage
        #.
        mail = self._mail('"Personne Interne" <bonjour-essai@example.com>', self.root)
        self.assertEqual(mail.email_from, '"Personne Interne" <bonjour-essai@example.com>')

    def test_signature_d_equipe_gardee(self):
        # bf_helpdesk : « Société — Équipe » voulu, accusé envoyé par le cron (F2).
        nom = '"Entreprise Essai — Client — Hypercare" <bonjour-essai@example.com>'
        mail = self._mail(nom, self.root)
        self.assertEqual(mail.email_from, nom)

    def test_conversation_de_gen_garde_gen(self):
        mail = self._mail('"Gen" <gen@example.com>', self.root)
        self.assertEqual(mail.email_from, '"Gen" <gen@example.com>')

    def test_note_du_cron_envoyee_part_sous_l_entreprise(self):
        # Le contact du cron porte l'adresse de l'entreprise, comme un contact root renommé qui porte cette adresse.
        self.root.email = '"soumission" <bonjour-essai@example.com>'
        fiche = self.env["res.partner"].create({"name": "Client essai", "email": "c@example.com"})
        msg = fiche.with_user(self.env.ref("base.user_root")).message_post(
            body="Tâche débloquée.", partner_ids=fiche.ids)
        self.assertEqual(msg.author_id, self.root)
        self.assertEqual(msg.email_from, '"Entreprise Essai" <bonjour-essai@example.com>')

    def test_adresse_etrangere_intouchee(self):
        mail = self._mail("quelquun@ailleurs.example", self.root)
        self.assertEqual(mail.email_from, "quelquun@ailleurs.example")


@tagged("post_install", "-at_install")
class TestActivityAssignedByPublicPage(TransactionCase):
    """« Public user vient de vous attribuer l'activité » → l'entreprise."""

    def test_activite_creee_par_une_page_publique(self):
        company = self.env.company
        company.name = "Entreprise Activite"
        user = self.env["res.users"].create({
            "name": "Responsable Essai", "login": "resp_essai", "email": "resp@example.com",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("base.group_partner_manager").id])],
        })
        fiche = self.env["res.partner"].create({"name": "Fiche activite"})
        public = self.env.ref("base.public_user")
        fiche.with_user(public).sudo().activity_schedule(
            "mail.mail_activity_data_todo", user_id=user.id, summary="Rappeler")
        msg = self.env["mail.message"].sudo().search(
            [("model", "=", "res.partner"), ("res_id", "=", fiche.id),
             ("message_type", "=", "user_notification")], limit=1)
        self.assertTrue(msg, "aucun avis d'activité")
        self.assertIn("Entreprise Activite", msg.body)
        self.assertNotIn("Public user", msg.body)
        # La phrase qui suit le nom reste (un xpath « replace » la perdait).
        self.assertTrue("assigned you" in msg.body or "attribuer" in msg.body, msg.body)
        self.assertEqual(msg.author_id, company.partner_id)

    def test_activite_creee_par_un_interne_garde_son_nom_et_sa_phrase(self):
        user = self.env["res.users"].create({
            "name": "Responsable Deux", "login": "resp_essai_b", "email": "resp2@example.com",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("base.group_partner_manager").id])],
        })
        createur = self.env["res.users"].create({
            "name": "Createur Interne", "login": "crea_essai", "email": "crea@example.com",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("base.group_partner_manager").id])],
        })
        fiche = self.env["res.partner"].create({"name": "Fiche activite deux"})
        fiche.with_user(createur).activity_schedule(
            "mail.mail_activity_data_todo", user_id=user.id, summary="Relancer")
        msg = self.env["mail.message"].sudo().search(
            [("model", "=", "res.partner"), ("res_id", "=", fiche.id),
             ("message_type", "=", "user_notification")], limit=1)
        self.assertIn("Createur Interne", msg.body)
        self.assertTrue("assigned you" in msg.body or "attribuer" in msg.body, msg.body)


@tagged("post_install", "-at_install")
class TestMailCompanySenderGardes(TransactionCase):
    """Ce que la règle d'expéditeur ne touche pas."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({"name": "Entreprise Garde", "email": "garde@example.com"})
        cls.root = cls.env.ref("base.partner_root")

    def test_courriel_entrant_intouche(self):
        fiche = self.env["res.partner"].create({"name": "Fiche entrant"})
        msg = self.env["mail.message"].create({
            "model": "res.partner", "res_id": fiche.id, "message_type": "email",
            "email_from": '"Personne" <garde@example.com>', "author_id": self.company.partner_id.id,
            "body": "<p>Entrant</p>",
        })
        self.assertEqual(msg.email_from, '"Personne" <garde@example.com>')

    def test_adresse_partagee_sans_fiche_prend_la_societe_de_session(self):
        autre = self.env["res.company"].create({"name": "Soeur", "email": "partagee@example.com"})
        self.company.email = "partagee@example.com"
        tierce = self.env["res.company"].create({"name": "Tierce", "email": "partagee@example.com"})
        mail = self.env["mail.mail"].sudo().with_company(self.env.ref("base.main_company")).create({
            "subject": "Avis", "body_html": "<p>x</p>", "email_to": "x@example.com",
            "email_from": "partagee@example.com", "author_id": self.root.id,
        })
        # La société de la session tranche quand la fiche ne dit rien.
        self.assertEqual(mail.email_from, '"%s" <partagee@example.com>' % self.env.company.name)
        self.assertTrue(autre and tierce)

    def test_adresse_partagee_prend_la_societe_de_la_fiche(self):
        self.company.email = "partagee2@example.com"
        soeur = self.env["res.company"].create({"name": "Soeur Deux", "email": "partagee2@example.com"})
        fiche = self.env["res.partner"].create({"name": "Fiche soeur", "company_id": soeur.id})
        mail = self.env["mail.mail"].sudo().create({
            "subject": "Avis", "body_html": "<p>x</p>", "email_to": "x@example.com",
            "email_from": "partagee2@example.com", "author_id": self.root.id,
            "model": "res.partner", "res_id": fiche.id,
        })
        self.assertEqual(mail.email_from, '"Soeur Deux" <partagee2@example.com>')

    def test_canal_discuss_intouche(self):
        canal = self.env["discuss.channel"].create({"name": "Flux essai"})
        msg = canal.message_post(body="Nouvelle.", email_from='"Gen" <garde@example.com>',
                                 author_id=self.root.id, message_type="comment")
        self.assertEqual(msg.email_from, '"Gen" <garde@example.com>')


@tagged("post_install", "-at_install")
class TestVoixSocieteFixe(TransactionCase):
    """Paramètre = id d'une société : elle seule parle, quelle que soit la société
    de la fiche, de l'usager public ou du cron (base dont l'usager public appartient
    à une seconde société qui ne doit pas paraître)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.seconde = cls.env["res.company"].create(
            {"name": "Seconde Societe Fixe", "email": "partage-fixe@example.com"})
        cls.voix = cls.env["res.company"].create(
            {"name": "Societe Qui Parle", "email": "partage-fixe@example.com"})
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_mail_company_voice.company_voice", str(cls.voix.id))
        cls.public = cls.env.ref("base.public_user")
        cls.fiche = cls.env["res.partner"].create(
            {"name": "Fiche seconde societe", "company_id": cls.seconde.id})

    def test_auteur_est_la_societe_fixe(self):
        msg = self.fiche.with_user(self.public).sudo().message_post(body="Signé.")
        self.assertEqual(msg.author_id, self.voix.partner_id)

    def test_adresse_partagee_prend_la_societe_fixe(self):
        mail = self.env["mail.mail"].sudo().create({
            "subject": "Avis", "body_html": "<p>x</p>", "email_to": "x@example.com",
            "email_from": "partage-fixe@example.com",
            "author_id": self.env.ref("base.partner_root").id,
            "model": "res.partner", "res_id": self.fiche.id})
        self.assertEqual(mail.email_from, '"Societe Qui Parle" <partage-fixe@example.com>')

    def test_avis_d_activite_nomme_la_societe_fixe(self):
        user = self.env["res.users"].create({
            "name": "Responsable Fixe", "login": "resp_fixe", "email": "resp-fixe@example.com",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("base.group_partner_manager").id])]})
        self.fiche.with_user(self.public).sudo().activity_schedule(
            "mail.mail_activity_data_todo", user_id=user.id, summary="Rappeler")
        msg = self.env["mail.message"].sudo().search(
            [("model", "=", "res.partner"), ("res_id", "=", self.fiche.id),
             ("message_type", "=", "user_notification")], limit=1)
        self.assertIn("Societe Qui Parle", msg.body)
        self.assertNotIn("Seconde Societe Fixe", msg.body)

    def test_id_inconnu_retombe_sur_la_fiche(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_mail_company_voice.company_voice", "999999")
        msg = self.fiche.with_user(self.public).sudo().message_post(body="Signé.")
        self.assertEqual(msg.author_id, self.seconde.partner_id)

"""Les abonnés de la fiche dans le composeur DU POSTE.

Écart relevé au constat S-M6 de l'audit Symbifox Mobile du
2026-09-08 : au téléphone, depuis le lot 18.0.11.36.0, répondre n'écrit qu'aux
destinataires saisis ; au poste, le composeur écrivait aussi aux abonnés de la
fiche, portail compris.

Mesuré sur une base réelle, sur trois mois, avant de trancher : 149
messages sur 68 fiches ont envoyé une copie à un abonné non saisi, 144 avis à
35 personnes hors de l'organisation. Et ce n'est pas qu'une copie de
plus : le composeur regroupe tout le monde dans UN courriel, chaque copie sort
avec le même « À », donc le fournisseur y lit l'adresse du client.

Arbitrage de l'opérateur au questionnaire du 2026-09-18 : les abonnés restent
**cochés**, rien ne change pour qui n'y touche pas, mais ils sont montrés en
pastilles retirables, et ce qu'on retire ne part pas.

⚠️ C'est une liste NOIRE, pas une liste blanche. ``test_abonne_arrive_apres_...``
est l'essai qui le prouve : quelqu'un qui s'abonne ENTRE l'ouverture du
composeur et l'envoi reçoit quand même. Une liste blanche le couperait, et un
destinataire perdu s'écrit exactement comme un envoi réussi.

⚠️ ``mail_defer_seconds=0`` partout : là où ``mail_post_defer`` est installé,
une notification sans envoi forcé attend trente secondes et n'existe pas encore
au moment de compter. Un zéro y passerait pour la garde.
"""
from odoo import fields
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestPosteAbonnes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.auteur = new_test_user(
            cls.env, login="poste.auteur@test.invalid", name="Interne du poste",
            email="auteur@bf.test", groups="base.group_user,project.group_project_manager")
        cls.portail = new_test_user(
            cls.env, login="poste.portail@test.invalid", name="Client Portail",
            email="portail@client.test", groups="base.group_portal")
        cls.externe = cls.env["res.partner"].create({
            "name": "Client Externe", "email": "externe@client.test"})
        cls.fournisseur = cls.env["res.partner"].create({
            "name": "Fournisseur", "email": "fournisseur@tiers.test"})
        cls.projet = cls.env["project.project"].create({
            "name": "Dossier", "privacy_visibility": "portal"})
        cls.tache = cls.env["project.task"].create({
            "name": "Tâche suivie", "project_id": cls.projet.id})
        cls.tache.message_subscribe(
            partner_ids=(cls.portail.partner_id | cls.externe).ids)

    # ------------------------------------------------------------------ outils
    def _composeur(self, sujet, destinataires=None, **valeurs):
        """Le composeur tel que « Répondre » l'ouvre : par le CONTEXTE.

        ⚠️ Les défauts se calculent au ``create`` à partir du contexte, pas des
        valeurs : un essai qui poserait ``model`` / ``res_ids`` en valeurs
        seulement verrait ``bf_abonnes_ids`` vide et prouverait le contraire de
        ce qu'il croit.
        """
        destinataires = destinataires or self.fournisseur
        return self.env["mail.compose.message"].with_user(self.auteur).with_context(
            default_model="project.task",
            default_res_ids=[self.tache.id],
            default_composition_mode="comment",
            default_partner_ids=[(6, 0, destinataires.ids)],
            default_subject=sujet,
            force_email=True,
            mail_create_nosubscribe=True,
            mail_notify_force_send=False,
            mail_defer_seconds=0,
        ).create({"body": "<p>Corps.</p>", **valeurs})

    def _dernier_message(self):
        return self.env["mail.message"].search(
            [("model", "=", "project.task"), ("res_id", "=", self.tache.id)],
            order="id desc", limit=1)

    def _destinataires(self, message):
        return set(self.env["mail.notification"].search([
            ("mail_message_id", "=", message.id),
            ("notification_type", "=", "email"),
        ]).mapped("res_partner_id.email"))

    def _vider_la_file(self, message):
        """Jouer le report : c'est là que la garde se perd quand elle se perd."""
        self.env["mail.message.schedule"].sudo().search(
            [("mail_message_id", "=", message.id)])._send_notifications()

    # ------------------------------------------------- le pré-remplissage
    def test_prefill_les_abonnes_qui_recevraient(self):
        composeur = self._composeur("Re: Soumission")
        self.assertEqual(
            composeur.bf_abonnes_ids,
            self.portail.partner_id | self.externe,
            "les deux abonnés externes doivent être proposés, cochés")
        self.assertEqual(composeur.bf_abonnes_ids, composeur.bf_abonnes_initiaux_ids)

    def test_prefill_exclut_qui_ecrit(self):
        self.tache.message_subscribe(partner_ids=self.auteur.partner_id.ids)
        composeur = self._composeur("Re: Soumission")
        self.assertNotIn(self.auteur.partner_id, composeur.bf_abonnes_ids,
                         "on ne s'écrit pas à soi-même")

    def test_prefill_exclut_un_interne_qui_lit_ses_avis_dans_odoo(self):
        collegue = new_test_user(
            self.env, login="poste.collegue@test.invalid", name="Collègue",
            email="collegue@bf.test", groups="base.group_user")
        collegue.notification_type = "inbox"
        self.tache.message_subscribe(partner_ids=collegue.partner_id.ids)
        composeur = self._composeur("Re: Soumission")
        self.assertNotIn(collegue.partner_id, composeur.bf_abonnes_ids,
                         "un interne en « inbox » ne reçoit pas de courriel : "
                         "le montrer ici laisserait croire qu'on peut le couper")

    def test_prefill_exclut_un_abonne_sans_adresse(self):
        muet = self.env["res.partner"].create({"name": "Sans adresse"})
        self.tache.message_subscribe(partner_ids=muet.ids)
        composeur = self._composeur("Re: Soumission")
        self.assertNotIn(muet, composeur.bf_abonnes_ids)

    def test_prefill_vide_hors_dune_fiche_unique(self):
        composeur = self.env["mail.compose.message"].with_user(self.auteur).create(
            {"subject": "Sans fiche", "body": "<p>.</p>"})
        self.assertFalse(composeur.bf_abonnes_ids)
        self.assertFalse(composeur._bf_garde_abonnes())

    # ------------------------------------------------------- ce qui part
    def test_rien_retire_rien_ne_change(self):
        """La parité : l'envoi où l'on ne touche à rien part comme avant."""
        composeur = self._composeur("Re: A")
        self.assertEqual(composeur._bf_garde_abonnes(), {})
        composeur._action_send_mail()
        message = self._dernier_message()
        self._vider_la_file(message)
        self.assertEqual(
            self._destinataires(message),
            {"fournisseur@tiers.test", "portail@client.test", "externe@client.test"})

    def test_un_abonne_retire_ne_recoit_pas(self):
        composeur = self._composeur("Re: B")
        composeur.bf_abonnes_ids = [(3, self.portail.partner_id.id)]
        composeur._action_send_mail()
        message = self._dernier_message()
        self._vider_la_file(message)
        recus = self._destinataires(message)
        self.assertNotIn("portail@client.test", recus)
        self.assertEqual(recus, {"fournisseur@tiers.test", "externe@client.test"})

    def test_retire_mais_saisi_au_a_reste_destinataire(self):
        """Le geste explicite gagne sur le retrait : sans ce filet, retirer un
        abonné qu'on vient de saisir dans le « À » l'effacerait de son propre
        courriel."""
        composeur = self._composeur(
            "Re: C", destinataires=self.fournisseur | self.portail.partner_id)
        composeur.bf_abonnes_ids = [(3, self.portail.partner_id.id)]
        self.assertEqual(composeur._bf_garde_abonnes(), {})
        composeur._action_send_mail()
        message = self._dernier_message()
        self._vider_la_file(message)
        self.assertIn("portail@client.test", self._destinataires(message))

    def test_abonne_arrive_apres_louverture_recoit_quand_meme(self):
        """🔴 La raison d'être de la liste NOIRE.

        Quelqu'un s'abonne entre l'ouverture du composeur et l'envoi. Une liste
        blanche, « n'envoyer qu'aux abonnés cochés », le couperait, et rien
        ne le dirait : un destinataire perdu s'écrit comme un envoi réussi.
        """
        composeur = self._composeur("Re: D")
        composeur.bf_abonnes_ids = [(3, self.portail.partner_id.id)]
        tardif = self.env["res.partner"].create({
            "name": "Arrivé après", "email": "tardif@client.test"})
        self.tache.message_subscribe(partner_ids=tardif.ids)
        composeur._action_send_mail()
        message = self._dernier_message()
        self._vider_la_file(message)
        recus = self._destinataires(message)
        self.assertIn("tardif@client.test", recus)
        self.assertNotIn("portail@client.test", recus)

    def test_le_retrait_survit_au_report_des_notifications(self):
        """🔴 Un envoi différé recalcule ses destinataires sans le contexte.

        ``mail_post_defer`` met en file tout envoi non forcé : le cron rappelle
        ``_notify_thread`` depuis SON environnement, ``msg_vals=False``. Sans le
        voyage par les ``kwargs``, l'abonné retiré recevrait trente secondes
        plus tard ce que l'envoi venait de lui refuser.
        """
        composeur = self._composeur("Re: E")
        composeur.bf_abonnes_ids = [(3, self.portail.partner_id.id)]
        composeur._action_send_mail()
        message = self._dernier_message()
        avant = self._destinataires(message)
        self._vider_la_file(message)
        apres = self._destinataires(message)
        self.assertNotIn("portail@client.test", avant)
        self.assertNotIn("portail@client.test", apres,
                         "le report a rendu à l'abonné ce qu'on lui avait retiré")

    def test_le_retrait_survit_a_un_envoi_programme(self):
        """Programmer tue le contexte : la garde doit être ÉCRITE dans les
        paramètres de la ``mail.scheduled.message``, et relue par la liste
        blanche du module."""
        composeur = self._composeur("Re: F")
        composeur.bf_abonnes_ids = [(3, self.portail.partner_id.id)]
        composeur.action_schedule_message(scheduled_date=fields.Datetime.now())
        programme = self.env["mail.scheduled.message"].sudo().search(
            [("subject", "=", "Re: F")], limit=1)
        self.assertTrue(programme, "l'envoi devait être programmé")
        self.assertIn("bf_abonnes_retires", programme.notification_parameters)
        programme.with_context(
            mail_notify_force_send=False, mail_defer_seconds=0)._post_message()
        message = self._dernier_message()
        self._vider_la_file(message)
        self.assertNotIn("portail@client.test", self._destinataires(message))

    def test_la_garde_ne_deborde_pas_sur_une_autre_fiche(self):
        """La garde nomme la fiche visée. Un message posté ailleurs pendant
        l'envoi, une note automatique, un journal d'activité, garde ses
        destinataires."""
        autre = self.env["project.task"].create({
            "name": "Autre dossier", "project_id": self.projet.id})
        autre.message_subscribe(partner_ids=self.portail.partner_id.ids)
        garde = {"bf_abonnes_retires": [
            "project.task", self.tache.id, [self.portail.partner_id.id]]}
        message = autre.with_user(self.auteur).with_context(
            mail_notify_force_send=False, mail_defer_seconds=0,
            **garde).message_post(
                body="<p>Ailleurs.</p>", subtype_xmlid="mail.mt_comment",
                message_type="comment")
        self._vider_la_file(message)
        self.assertIn("portail@client.test", set(
            self.env["mail.notification"].search([
                ("mail_message_id", "=", message.id),
                ("notification_type", "=", "email"),
            ]).mapped("res_partner_id.email")))

    def test_la_garde_du_telephone_tient_toujours(self):
        """Non-régression : ``bf_notify_explicit_only`` et le retrait du poste
        vivent dans la MÊME méthode. Deux ``def`` du même nom, et Python garde
        la dernière sans un mot."""
        message = self.tache.with_user(self.auteur).with_context(
            mail_notify_force_send=False, mail_defer_seconds=0,
            bf_notify_explicit_only=("project.task", self.tache.id)).message_post(
                body="<p>Du téléphone.</p>", subtype_xmlid="mail.mt_comment",
                message_type="comment", partner_ids=self.fournisseur.ids)
        self._vider_la_file(message)
        recus = set(self.env["mail.notification"].search([
            ("mail_message_id", "=", message.id),
            ("notification_type", "=", "email"),
        ]).mapped("res_partner_id.email"))
        self.assertEqual(recus, {"fournisseur@tiers.test"})

    def test_composer_depuis_la_boite_montre_les_abonnes_de_la_cible(self):
        """🔴 Le composeur de la boîte s'ouvre sur le brouillon, pas sur une
        fiche : la cible est choisie APRÈS, dans « Classer dans ». Sans le
        raccord sur ``target_reference``, les pastilles restaient vides, et
        ``_bf_retarget_to_chatter`` envoyait ensuite à des abonnés que l'écran
        n'avait jamais nommés."""
        brouillon = self.env["bf.email"].with_user(self.auteur).create({
            "subject": "Nouveau courriel", "body_html": "<p>.</p>",
            "email_from": "auteur@bf.test", "email_to": "fournisseur@tiers.test",
            "date": "2026-09-18 05:00:00", "direction": "out", "source": "chatter",
        })
        composeur = self.env["mail.compose.message"].with_user(self.auteur).with_context(
            default_model="bf.email",
            default_res_ids=[brouillon.id],
            default_composition_mode="comment",
            bf_email_compose_shell_id=brouillon.id,
            mail_notify_force_send=False, mail_defer_seconds=0,
        ).create({"subject": "Nouveau", "body": "<p>Corps.</p>",
                  "partner_ids": [(6, 0, self.fournisseur.ids)]})
        self.assertFalse(composeur.bf_abonnes_ids,
                         "sur le brouillon lui-même, il n'y a personne à montrer")
        composeur.target_reference = "%s,%s" % ("project.task", self.tache.id)
        composeur._onchange_bf_abonnes_de_la_cible()
        self.assertEqual(composeur.bf_abonnes_ids,
                         self.portail.partner_id | self.externe,
                         "choisir la fiche doit faire apparaître ses abonnés")
        self.assertEqual(composeur.bf_abonnes_ids, composeur.bf_abonnes_initiaux_ids)

    def test_un_abonne_ajoute_aux_pastilles_recoit_vraiment(self):
        """🔴 Le champ est modifiable : sans versement, y déposer quelqu'un
        n'aurait AUCUN effet, la garde ne sait que retirer, et l'écran
        promettrait un destinataire que le courriel n'aurait jamais."""
        tiers = self.env["res.partner"].create({
            "name": "Ajouté à la main", "email": "ajoute@tiers.test"})
        composeur = self._composeur("Re: G")
        composeur.bf_abonnes_ids = [(4, tiers.id)]
        composeur._action_send_mail()
        message = self._dernier_message()
        self._vider_la_file(message)
        self.assertIn("ajoute@tiers.test", self._destinataires(message))
        self.assertIn(tiers, message.partner_ids,
                      "l'ajout doit être un destinataire du « À », pas un abonné fantôme")

    def test_ajouter_et_retirer_dans_le_meme_geste(self):
        """Les deux gestes cohabitent sans se marcher dessus."""
        tiers = self.env["res.partner"].create({
            "name": "Ajouté", "email": "ajoute2@tiers.test"})
        composeur = self._composeur("Re: H")
        composeur.bf_abonnes_ids = [(4, tiers.id), (3, self.portail.partner_id.id)]
        composeur._action_send_mail()
        message = self._dernier_message()
        self._vider_la_file(message)
        recus = self._destinataires(message)
        self.assertIn("ajoute2@tiers.test", recus)
        self.assertNotIn("portail@client.test", recus)
        self.assertIn("externe@client.test", recus)

    def test_la_garde_posee_a_la_main_respecte_les_destinataires_saisis(self):
        """La portée est un simple contexte : tout code peut la poser, pas
        seulement le composeur, qui, lui, purge déjà sa liste avant de
        l'écrire. Cet essai mesure le second filet POUR LUI-MÊME : un
        destinataire à la fois « retiré » et saisi dans le « À » reçoit.

        Sans lui, la mutation qui enlève ce filet survivait : aucun essai ne
        l'atteignait, et il aurait pu disparaître sans que rien ne le dise.
        """
        garde = {"bf_abonnes_retires": [
            "project.task", self.tache.id, [self.portail.partner_id.id]]}
        message = self.tache.with_user(self.auteur).with_context(
            mail_notify_force_send=False, mail_defer_seconds=0,
            **garde).message_post(
                body="<p>Posé à la main.</p>", subtype_xmlid="mail.mt_comment",
                message_type="comment",
                partner_ids=(self.fournisseur | self.portail.partner_id).ids)
        self._vider_la_file(message)
        self.assertIn("portail@client.test", self._destinataires(message),
                      "saisi dans le « À », il reçoit malgré le retrait")

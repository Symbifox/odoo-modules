"""L'avis au responsable du mandat quand le client agit sur sa page.

Ce qui compte : le geste du client n'écrit qu'un avis en file, et c'est la tâche
planifiée qui avise ; le responsable est avisé par les canaux que la société a
cochés, et par eux seuls ; rien de ce que le client a écrit ne sort par courriel
ou par notification ; le client n'est jamais abonné ni destinataire ; dix
fichiers d'une même pièce font une activité ; trois erreurs de code de suite
avisent une fois, et un code juste, le réarmement ou une nouvelle adresse
remettent la suite à zéro ; un avis qui échoue ne fait pas échouer le geste du
client, mais une erreur de concurrence remonte pour être rejouée.
"""
from unittest.mock import patch

from psycopg2.errors import SerializationFailure

from odoo import fields
from odoo.addons.mail.tests.common import MockEmail
from odoo.tests import Form, HttpCase, TransactionCase, new_test_user, tagged

from .test_otp import CODE, FAUX, JETON_CSRF, JETON_CSRF_SCRIPT, CourseSimulee
from .test_portail import _Mandat

NOM_DU_FICHIER = "releve-confidentiel-NE-PAS-DIFFUSER.pdf"
CONTENU = b"%PDF-1.4 piece du client"
JOURNAL_AVIS = "odoo.addons.bf_stepbystep_clients.models.progression_avis"
TOUS_LES_CANAUX = ("activity", "inbox", "email")


class _AvecResponsable(_Mandat):

    def _responsable(self, env, login="resp_avis", **valeurs):
        return new_test_user(
            env, login=login, name="Responsable Essai",
            email="responsable@essai.invalid",
            groups="project.group_project_user",
            **dict({"notification_type": "inbox"}, **valeurs))

    def _regler(self, societe, *canaux):
        societe.write({"progression_notify_%s" % c: c in canaux for c in TOUS_LES_CANAUX})

    def _activites(self, cible):
        self.env.invalidate_all()
        return self.env["mail.activity"].sudo().search([
            ("res_model", "=", cible._name), ("res_id", "=", cible.id),
            ("activity_type_id", "=", self.env.ref(
                "bf_stepbystep_clients.mail_activity_type_progression_client").id),
        ])

    def _avis_par_courriel(self, depuis_id=0):
        return self.env["mail.mail"].sudo().search([
            ("id", ">", depuis_id),
            ("email_to", "ilike", "responsable@essai.invalid"),
        ])

    def _dernier_courriel_id(self):
        dernier = self.env["mail.mail"].sudo().search([], order="id desc", limit=1)
        return dernier.id or 0

    def _en_file(self, projet):
        self.env.invalidate_all()
        return self.env["bf.progression.notice"].sudo().search(
            [("project_id", "=", projet.id)])

    def _traiter(self):
        """Ce que fait la tâche planifiée : l'essai éteint les crons, on l'appelle."""
        return self.env["bf.progression.notice"]._cron_envoyer()

    def _vieillir_les_avis(self, projet, minutes=10):
        """Comme le temps qui passe entre deux essais d'un avis en échec."""
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE bf_progression_notice SET write_date = write_date - %s * interval '1 minute'"
            " WHERE project_id = %s", (minutes, projet.id))
        self.env.invalidate_all()

    def _courriels_partis_au_responsable(self):
        """Les courriels réellement passés par la passerelle (MockEmail)."""
        return [m for m in self._mails if "responsable@essai.invalid" in str(m["email_to"])]


@tagged("post_install", "-at_install")
class TestAvisDuDepot(HttpCase, MockEmail, _AvecResponsable):

    def setUp(self):
        super().setUp()
        self.projet, self.colonnes = self._mandat(self.env)
        self.projet.partner_id.email = "client@exemple.invalid"
        self.responsable = self._responsable(self.env)
        self.projet.user_id = self.responsable
        self.societe = self.projet.company_id or self.responsable.company_id
        self._regler(self.societe, "activity", "email")
        self.env.ref("bf_stepbystep_clients.ir_cron_progression_notices").sudo().active = True
        self.piece = self.env["bf.progression.document"].create({
            "name": "États financiers", "stage_id": self.colonnes[1].id})
        self.projet.action_progression_portal_publish()
        self.jeton = self.projet.progression_portal_token
        self.page = "/progression/%s/%s" % (self.projet.id, self.jeton)
        texte = self.url_open(self.page).text
        trouve = JETON_CSRF.search(texte) or JETON_CSRF_SCRIPT.search(texte)
        self.csrf = trouve.group(1)
        # La tâche OUVERTE de la colonne des pièces : c'est elle qui reçoit.
        self.tache = self.projet._progression_tache_du_depot(self.colonnes[1])
        self.assertTrue(self.tache)
        self.depuis = self._dernier_courriel_id()

    def _deposer(self, nom=NOM_DU_FICHIER, contenu=CONTENU):
        reponse = self.url_open(
            "%s/deposit/%s" % (self.page, self.piece.id),
            data={"csrf_token": self.csrf},
            files={"file": (nom, contenu)}, allow_redirects=False)
        self.assertEqual(reponse.status_code, 303)
        self.assertIn("message=received", reponse.headers["Location"])
        return reponse

    def test_le_geste_du_client_ne_fait_que_mettre_en_file(self):
        """🔴 La transaction du client n'écrit aucune ligne partagée : ni
        activité, ni abonné, ni courriel. Sur le chemin S3, un conflit là
        faisait échouer un dépôt que le rejeu ne pouvait pas refaire."""
        abonnes_avant = self.tache.message_partner_ids
        self._deposer()
        self.assertFalse(self._activites(self.tache))
        self.assertFalse(self._avis_par_courriel(self.depuis))
        self.assertEqual(self.tache.message_partner_ids, abonnes_avant)
        avis = self._en_file(self.projet)
        self.assertEqual(len(avis), 1)
        self.assertEqual(avis.state, "pending")
        self.assertEqual((avis.res_model, avis.res_id), ("project.task", self.tache.id))
        self._traiter()
        self.assertEqual(avis.state, "done")
        self.assertEqual(avis.channels, "activity,email")

    def test_le_depot_avise_le_responsable_par_activite_et_courriel(self):
        self._deposer()
        self._traiter()
        activites = self._activites(self.tache)
        self.assertEqual(len(activites), 1)
        self.assertEqual(activites.user_id, self.responsable)
        self.assertIn("États financiers", activites.summary)
        # 🔴 Pas le nom du fichier, même dans l'activité : sa note est recopiée
        # au fil quand on la marque faite, et peut partir aux abonnés.
        self.assertNotIn(NOM_DU_FICHIER, str(activites.note))
        depot = self.projet.progression_deposit_ids
        self.assertIn("deposit no. %s" % depot.id, str(activites.note))
        # Et la pièce, elle, est bien sur la tâche, sous son nom.
        self.assertEqual(depot.attachment_id.res_id, self.tache.id)
        self.assertEqual(depot.attachment_id.name, NOM_DU_FICHIER)

        courriels = self._avis_par_courriel(self.depuis)
        self.assertEqual(len(courriels), 1)
        corps = str(courriels.body_html)
        self.assertIn("États financiers", corps)
        self.assertIn("/odoo/project.task/%s" % self.tache.id, corps)
        # 🔴 Ni la pièce, ni rien de ce que le client a écrit.
        self.assertNotIn(NOM_DU_FICHIER, corps)
        self.assertNotIn(NOM_DU_FICHIER, courriels.subject or "")
        self.assertFalse(courriels.attachment_ids)
        self.assertFalse(courriels.auto_delete)
        # Détaché du fil du projet.
        self.assertFalse(courriels.mail_message_id.model)
        # 🔴 Et il PART, même pour un usager réglé « dans Odoo ».
        self.assertEqual(self.responsable.notification_type, "inbox")
        courriels.send()
        self.assertEqual(courriels.state, "sent")

    def test_le_client_n_est_ni_abonne_ni_destinataire(self):
        self._regler(self.societe, *TOUS_LES_CANAUX)
        self._deposer()
        self._traiter()
        client = self.projet.partner_id
        self.assertNotIn(client, self.tache.message_partner_ids)
        self.assertNotIn(client, self.projet.message_partner_ids)
        courriels = self.env["mail.mail"].sudo().search([("id", ">", self.depuis)])
        self.assertTrue(courriels)
        self.assertNotIn(client, courriels.recipient_ids)
        for courriel in courriels:
            self.assertNotIn("client@exemple.invalid", courriel.email_to or "")
        notifies = self.env["mail.notification"].sudo().search([
            ("mail_message_id.model", "=", "project.task"),
            ("mail_message_id.res_id", "=", self.tache.id)]).res_partner_id
        self.assertTrue(notifies)
        self.assertNotIn(client, notifies)

    def test_deux_fichiers_de_la_meme_piece_font_une_seule_activite(self):
        self._deposer(nom="page-1.pdf")
        self._deposer(nom="page-2.pdf")
        self._traiter()
        activites = self._activites(self.tache)
        self.assertEqual(len(activites), 1)
        self.assertEqual(str(activites.note).count("deposit no."), 2)
        # Un courriel par geste : le responsable sait quand le second arrive.
        self.assertEqual(len(self._avis_par_courriel(self.depuis)), 2)

    def test_les_canaux_suivent_les_reglages_de_la_societe(self):
        self._regler(self.societe, "inbox")
        self._deposer()
        self._traiter()
        self.assertFalse(self._activites(self.tache))
        self.assertFalse(self._avis_par_courriel(self.depuis))
        notification = self.env["mail.notification"].sudo().search([
            ("res_partner_id", "=", self.responsable.partner_id.id),
            ("mail_message_id.model", "=", "project.task"),
            ("mail_message_id.res_id", "=", self.tache.id),
        ])
        self.assertEqual(len(notification), 1)
        self.assertEqual(notification.notification_type, "inbox")

    def test_la_notification_ne_porte_pas_le_nom_du_fichier(self):
        """🔴 Un usager réglé « par courriel » reçoit la notification PAR
        COURRIEL : elle ne doit pas citer le nom du fichier du client."""
        self.responsable.notification_type = "email"
        self._regler(self.societe, "inbox")
        self._deposer()
        with self.mock_mail_gateway():
            self._traiter()
        partis = self._courriels_partis_au_responsable()
        self.assertEqual(len(partis), 1, "la notification n'est pas partie par courriel")
        self.assertIn("États financiers", partis[0]["body"])
        # Le texte du courriel, qui nomme le mandat, et non la ligne de l'activité.
        self.assertIn(self.projet.name, partis[0]["body"])
        self.assertNotIn(NOM_DU_FICHIER, partis[0]["body"])
        self.assertNotIn(NOM_DU_FICHIER, partis[0]["subject"])
        message = self.env["mail.message"].sudo().search([
            ("model", "=", "project.task"), ("res_id", "=", self.tache.id),
            ("partner_ids", "in", self.responsable.partner_id.ids)])
        self.assertEqual(len(message), 1)
        self.assertIn("États financiers", str(message.body))
        self.assertNotIn(NOM_DU_FICHIER, str(message.body))
        self.assertNotIn(NOM_DU_FICHIER, message.subject or "")

    def test_notification_et_courriel_ne_font_pas_deux_courriels(self):
        self.responsable.notification_type = "email"
        self._regler(self.societe, *TOUS_LES_CANAUX)
        self._deposer()
        with self.mock_mail_gateway():
            self._traiter()
        self.assertEqual(self._en_file(self.projet).channels, "activity,email")
        # Aucune notification partie par courriel, et UN courriel en file.
        self.assertFalse(self._courriels_partis_au_responsable())
        self.assertEqual(len(self._avis_par_courriel(self.depuis)), 1)

    def test_aucun_canal_coche_aucun_avis(self):
        self._regler(self.societe)
        self._deposer()
        self._traiter()
        self.assertFalse(self._activites(self.tache))
        self.assertFalse(self._avis_par_courriel(self.depuis))
        self.assertEqual(len(self.projet.progression_deposit_ids), 1)

    def test_sans_responsable_le_depot_passe_sans_avis(self):
        self.projet.user_id = False
        self._deposer()
        self._traiter()
        self.assertEqual(len(self.projet.progression_deposit_ids), 1)
        self.assertFalse(self._activites(self.tache))
        self.assertFalse(self._avis_par_courriel(self.depuis))
        self.assertEqual(self._en_file(self.projet).state, "done")

    def test_un_canal_qui_echoue_laisse_partir_les_autres(self):
        with patch.object(
                type(self.env["project.project"]), "_progression_aviser_par_email",
                side_effect=RuntimeError("relais en panne")), \
                self.assertLogs(JOURNAL_AVIS, level="ERROR"):
            self._deposer()
            self._traiter()
        self.assertEqual(len(self._activites(self.tache)), 1)
        self.assertEqual(self._en_file(self.projet).channels, "activity")

    def test_un_avis_dont_tous_les_canaux_echouent_reste_en_file(self):
        self._deposer()
        Projet = type(self.env["project.project"])
        with patch.object(Projet, "_progression_aviser_par_email",
                          side_effect=RuntimeError("a")), \
                patch.object(Projet, "_progression_aviser_par_activity",
                             side_effect=RuntimeError("b")), \
                self.assertLogs(JOURNAL_AVIS, level="ERROR"):
            self._traiter()
            avis = self._en_file(self.projet)
            self.assertEqual((avis.state, avis.attempts), ("pending", 1))
            # Tout de suite : l'avis attend son délai, il n'est pas retenté.
            self._traiter()
            self.assertEqual(self._en_file(self.projet).attempts, 1)
            self._vieillir_les_avis(self.projet)
            self._traiter()
            self._vieillir_les_avis(self.projet)
            self._traiter()
        avis = self._en_file(self.projet)
        self.assertEqual((avis.state, avis.attempts), ("failed", 3))
        self.assertIn("RuntimeError", avis.error or "")
        self.assertEqual(len(self.projet.progression_deposit_ids), 1)
        # Visible et relançable depuis Progression > Configuration.
        avis.action_retry()
        self.assertEqual((avis.state, avis.attempts), ("pending", 0))
        self._traiter()
        self.assertEqual(self._en_file(self.projet).state, "done")

    def test_relancer_un_avis_exige_le_droit_d_ecrire(self):
        """Un gestionnaire de projets lit les avis, il ne les relance pas."""
        from odoo.exceptions import AccessError
        self._deposer()
        avis = self._en_file(self.projet)
        avis.sudo().write({"state": "failed", "attempts": 3})
        gestionnaire = new_test_user(
            self.env, login="gestion_avis", name="Gestion Essai",
            groups="project.group_project_manager")
        self.assertTrue(avis.with_user(gestionnaire).read(["state"]))
        with self.assertRaises(AccessError):
            avis.with_user(gestionnaire).action_retry()
        self.env.invalidate_all()
        self.assertEqual((avis.state, avis.attempts), ("failed", 3))

    def test_un_administrateur_d_une_autre_societe_ne_touche_pas_au_mandat(self):
        """Le groupe ne suffit pas : copier l'adresse, régénérer le jeton ou
        oublier les appareils d'un mandat d'une AUTRE société est refusé."""
        from odoo.exceptions import AccessError
        autre = self.env["res.company"].create({"name": "Autre société essai"})
        self.projet.company_id = self.env.company
        admin_ailleurs = new_test_user(
            self.env, login="admin_ailleurs_avis", name="Admin Ailleurs",
            company_id=autre.id, company_ids=[(6, 0, autre.ids)],
            groups="project.group_project_manager")
        projet = self.projet.with_user(admin_ailleurs)
        jeton = self.projet.progression_portal_token
        for action in ("action_progression_portal_copy_link",
                       "action_progression_portal_regenerate_token",
                       "action_progression_otp_reset"):
            with self.assertRaises(AccessError, msg=action):
                getattr(projet, action)()
        self.env.invalidate_all()
        self.assertEqual(self.projet.progression_portal_token, jeton)

    def test_un_canal_qui_n_a_rien_produit_ne_compte_pas(self):
        """Un responsable sans adresse : le courriel n'est pas « parti », et
        l'avis le dit."""
        self.responsable.partner_id.email = False
        self._deposer()
        self._traiter()
        avis = self._en_file(self.projet)
        self.assertEqual(avis.state, "done")
        self.assertEqual(avis.channels, "activity")
        self.assertIn("email", avis.error or "")

    def test_tache_planifiee_eteinte_le_depot_passe_et_l_avis_attend(self):
        self.env.ref("bf_stepbystep_clients.ir_cron_progression_notices").sudo().active = False
        with self.assertLogs(JOURNAL_AVIS, level="WARNING"):
            self._deposer()
        self.assertEqual(len(self.projet.progression_deposit_ids), 1)
        self.assertEqual(self._en_file(self.projet).state, "pending")

    def test_l_activite_porte_l_heure_du_geste(self):
        """Un avis resté en file une heure est daté de l'heure du DÉPÔT."""
        self._deposer()
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE bf_progression_notice SET create_date = %s WHERE project_id = %s",
            ("2026-01-15 14:30:00", self.projet.id))
        self.env.invalidate_all()
        self.responsable.tz = "UTC"
        self._traiter()
        self.assertIn("2026-01-15 14:30", str(self._activites(self.tache).note))

    def test_une_integrite_violee_ne_fait_pas_echouer_le_depot(self):
        """Odoo ne rejoue PAS une IntegrityError : la laisser remonter perdait
        le dépôt (sur le chemin S3, après un assemblage qui ne se refait pas)."""
        from psycopg2.errors import UniqueViolation
        with patch.object(type(self.env["bf.progression.notice"]), "create",
                          side_effect=UniqueViolation("doublon")), \
                self.assertLogs(JOURNAL_AVIS, level="ERROR"):
            self.assertFalse(self.projet._progression_aviser(
                "deposit", cible=self.tache, depot_id=0))

    def test_une_mise_en_file_qui_echoue_ne_fait_pas_echouer_le_depot(self):
        with patch.object(type(self.env["bf.progression.notice"]), "create",
                          side_effect=RuntimeError("table verrouillée")), \
                self.assertLogs(JOURNAL_AVIS, level="ERROR"):
            self._deposer()
        self.assertEqual(len(self.projet.progression_deposit_ids), 1)

    def test_une_erreur_de_concurrence_remonte_pour_etre_rejouee(self):
        with patch.object(type(self.env["bf.progression.notice"]), "create",
                          side_effect=CourseSimulee("course")), \
                self.assertRaises(SerializationFailure):
            self.projet._progression_aviser("deposit", cible=self.tache, depot_id=0)


@tagged("post_install", "-at_install")
class TestAvisDuCode(HttpCase, _AvecResponsable):

    def setUp(self):
        super().setUp()
        self.projet, self.colonnes = self._mandat(self.env)
        self.projet.partner_id.email = "client@exemple.invalid"
        self.responsable = self._responsable(self.env)
        self.projet.user_id = self.responsable
        self._regler(self.projet.company_id or self.responsable.company_id,
                     "activity", "email")
        self.env.ref("bf_stepbystep_clients.ir_cron_progression_notices").sudo().active = True
        self.projet.action_progression_portal_publish()
        texte = self.url_open("/progression/%s/%s" % (
            self.projet.id, self.projet.progression_portal_token)).text
        trouve = JETON_CSRF.search(texte) or JETON_CSRF_SCRIPT.search(texte)
        self.csrf = trouve.group(1)
        self.projet.progression_otp_required = True
        self.env.ref("mail.ir_cron_mail_scheduler_action").sudo().active = True
        self._demander()
        self.depuis = self._dernier_courriel_id()

    def _demander(self):
        with patch("odoo.addons.bf_stepbystep_clients.models.progression_otp"
                   ".secrets.randbelow", return_value=int(CODE)):
            self.url_open("/progression/%s/%s" % (
                self.projet.id, self.projet.progression_portal_token),
                allow_redirects=False)

    def _poster(self, code):
        return self.url_open(
            "/progression/%s/%s/code" % (self.projet.id, self.projet.progression_portal_token),
            data={"csrf_token": self.csrf, "code": code}, allow_redirects=False)

    def _suite(self):
        self.projet.invalidate_recordset(["progression_otp_failed_streak"])
        return self.projet.sudo().progression_otp_failed_streak

    def test_trois_erreurs_de_suite_avisent_une_fois(self):
        self._poster(FAUX)
        self._poster(FAUX)
        self._traiter()
        self.assertEqual(self._suite(), 2)
        self.assertFalse(self._activites(self.projet))
        self._poster(FAUX)
        self.assertEqual(self._suite(), 3)
        self._traiter()
        activites = self._activites(self.projet)
        self.assertEqual(len(activites), 1)
        self.assertIn("3 wrong access codes in a row", str(activites.note))
        self.assertEqual(activites.user_id, self.responsable)
        courriels = self._avis_par_courriel(self.depuis)
        self.assertEqual(len(courriels), 1)
        # Ni le code, ni l'adresse du visiteur, dans aucun avis. ⚠️ En essai, le
        # visiteur et le serveur sont tous deux 127.0.0.1 : l'adresse de base des
        # liens est retirée avant de chercher l'IP.
        ip = self.env["bf.progression.otp"].sudo().search(
            [("project_id", "=", self.projet.id)]).mapped("ip_address")
        self.assertIn("127.0.0.1", ip)
        base = self.projet.get_base_url()
        for texte in (str(courriels.body_html), str(activites.note)):
            self.assertNotIn(CODE, texte)
            self.assertNotIn(FAUX, texte)
            self.assertNotIn("127.0.0.1", texte.replace(base, ""))
        # La quatrième et la cinquième n'avisent pas de nouveau.
        self._poster(FAUX)
        self._poster(FAUX)
        self._traiter()
        self.assertEqual(len(self._avis_par_courriel(self.depuis)), 1)

    def test_un_code_juste_remet_la_suite_a_zero(self):
        self._poster(FAUX)
        self._poster(FAUX)
        self.assertEqual(self._poster(CODE).status_code, 303)
        self.assertEqual(self._suite(), 0)
        self._traiter()
        self.assertFalse(self._activites(self.projet))
        self.assertFalse(self._avis_par_courriel(self.depuis))

    def test_le_rearmement_remet_la_suite_a_zero(self):
        self._poster(FAUX)
        self._poster(FAUX)
        self.projet.action_progression_otp_reset()
        self.assertEqual(self._suite(), 0)

    def test_une_nouvelle_adresse_remet_la_suite_a_zero(self):
        self._poster(FAUX)
        self._poster(FAUX)
        self.projet.action_progression_portal_regenerate_token()
        self.assertEqual(self._suite(), 0)

    def test_une_suite_vieille_d_un_jour_repart_a_un(self):
        self._poster(FAUX)
        self._poster(FAUX)
        self.env.cr.execute(
            "UPDATE project_project SET progression_otp_failed_at = %s WHERE id = %s",
            (fields.Datetime.subtract(fields.Datetime.now(), hours=25), self.projet.id))
        self._poster(FAUX)
        self.assertEqual(self._suite(), 1)
        self._traiter()
        self.assertFalse(self._activites(self.projet))

    def test_la_suite_se_lit_d_un_code_a_l_autre(self):
        """Une suite ne s'arrête pas au code : deux erreurs, le code meurt, un
        code neuf, une erreur de plus, et c'est la troisième."""
        self._poster(FAUX)
        self._poster(FAUX)
        self.env["bf.progression.otp"].sudo().search(
            [("project_id", "=", self.projet.id)])._tuer()
        self._demander()
        self._poster(FAUX)
        self.assertEqual(self._suite(), 3)
        self._traiter()
        self.assertEqual(len(self._activites(self.projet)), 1)


@tagged("post_install", "-at_install")
class TestReglagesEtLangue(TransactionCase, _AvecResponsable):

    def test_les_reglages_par_defaut_sont_activite_et_courriel(self):
        societe = self.env["res.company"].create({"name": "Société neuve essai"})
        self.assertTrue(societe.progression_notify_activity)
        self.assertFalse(societe.progression_notify_inbox)
        self.assertTrue(societe.progression_notify_email)

    def test_la_page_des_parametres_affiche_et_garde_la_valeur(self):
        """⚠️ La page des paramètres se bâtit par `onchange` : un champ de
        façade mal fait s'y affiche faux, et l'enregistrer depuis un AUTRE
        onglet écrirait ce faux à la société."""
        societe = self.env.company
        societe.write({"progression_notify_activity": True,
                       "progression_notify_inbox": True,
                       "progression_notify_email": False})
        formulaire = Form(self.env["res.config.settings"])
        self.assertTrue(formulaire.progression_notify_inbox)
        self.assertFalse(formulaire.progression_notify_email)
        self.assertTrue(formulaire.progression_notify_activity)
        formulaire.save().execute()
        self.assertTrue(societe.progression_notify_inbox)
        self.assertFalse(societe.progression_notify_email)
        formulaire = Form(self.env["res.config.settings"])
        formulaire.progression_notify_email = True
        formulaire.save().execute()
        self.assertTrue(societe.progression_notify_email)

    def test_l_avis_parle_la_langue_du_responsable(self):
        """Libellés ET nom de la pièce : la copie `document_name` du dépôt est
        dans la langue du client, la pièce se relit dans celle du responsable."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.module.module"].search(
            [("name", "=", "bf_stepbystep_clients")])._update_translations(["fr_CA"])
        projet, colonnes = self._mandat(self.env)
        responsable = self._responsable(self.env, lang="fr_CA")
        projet.user_id = responsable
        projet.partner_id.lang = "en_US"
        self._regler(projet.company_id or responsable.company_id, "activity", "email")
        piece = self.env["bf.progression.document"].with_context(lang="en_US").create({
            "name": "Financial statements", "stage_id": colonnes[1].id})
        piece.update_field_translations("name", {"fr_CA": "États financiers"})
        depuis = self._dernier_courriel_id()
        # Comme la route : dans la langue du CLIENT.
        self.env["bf.progression.deposit"].with_context(lang="en_US").sudo()._verser(
            projet.with_context(lang="en_US"), piece.with_context(lang="en_US"),
            "bilan.pdf", CONTENU)
        self._traiter()
        tache = projet._progression_tache_du_depot(colonnes[1])
        activite = self._activites(tache)
        self.assertEqual(activite.summary, "Pièce déposée : États financiers")
        courriel = self._avis_par_courriel(depuis)
        self.assertIn("Ouvrir dans Odoo", str(courriel.body_html))
        self.assertIn("États financiers", str(courriel.body_html))
        self.assertEqual(courriel.subject, "Pièce déposée : États financiers")

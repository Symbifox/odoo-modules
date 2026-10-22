"""Chaque texte que ce module écrit pour quelqu'un d'autre est dans la langue de QUI LE LIRA.

18.0.2.2.0 passe la source en anglais (le français vit dans `i18n/fr_CA.po`). Odoo
ne traduit jamais vers en_US, et `_()` prend la langue de QUI ÉCRIT : la page
publique (utilisateur public), la passerelle de courriel ou un travail planifié
(OdooBot), la personne qui signale. Le lecteur est ailleurs :
- noms stockés, notes, billet, alertes ntfy : l'équipe de sécurité, donc la
  langue de la société (`company_env`) ;
- activité de triage, relance : la personne assignée (sinon la société) ;
- notes d'inscription, échec d'envoi de masse : l'opérateur qui agit (sinon la
  société) ;
- invitation : la personne invitée, sinon son organisation, sinon la société ;
- tableau de bord, message d'erreur : l'usager qui le regarde ;
- page publique : le visiteur.

Les essais épinglent fr_CA et en_CA (activées ici) sur la société, les usagers et
les partenaires : sur une base réelle, OdooBot et l'administrateur sont en fr_CA. La
langue de QUI ÉCRIT est toujours l'autre, pour que l'ancien code (`_()` de
l'écrivain) tombe.
"""

import importlib.util
import os
from types import SimpleNamespace
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

MODULE = "bf_security_awareness"
NTFY_POST = "odoo.addons.bf_security_awareness.models.bf_reported_phish.requests.post"

RISQUES_FR = {"critical": "Critique", "high": "Élevé", "moderate": "Modéré", "low": "Faible"}
RISQUES_EN = {"critical": "Critical", "high": "High", "moderate": "Moderate", "low": "Low"}


def _activer_langues(env):
    for code in ("fr_CA", "en_CA"):
        env["res.lang"]._activate_lang(code)
    env["ir.module.module"]._load_module_terms([MODULE], ["fr_CA"], overwrite=True)


class LangueCommun(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _activer_langues(cls.env)
        cls.societe = cls.env.company
        cls.societe.partner_id.lang = "fr_CA"
        cls.ICP = cls.env["ir.config_parameter"].sudo()
        cls.signaleur = cls.env["res.partner"].create({
            "name": "Sam Signaleur", "email": "sam.signaleur@exemple.test", "lang": "en_CA"})

    def _societe_en(self, lang):
        self.societe.partner_id.lang = lang

    def _corps(self, enregistrement):
        return " ".join(enregistrement.message_ids.mapped("body"))

    def _signalement(self, lang_ecrivain, **valeurs):
        valeurs = {"reporter_id": self.signaleur.id, "category": "wire_fraud",
                   "subject": "Facture urgente", "email_from": "pirate@mechant.test",
                   **valeurs}
        return self.env["bf.reported.phish"].with_context(lang=lang_ecrivain).create(valeurs)

    def _campagne(self, lang_ecrivain="en_CA", nom="Campagne d'essai", destinataires=None,
                  **valeurs):
        modele = self.env["bf.phishing.template"].create({
            "name": "Leurre d'essai", "subject": "Action requise",
            "difficulty": "easy", "landing_mode": "credential"})
        destinataires = destinataires or self.env["res.partner"].create({
            "name": "Cible d'essai", "email": "cible.essai@exemple.test"})
        return self.env["bf.phishing.campaign"].with_context(lang=lang_ecrivain).create({
            "name": nom, "template_id": modele.id,
            "recipient_partner_ids": [(6, 0, destinataires.ids)], **valeurs})


# --------------------------------------------------------------------------- #
# Noms stockés : la langue de la société
# --------------------------------------------------------------------------- #
@tagged("post_install", "-at_install")
class TestNomsStockes(LangueCommun):

    def test_nom_du_signalement_dans_la_langue_de_la_societe(self):
        """bf_reported_phish._compute_name : « Report: %s » suivait l'écrivain."""
        self.assertEqual(self._signalement("en_CA").name, "Signalement — Facture urgente")
        self._societe_en("en_CA")
        self.assertEqual(self._signalement("fr_CA").name, "Report: Facture urgente")

    def test_nom_du_signalement_sans_sujet_ni_expediteur(self):
        """Le repli « Suspicious email » suit aussi la société."""
        nom = self._signalement("en_CA", subject=False, email_from=False).name
        self.assertEqual(nom, "Signalement — Courriel suspect")

    def test_nom_du_retrait_dans_la_langue_de_la_societe(self):
        """bf_clawback._compute_name : « Removal: %s »."""
        connecteur = self.env["bf.mail.clawback.connector"].create({
            "name": "Connecteur d'essai", "backend": "m365_oauth", "mailbox_source": "manual",
            "manual_emails": "a@bf.test", "tenant_id": "t", "client_id": "c"})
        Operation = self.env["bf.clawback.operation"]
        op = Operation.with_context(lang="en_CA").create({
            "connector_id": connecteur.id, "subject": "Gagnez un prix"})
        self.assertEqual(op.name, "Retrait — Gagnez un prix")
        sans_sujet = Operation.with_context(lang="en_CA").create({
            "connector_id": connecteur.id, "message_id": False})
        self.assertEqual(sans_sujet.name, "Retrait — courriel")
        self._societe_en("en_CA")
        op = Operation.with_context(lang="fr_CA").create({
            "connector_id": connecteur.id, "subject": "Gagnez un prix"})
        self.assertEqual(op.name, "Removal: Gagnez un prix")

    def test_campagne_utm_dans_la_langue_de_la_societe(self):
        """bf_phishing_campaign._ensure_utm_campaign : « Phishing: %s »."""
        campagne = self._campagne("en_CA", nom="Trimestre 4")
        campagne.with_context(lang="en_CA").action_prepare()
        self.assertEqual(campagne.utm_campaign_id.name, "Hameçonnage : Trimestre 4")
        self._societe_en("en_CA")
        autre = self._campagne("fr_CA", nom="Trimestre 1", destinataires=self.signaleur)
        autre.with_context(lang="fr_CA").action_prepare()
        self.assertEqual(autre.utm_campaign_id.name, "Phishing: Trimestre 1")

    def test_titre_du_lien_piege_dans_la_langue_de_la_societe(self):
        """link_tracker._get_title_from_url : titre stocké, lu par les opérateurs."""
        base = self.ICP.get_param("web.base.url") or "http://localhost:8069"
        self.ICP.set_param("web.base.url", base)
        url = base.rstrip("/") + "/phish/jeton-essai"
        Lien = self.env["link.tracker"]
        self.assertEqual(Lien.with_context(lang="en_CA")._get_title_from_url(url),
                         "Simulation d'hameçonnage")
        self._societe_en("en_CA")
        self.assertEqual(Lien.with_context(lang="fr_CA")._get_title_from_url(url),
                         "Phishing simulation")

    def test_nom_par_defaut_du_connecteur_dans_la_langue_de_l_usager(self):
        """bf_mail_connector : le défaut était « Connecteur courriel interne » en dur."""
        Connecteur = self.env["bf.mail.clawback.connector"]
        valeurs = {"backend": "m365_oauth", "mailbox_source": "manual",
                   "manual_emails": "a@bf.test", "tenant_id": "t", "client_id": "c"}
        self.assertEqual(Connecteur.with_context(lang="en_CA").create(dict(valeurs)).name,
                         "Internal email connector")
        self.assertEqual(Connecteur.with_context(lang="fr_CA").create(dict(valeurs)).name,
                         "Connecteur courriel interne")


# --------------------------------------------------------------------------- #
# Signalement : notes, billet, ntfy, activité de triage
# --------------------------------------------------------------------------- #
@tagged("post_install", "-at_install")
class TestSignalementTriage(LangueCommun):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.trieur_fr = new_test_user(
            cls.env, login="trieur_fr_essai", name="Trieuse Francophone",
            email="trieuse.fr.essai@exemple.test", lang="fr_CA",
            groups="base.group_user,bf_security_awareness.group_bf_secaware_manager")
        cls.trieur_en = new_test_user(
            cls.env, login="trieur_en_essai", name="English Triager",
            email="triager.en.essai@exemple.test", lang="en_CA",
            groups="base.group_user,bf_security_awareness.group_bf_secaware_manager")
        cls.ICP.set_param("bf_security_awareness.report_ntfy_url", "")

    def _ntfy(self):
        self.ICP.set_param("bf_security_awareness.report_ntfy_url",
                           "https://ntfy.exemple.invalid/securite")

    def _billet(self, signalement):
        self.assertTrue(signalement.helpdesk_ticket_id, "le billet doit être créé")
        return self.env["helpdesk.ticket"].sudo().browse(signalement.helpdesk_ticket_id)

    # -- notes au chatter -----------------------------------------------------
    def test_note_de_triage_dans_la_langue_de_la_societe(self):
        """process() : « Suspicious email sent to security triage. »"""
        rapport = self._signalement("en_CA")
        rapport.with_context(lang="en_CA").process()
        corps = self._corps(rapport)
        self.assertIn("Courriel suspect transmis au triage de sécurité.", corps)
        self.assertNotIn("Suspicious email sent to security triage.", corps)

    def test_note_de_simulation_reconnue_dans_la_langue_de_la_societe(self):
        """process() : « Report recognized: ... » sur un leurre de nos campagnes."""
        campagne = self._campagne("en_CA", destinataires=self.signaleur)
        campagne.action_prepare()
        resultat = campagne.result_ids[0]
        rapport = self._signalement(
            "en_CA", suspicious_link="https://x.test/phish/%s" % resultat.token)
        retour = rapport.with_context(lang="en_CA").process()
        self.assertTrue(retour["is_simulation"])
        self.assertIn("Signalement reconnu : ce courriel faisait partie", self._corps(rapport))

    # -- billet d'assistance ----------------------------------------------------
    def test_billet_dans_la_langue_de_la_societe_categorie_traduite(self):
        """_create_helpdesk_ticket : catégorie lue par la sélection, plus `dict(REPORT_CATEGORIES)`."""
        if "helpdesk.ticket" not in self.env:
            self.skipTest("pas de module d'assistance")
        rapport = self._signalement("en_CA")
        rapport.with_context(lang="en_CA").process()
        billet = self._billet(rapport)
        self.assertEqual(billet.name, "Courriel suspect : Facture urgente")
        description = str(billet.description)
        self.assertIn("Fraude au virement / au président", description)
        self.assertIn("Catégorie :", description)
        self.assertNotIn("Wire fraud / CEO fraud", description)

    def test_billet_anglais_pour_une_societe_anglaise(self):
        if "helpdesk.ticket" not in self.env:
            self.skipTest("pas de module d'assistance")
        self._societe_en("en_CA")
        rapport = self._signalement("fr_CA")
        rapport.with_context(lang="fr_CA").process()
        billet = self._billet(rapport)
        self.assertEqual(billet.name, "Suspicious email: Facture urgente")
        self.assertIn("Wire fraud / CEO fraud", str(billet.description))
        self.assertNotIn("Fraude au virement", str(billet.description))

    # -- ntfy -----------------------------------------------------------------
    def test_alerte_ntfy_en_tete_ascii_dans_la_langue_de_la_societe(self):
        """_notify_security_team : titre traduit puis replié en ASCII (`_ascii_header`)."""
        self._ntfy()
        rapport = self._signalement("en_CA")
        with patch(NTFY_POST) as envoi:
            rapport.with_context(lang="en_CA")._notify_security_team()
        self.assertEqual(envoi.call_count, 1)
        entetes = envoi.call_args.kwargs["headers"]
        corps = envoi.call_args.kwargs["data"].decode("utf-8")
        self.assertEqual(entetes["Title"], "Courriel malveillant signale")
        self.assertTrue(entetes["Title"].isascii())
        self.assertIn("Signalé par : Sam Signaleur", corps)
        self.assertIn("Catégorie : Fraude au virement / au président", corps)

    def test_alerte_ntfy_anglaise_pour_une_societe_anglaise(self):
        """Le titre était « Courriel malveillant signale » en dur, quelle que soit la langue."""
        self._ntfy()
        self._societe_en("en_CA")
        rapport = self._signalement("fr_CA")
        with patch(NTFY_POST) as envoi:
            rapport.with_context(lang="fr_CA")._notify_security_team()
        entetes = envoi.call_args.kwargs["headers"]
        self.assertEqual(entetes["Title"], "Malicious email reported")
        corps = envoi.call_args.kwargs["data"].decode("utf-8")
        self.assertIn("Category: Wire fraud / CEO fraud", corps)

    def test_alerte_ntfy_du_retrait_dans_la_langue_de_la_societe(self):
        """_notify_security_team_clawback : « Clawback executed », en-tête ASCII."""
        self._ntfy()
        rapport = self._signalement("en_CA")
        operation = SimpleNamespace(messages_removed=3, mailboxes_swept=2)
        with patch(NTFY_POST) as envoi:
            rapport.with_context(lang="en_CA")._notify_security_team_clawback(operation)
        entetes = envoi.call_args.kwargs["headers"]
        self.assertEqual(entetes["Title"], "Clawback execute")
        self.assertTrue(entetes["Title"].isascii())
        self.assertIn("Retrait exécuté : 3 courriel(s)",
                      envoi.call_args.kwargs["data"].decode("utf-8"))
        self._societe_en("en_CA")
        with patch(NTFY_POST) as envoi:
            rapport.with_context(lang="fr_CA")._notify_security_team_clawback(operation)
        self.assertEqual(envoi.call_args.kwargs["headers"]["Title"], "Clawback executed")

    # -- activité de triage -----------------------------------------------------
    def _activite_de_triage(self, trieur, lang_ecrivain):
        self.ICP.set_param("bf_security_awareness.report_responsible_user_id", str(trieur.id))
        rapport = self._signalement(lang_ecrivain)
        rapport.with_context(lang=lang_ecrivain)._schedule_triage_activity()
        activite = rapport.activity_ids
        self.assertEqual(len(activite), 1)
        self.assertEqual(activite.user_id, trieur)
        avis = self.env["mail.message"].search([
            ("model", "=", rapport._name), ("res_id", "=", rapport.id),
            ("message_type", "=", "user_notification"),
            ("partner_ids", "in", trieur.partner_id.ids)])
        return activite, avis

    def test_activite_de_triage_dans_la_langue_de_l_assignee(self):
        """_schedule_triage_activity : résumé et note dans la langue de l'assignée."""
        activite, avis = self._activite_de_triage(self.trieur_fr, "en_CA")
        self.assertEqual(activite.summary, "Trier le courriel suspect signalé")
        self.assertIn("Signalé par Sam Signaleur", str(activite.note))
        # Créée dans son env : l'objet de l'avis d'assignation suit aussi.
        self.assertEqual(len(avis), 1)
        self.assertIn("Trier le courriel suspect signalé", avis.subject)
        self.assertNotIn("assigned to you", avis.subject)

    def test_activite_de_triage_anglaise_pour_un_assigne_anglais(self):
        activite, avis = self._activite_de_triage(self.trieur_en, "fr_CA")
        self.assertEqual(activite.summary, "Triage the reported suspicious email")
        self.assertIn("Reported by Sam Signaleur", str(activite.note))
        self.assertIn("assigned to you", avis.subject)

    def test_activite_de_triage_sans_langue_prend_la_societe(self):
        self.trieur_en.lang = False
        activite, _avis = self._activite_de_triage(self.trieur_en, "en_CA")
        self.assertEqual(activite.summary, "Trier le courriel suspect signalé")


# --------------------------------------------------------------------------- #
# Résultats d'hameçonnage et campagne
# --------------------------------------------------------------------------- #
@tagged("post_install", "-at_install")
class TestResultatsEtCampagne(LangueCommun):

    def _resultat(self):
        campagne = self._campagne("en_CA")
        campagne.action_prepare()
        return campagne.result_ids[0]

    def test_notes_de_la_page_publique_dans_la_langue_de_la_societe(self):
        """register_click / register_submit / register_report : écrits par l'utilisateur public."""
        resultat = self._resultat()
        public = resultat.with_user(self.env.ref("base.public_user")).sudo().with_context(
            lang="en_CA")
        public.register_click()
        public.register_submit()
        public.register_report()
        corps = self._corps(resultat)
        self.assertIn("A cliqué sur le lien piégé.", corps)
        self.assertIn("A soumis le faux formulaire de connexion.", corps)
        self.assertIn("A signalé le courriel comme hameçonnage.", corps)
        self.assertNotIn("Clicked the trap link.", corps)

    def test_echec_de_l_envoi_de_masse_dans_la_langue_de_l_operateur(self):
        """_release_due_results : la note suit l'opérateur qui lance, sinon la société."""
        operateur = new_test_user(
            self.env, login="operateur_en_essai", name="English Operator", lang="en_CA",
            email="operator.en.essai@exemple.test",
            groups="base.group_user,bf_security_awareness.group_bf_secaware_manager")
        campagne = self._campagne("fr_CA")
        campagne.action_prepare()
        Mailing = type(self.env["mailing.mailing"])
        with patch.object(Mailing, "action_send_mail", side_effect=Exception("boom")):
            campagne.with_user(operateur).with_context(lang="fr_CA").action_launch()
        corps = self._corps(campagne)
        self.assertIn("The mass mailing failed (boom)", corps)
        self.assertNotIn("L'envoi de masse a échoué", corps)

    def test_echec_de_l_envoi_de_masse_par_le_travail_planifie(self):
        """OdooBot n'est pas un lecteur : la société."""
        campagne = self._campagne("en_CA")
        campagne.action_prepare()
        Mailing = type(self.env["mailing.mailing"])
        with patch.object(Mailing, "action_send_mail", side_effect=Exception("boom")):
            campagne.with_context(lang="en_CA").action_launch()
        self.assertIn("L'envoi de masse a échoué (boom)", self._corps(campagne))


# --------------------------------------------------------------------------- #
# Assignation : inscription, invitation, relance
# --------------------------------------------------------------------------- #
@tagged("post_install", "-at_install")
class TestAssignationLangue(LangueCommun):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.societe.write({"email": "securite.essai@exemple.test"})
        cls.cours = cls.env["slide.channel"].create({
            "name": "Reconnaître l'hameçonnage", "channel_type": "training",
            "enroll": "invite", "visibility": "connected",
            # Publié : la note d'inscription lit le nom du cours en tant qu'opérateur.
            "is_published": True})
        cls.op_fr = new_test_user(
            cls.env, login="op_fr_essai", name="Opératrice Francophone", lang="fr_CA",
            email="op.fr.essai@exemple.test", notification_type="inbox",
            groups="base.group_user,bf_security_awareness.group_bf_secaware_manager")
        cls.op_en = new_test_user(
            cls.env, login="op_en_essai", name="English Operator", lang="en_CA",
            email="op.en.essai@exemple.test", notification_type="inbox",
            groups="base.group_user,bf_security_awareness.group_bf_secaware_manager")
        cls.gabarit = cls.env.ref("bf_security_awareness.mail_template_training_invite")

    def _personne(self, lang, **valeurs):
        personne = self.env["res.partner"].create({
            "name": "Alex Invité", "email": "alex.essai@exemple.test", **valeurs})
        personne.lang = lang
        return personne

    def _assignation(self, personne, assigne_par, **valeurs):
        return self.env["bf.training.assignment"].create({
            "partner_id": personne.id, "channel_id": self.cours.id,
            "assigned_by_id": assigne_par.id, **valeurs})

    def _relance(self, assignation):
        todo = self.env.ref("mail.mail_activity_data_todo")
        return assignation.activity_ids.filtered(lambda a: a.activity_type_id == todo)

    def _inviter(self, assignation, qui, lang_qui):
        dernier = self.env["mail.mail"].search([], order="id desc", limit=1).id or 0
        assignation.with_user(qui).with_context(lang=lang_qui).action_send_invite()
        courriel = self.env["mail.mail"].search([
            ("id", ">", dernier), ("model", "=", "bf.training.assignment"),
            ("res_id", "=", assignation.id)])
        self.assertEqual(len(courriel), 1, "l'invitation doit partir")
        return courriel

    # -- action_enroll ------------------------------------------------------------
    def test_note_d_inscription_dans_la_langue_de_l_operateur(self):
        """action_enroll : la note et le libellé d'état (par la sélection) suivent l'opérateur."""
        assignation = self._assignation(self._personne("fr_CA", email=False), self.op_en)
        assignation.with_user(self.op_en).with_context(lang="fr_CA").action_enroll()
        self.assertEqual(assignation.state, "invited")
        corps = self._corps(assignation)
        self.assertIn("Enrolled in course", corps)
        self.assertIn("(Invited).", corps)
        self.assertNotIn("Inscrite au cours", corps)

    def test_note_d_inscription_francaise_libelle_d_etat_traduit(self):
        """L'ancien code lisait `selection` brut : « (invited) » ou « (Invited) » en français."""
        assignation = self._assignation(self._personne("en_CA", email=False), self.op_fr)
        assignation.with_user(self.op_fr).with_context(lang="en_CA").action_enroll()
        corps = self._corps(assignation)
        self.assertIn("Inscrite au cours", corps)
        self.assertIn("(Invitée).", corps)
        self.assertNotIn("Enrolled in course", corps)

    def test_note_d_inscription_par_odoobot_prend_la_societe(self):
        assignation = self._assignation(self._personne("en_CA", email=False), self.op_en)
        assignation.with_context(lang="en_CA").action_enroll()
        self.assertIn("(Invitée).", self._corps(assignation))

    # -- invitation -----------------------------------------------------------------
    def test_invitation_dans_la_langue_de_l_invite(self):
        """action_send_invite : le gabarit sans `lang` se rendait dans la langue de qui envoie."""
        self.gabarit.lang = False
        assignation = self._assignation(self._personne("en_CA"), self.op_fr)
        courriel = self._inviter(assignation, self.op_fr, "fr_CA")
        self.assertEqual(courriel.subject, "Cybersecurity training to complete")
        self.assertIn("Hello", courriel.body_html)
        self.assertNotIn("Bonjour", courriel.body_html)

    def test_invitation_sans_langue_prend_celle_de_son_organisation(self):
        organisation = self.env["res.partner"].create({
            "name": "Organisation d'essai", "is_company": True})
        organisation.lang = "fr_CA"
        personne = self._personne(False, parent_id=organisation.id)
        assignation = self._assignation(personne, self.op_en)
        courriel = self._inviter(assignation, self.op_en, "en_CA")
        self.assertEqual(courriel.subject, "Formation en cybersécurité à compléter")
        self.assertIn("Bonjour", courriel.body_html)

    def test_invitation_sans_langue_ni_organisation_prend_la_societe(self):
        self._societe_en("en_CA")
        assignation = self._assignation(self._personne(False), self.op_fr)
        courriel = self._inviter(assignation, self.op_fr, "fr_CA")
        self.assertEqual(courriel.subject, "Cybersecurity training to complete")
        self.assertNotIn("Bonjour", courriel.body_html)

    # -- relance ----------------------------------------------------------------------
    def test_relance_de_l_invitation_dans_la_langue_du_responsable(self):
        """_planifier_relance : le résumé était composé par qui envoie."""
        assignation = self._assignation(self._personne("fr_CA"), self.op_en)
        self._inviter(assignation, self.op_fr, "fr_CA")
        relance = self._relance(assignation)
        self.assertEqual(relance.user_id, self.op_en)
        self.assertEqual(relance.summary,
                         "Remind Alex Invité about the cybersecurity training")

    def test_relance_du_retard_dans_la_langue_du_responsable(self):
        """_cron_training_reminders → _planifier_relance(retard=True) : plus la langue d'OdooBot."""
        assignation = self._assignation(
            self._personne("en_CA"), self.op_fr,
            due_date=fields.Date.add(fields.Date.today(), days=-3))
        self.env["bf.training.assignment"].with_context(
            lang="en_CA")._cron_training_reminders()
        relance = self._relance(assignation)
        self.assertEqual(relance.user_id, self.op_fr)
        self.assertEqual(relance.summary, "Formation cybersécurité en retard : Alex Invité")

    def test_relance_d_un_responsable_sans_langue_prend_la_societe(self):
        self.op_en.lang = False
        assignation = self._assignation(self._personne("en_CA"), self.op_en)
        assignation.with_context(lang="en_CA")._planifier_relance()
        self.assertEqual(self._relance(assignation).summary,
                         "Relancer Alex Invité sur la formation cybersécurité")


# --------------------------------------------------------------------------- #
# Tableau de bord et message d'erreur : la langue de l'usager
# --------------------------------------------------------------------------- #
@tagged("post_install", "-at_install")
class TestTableauDeBord(LangueCommun):

    def test_repartition_des_risques_dans_la_langue_de_l_usager(self):
        """_get_risk_distribution : libellés de `_RISK_META` en anglais en dur."""
        Tableau = self.env["bf.security.dashboard"]
        for lang, attendus in (("fr_CA", RISQUES_FR), ("en_CA", RISQUES_EN)):
            niveaux = Tableau.with_context(lang=lang)._get_risk_distribution()["levels"]
            self.assertEqual({n["level"]: n["label"] for n in niveaux}, attendus, lang)

    def test_recidivistes_portent_le_libelle_du_risque(self):
        """_get_repeat_clickers : `risk_label` traduit (le gabarit affichait `risk_level` brut)."""
        societe = self.env["res.company"].create({"name": "Société Récidive"})
        personne = self.env["res.partner"].create({
            "name": "Rita Récidive", "email": "rita.essai@exemple.test"})
        for nom in ("Récidive A", "Récidive B"):
            campagne = self._campagne(
                "fr_CA", nom=nom, destinataires=personne, company_id=societe.id)
            campagne.with_company(societe).action_prepare()
            self.assertEqual(campagne.result_ids.company_id, societe)
            campagne.result_ids.write({"state": "clicked"})
        self.env.flush_all()  # la requête des récidivistes est en SQL
        Tableau = self.env["bf.security.dashboard"].with_company(societe).with_context(
            allowed_company_ids=societe.ids)
        for lang, attendus in (("fr_CA", RISQUES_FR), ("en_CA", RISQUES_EN)):
            lignes = [r for r in Tableau.with_context(lang=lang)._get_repeat_clickers()
                      if r["partner_id"] == personne.id]
            self.assertEqual(len(lignes), 1, lang)
            self.assertEqual(lignes[0]["risk_label"], attendus[lignes[0]["risk_level"]], lang)

    def test_badge_de_resilience_dans_la_langue_de_l_usager(self):
        """_resilience_badge : libellés et conseils en français en dur."""
        Tableau = self.env["bf.security.dashboard"]
        reflexe = SimpleNamespace(emails_received=3, risk_level="low", reported_count=2)
        en = Tableau.with_context(lang="en_CA")
        self.assertEqual(en._resilience_badge(False)["label"], "Not yet tested")
        self.assertEqual(en._resilience_badge(False)["hint"], "You have not been tested yet.")
        self.assertEqual(en._resilience_badge(reflexe)["label"], "Great reflex")
        fr = Tableau.with_context(lang="fr_CA")
        self.assertEqual(fr._resilience_badge(False)["label"], "À découvrir")
        self.assertEqual(fr._resilience_badge(reflexe)["label"], "Excellent réflexe")

    def test_libelle_expediteur_du_controle_imap_traduit(self):
        """bf_clawback._criteria : « Sender » passé en dur à `_reject_control`."""
        connecteur = self.env["bf.mail.clawback.connector"].create({
            "name": "Connecteur contrôle", "backend": "m365_oauth", "mailbox_source": "manual",
            "manual_emails": "a@bf.test", "tenant_id": "t", "client_id": "c"})
        op = self.env["bf.clawback.operation"].create({
            "connector_id": connecteur.id, "match_strategy": "heuristic",
            "email_from": "pirate\r\n@mechant.test"})
        with self.assertRaises(UserError) as fr:
            op.with_context(lang="fr_CA")._criteria()
        self.assertIn("Expéditeur", str(fr.exception))
        with self.assertRaises(UserError) as en:
            op.with_context(lang="en_CA")._criteria()
        self.assertIn("Sender", str(en.exception))


# --------------------------------------------------------------------------- #
# Migration 18.0.2.2.0 : le gabarit d'invitation livré
# --------------------------------------------------------------------------- #
@tagged("post_install", "-at_install")
class TestMigrationGabaritInvitation(LangueCommun):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        chemin = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations",
                              "18.0.2.2.0", "end-migrate.py")
        spec = importlib.util.spec_from_file_location("bf_sa_mig_essai_end", chemin)
        cls.mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mig)
        cls.gabarit = cls.env.ref("bf_security_awareness.mail_template_training_invite")

    def _livre_fr(self, champ):
        return self.mig.LIVRES_FR[("data/mail_template_data.xml",
                                   "mail_template_training_invite", champ)]

    def _poser(self, corps_en, sujet_en):
        """L'état d'une base avant 18.0.2.2.0 : le français livré en en_US et en fr_CA."""
        corps_fr = self.mig._forme(self.gabarit, "body_html", self._livre_fr("body_html"))
        sujet_fr = self._livre_fr("subject")
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = jsonb_build_object('en_US', %s::text, "
            "'fr_CA', %s::text), subject = jsonb_build_object('en_US', %s::text, "
            "'fr_CA', %s::text) WHERE id = %s",
            (corps_en if corps_en is not None else corps_fr, corps_fr,
             sujet_en if sujet_en is not None else sujet_fr, sujet_fr, self.gabarit.id))
        self.gabarit.invalidate_recordset()

    def _migrer(self):
        self.mig.migrate(self.env.cr, "18.0.2.1.0")
        self.env.invalidate_all()

    def test_corps_livre_passe_a_l_anglais_en_en_us_et_reste_francais_en_fr_ca(self):
        self._poser(None, None)
        self.assertIn("Bonjour", self.gabarit.with_context(lang="en_US").body_html)
        self._migrer()
        en = self.gabarit.with_context(lang="en_US")
        fr = self.gabarit.with_context(lang="fr_CA")
        self.assertIn("Hello", en.body_html)
        self.assertIn("A short cybersecurity awareness training", en.body_html)
        self.assertNotIn("Bonjour", en.body_html)
        self.assertEqual(en.subject, "Cybersecurity training to complete")
        self.assertIn("Bonjour", fr.body_html)
        self.assertIn("Une courte formation de sensibilisation", fr.body_html)
        self.assertNotIn("Hello", fr.body_html)
        self.assertEqual(fr.subject, "Formation en cybersécurité à compléter")

    def test_corps_retouche_reste_tel_quel(self):
        self._poser("<p>Corps retouché par le client</p>", "Sujet retouché")
        self._migrer()
        en = self.gabarit.with_context(lang="en_US")
        self.assertIn("Corps retouché par le client", en.body_html)
        self.assertEqual(en.subject, "Sujet retouché")


# --------------------------------------------------------------------------- #
# Page publique : titre et `lang` selon le visiteur
# --------------------------------------------------------------------------- #
@tagged("post_install", "-at_install")
class TestPagePubliqueLangue(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _activer_langues(cls.env)
        Lang = cls.env["res.lang"]
        cls.fr = Lang._lang_get("fr_CA")
        cls.en = Lang._lang_get("en_CA")
        cls.env["website"].search([]).write({
            "language_ids": [(4, cls.fr.id), (4, cls.en.id)],
            "default_lang_id": cls.fr.id})

    def _page(self, lang, chemin):
        resp = self.url_open("/%s%s" % (lang.url_code, chemin))
        self.assertEqual(resp.status_code, 200)
        return resp.text

    def test_page_de_lecon_selon_le_visiteur(self):
        """controllers/main._render_lesson : `<title>` et `lang` étaient en français en dur."""
        anglais = self._page(self.en, "/phish/jeton-inconnu-essai/lesson")
        self.assertIn('<html lang="en-CA">', anglais)
        self.assertIn("<title>Security simulation</title>", anglais)
        francais = self._page(self.fr, "/phish/jeton-inconnu-essai/lesson")
        self.assertIn('<html lang="fr-CA">', francais)
        self.assertIn("<title>Simulation de sécurité</title>", francais)

    def test_page_de_remerciement_selon_le_visiteur(self):
        """controllers/main.phish_report : « Merci » en dur."""
        anglais = self._page(self.en, "/phish/jeton-inconnu-essai/report")
        self.assertIn('<html lang="en-CA">', anglais)
        self.assertIn("<title>Thank you</title>", anglais)
        francais = self._page(self.fr, "/phish/jeton-inconnu-essai/report")
        self.assertIn('<html lang="fr-CA">', francais)
        self.assertIn("<title>Merci</title>", francais)

    def test_faux_formulaire_identique_pour_tout_visiteur(self):
        """views/phishing_landing_templates.xml : le faux formulaire reste en français.

        C'est un contenu d'exercice (`lang="fr"`, « Se connecter ») : ses étiquettes ne
        suivent pas le visiteur, sinon la page mêle deux langues et trahit l'exercice.
        Le `<form>` porte `t-translation="off"` : ni extrait ni traduit.
        """
        en_us = self.env["res.lang"]._activate_lang("en_US")
        self.env["website"].search([]).write({"language_ids": [(4, en_us.id)]})
        modele = self.env["bf.phishing.template"].create({
            "name": "Leurre d'essai", "subject": "Action requise",
            "difficulty": "easy", "landing_mode": "credential"})
        cible = self.env["res.partner"].create({
            "name": "Cible d'essai", "email": "cible.formulaire@exemple.test"})
        campagne = self.env["bf.phishing.campaign"].create({
            "name": "Campagne d'essai", "template_id": modele.id,
            "recipient_partner_ids": [(6, 0, cible.ids)]})
        campagne.action_prepare()
        jeton = campagne.result_ids[0].token
        for lang in (en_us, self.en, self.fr):
            page = self._page(lang, "/phish/%s" % jeton)
            self.assertIn('<label for="login">Identifiant ou courriel</label>', page, lang.code)
            self.assertIn('<label for="password">Mot de passe</label>', page, lang.code)
            self.assertNotIn("Username or email", page, lang.code)
        vue = self.env.ref("bf_security_awareness.phishing_credential_form")
        termes = vue._fields["arch_db"].get_trans_terms(vue.with_context(lang="en_US").arch_db)
        self.assertNotIn("Identifiant ou courriel", termes)
        self.assertNotIn("Mot de passe", termes)

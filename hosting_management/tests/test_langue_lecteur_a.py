# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Chaque texte du module s'écrit dans la langue de qui le lira (partie A).

Le module est passé d'une source française à une source anglaise ; le français vit
dans `i18n/fr_CA.po`. Ces essais tiennent les correctifs qui choisissent le LECTEUR :

* le récapitulatif : la langue de l'usager destinataire (corps, objet, type de
  maintenance, pastille d'état), et un nom par défaut traduisible ;
* les alertes de santé (courriel et ntfy), le journal d'audit (services, tableau de
  bord, serveurs, postes), les constats des sondes : la langue des OPÉRATEURS,
  c'est-à-dire celle du partenaire de la société, pas celle de qui déclenche ;
* les activités (expiration, configuration) : créées dans l'env de l'ASSIGNÉ, parce
  que l'objet de l'avis d'Odoo suit la langue du cadre de création (piège 63) ;
* `os_eol_tag`, valeur STOCKÉE : la langue de la société ;
* le courriel de garantie : la langue du responsable du service ;
* les noms d'action et les erreurs de validation : la langue de l'usager.

Chaque essai met le lecteur dans une langue et l'appelant dans l'autre : un correctif
annulé fait passer le texte du côté de l'appelant (ou du français d'origine), et
l'assertion tombe. Les langues sont épinglées (usagers créés en fr_CA ou en_CA,
partenaire de la société réécrit) : rien ne dépend des données copiées de la prod.
Rien ne sort : `mail.mail.send`, ntfy et `requests.get` sont interceptés.
"""
import unittest
from datetime import timedelta
from unittest.mock import patch

import requests

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..models import hosting_email_template as gabarit

MODULE = "odoo.addons.hosting_management.models.hosting_service"


@tagged("post_install", "-at_install")
class TestLangueA(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(
            ["hosting_management"], ["fr_CA"], overwrite=True)
        groupes = "base.group_user,hosting_management.group_hosting_user"
        cls.usager_en = new_test_user(
            cls.env, login="langue_a9_en", groups=groupes, name="Avery Essai",
            lang="en_CA", email="avery.a9@example.org", notification_type="inbox")
        cls.usager_fr = new_test_user(
            cls.env, login="langue_a9_fr", groups=groupes, name="Morgane Essai",
            lang="fr_CA", email="morgane.a9@example.org", notification_type="inbox")
        cls.client = cls.env["res.partner"].create({
            "name": "Client Langue A9", "is_company": True, "has_sla": True,
        })
        cls.logiciel = cls.env["hosting.software"].create({
            "name": "Logiciel Langue A9", "code": "TLA9", "software_type": "self_hosted",
        })

    # -- outils ----------------------------------------------------------
    def _env(self, lang):
        return self.env(context=dict(self.env.context, lang=lang))

    def _operateurs(self, lang):
        """La langue des opérateurs est celle du partenaire de la société."""
        self.env.company.partner_id.lang = lang

    def _service(self, nom, **vals):
        return self.env["hosting.service"].create({
            "name": nom, "partner_id": self.client.id,
            "software_id": self.logiciel.id, **vals,
        })

    def _seul_actif(self, service):
        """Suspendre (en SQL, sans journal) tous les autres services actifs : les
        sondes balaient tous les services actifs, ceux de la prod copiée compris."""
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE hosting_service SET state = 'suspended' "
            "WHERE state = 'active' AND id != %s", (service.id,))
        self.env.invalidate_all()

    def _audit(self, modele, res_id):
        return self.env["hosting.audit.log"].sudo().search(
            [("res_model", "=", modele), ("res_id", "=", res_id)], order="id")

    def _activite_et_avis(self, service, resume=None):
        domaine = [("res_model", "=", "hosting.service"), ("res_id", "=", service.id)]
        if resume:
            domaine.append(("summary", "=", resume))
        activite = self.env["mail.activity"].search(domaine, limit=1)
        self.assertTrue(activite)
        avis = self.env["mail.message"].search([
            ("model", "=", "hosting.service"), ("res_id", "=", service.id),
            ("message_type", "=", "user_notification"),
            ("subject", "ilike", activite.summary),
        ], order="id desc", limit=1)
        self.assertTrue(avis, "aucun avis d'assignation")
        return activite, avis

    def _donnees_recap(self, digest):
        """Un service en panne et une maintenance en retard, sans lire la prod."""
        service = self._service("Service Recap A9", state="active")
        self.env["hosting.health.check"].create({"service_id": service.id, "status": "down"})
        self.assertEqual(service.last_health_status, "down")
        planif = self.env["hosting.maintenance.schedule"].create({
            "name": "Correctifs A9", "service_id": service.id,
            "maintenance_type": "security_patch", "frequency": "monthly",
            "last_performed": fields.Date.today() - timedelta(days=90),
        })
        self.assertLess(planif.days_until_due, 0)
        return {
            "digest": digest, "expiring_services": [], "updates_services": [],
            "storage_alert_services": [], "health_issue_services": service,
            "maintenance_overdue": planif, "maintenance_due_soon": [],
        }

    # -- récapitulatif (hosting_digest.py) --------------------------------
    def test_recap_corps_dans_la_langue_du_destinataire_anglophone(self):
        """Un francophone (le cadre) envoie le récapitulatif à un anglophone."""
        digest = self._env("fr_CA")["hosting.digest"].create({
            "name": "Recap A9", "user_ids": [(6, 0, self.usager_en.ids)]})
        corps = digest._generate_digest_html(self._donnees_recap(digest), self.usager_en)
        for attendu in ("Hello Avery Essai,", "Here is your <strong>weekly</strong> summary",
                        "Health issues", "Overdue maintenance", "d overdue",
                        "Security patches", ">OFFLINE<",
                        "For any assistance, contact your hosting provider."):
            self.assertIn(attendu, corps)
        for absent in ("Bonjour", "Problèmes de santé", "Correctifs de sécurité",
                       "HORS LIGNE", ">DOWN<", "Pour toute assistance"):
            self.assertNotIn(absent, corps)

    def test_recap_corps_dans_la_langue_du_destinataire_francophone(self):
        """Le sens inverse, qui éprouve aussi le catalogue fr_CA."""
        digest = self._env("en_CA")["hosting.digest"].create({
            "name": "Recap A9", "user_ids": [(6, 0, self.usager_fr.ids)]})
        corps = digest._generate_digest_html(self._donnees_recap(digest), self.usager_fr)
        for attendu in ("Bonjour Morgane Essai,", "Voici votre résumé <strong>hebdomadaire</strong>",
                        "Problèmes de santé", "Maintenance en retard", "j de retard",
                        "Correctifs de sécurité", ">HORS LIGNE<", "Pour toute assistance"):
            self.assertIn(attendu, corps)
        for absent in ("Hello", "Health issues", "Security patches", ">OFFLINE<", ">DOWN<"):
            self.assertNotIn(absent, corps)

    def test_recap_objet_dans_la_langue_de_chaque_destinataire(self):
        digest = self._env("fr_CA")["hosting.digest"].create({
            "name": "Recap Objet A9",
            "user_ids": [(6, 0, (self.usager_en | self.usager_fr).ids)]})
        donnees = self._donnees_recap(digest)
        dernier = self.env["mail.mail"].search([], order="id desc", limit=1).id or 0
        with patch.object(type(digest), "_generate_digest_data", return_value=donnees), \
             patch.object(type(self.env["mail.mail"]), "send", lambda self_, *a, **k: None):
            digest._send_digest()
        courriels = self.env["mail.mail"].search([("id", ">", dernier)])
        en = courriels.filtered(lambda m: m.email_to == self.usager_en.email)
        fr = courriels.filtered(lambda m: m.email_to == self.usager_fr.email)
        self.assertEqual(en.subject, "Hosting services summary - Recap Objet A9")
        self.assertEqual(fr.subject, "Résumé des services d'hébergement - Recap Objet A9")
        self.assertIn("Hello Avery Essai,", en.body_html)
        self.assertIn("Bonjour Morgane Essai,", fr.body_html)

    def test_recap_nom_par_defaut_traduisible(self):
        self.assertEqual(
            self._env("en_CA")["hosting.digest"].default_get(["name"])["name"], "Weekly digest")
        self.assertEqual(
            self._env("fr_CA")["hosting.digest"].default_get(["name"])["name"],
            "Récapitulatif hebdomadaire")

    # -- alertes de santé (hosting_service.py) -----------------------------
    def _courriel_alerte(self, env, items, alert_type):
        self.env["ir.config_parameter"].sudo().set_param(
            "hosting.health_alert_email", "operateurs.a9@example.org")
        dernier = self.env["mail.mail"].search([], order="id desc", limit=1).id or 0
        with patch.object(type(self.env["mail.mail"]), "send", lambda self_, *a, **k: None):
            env["hosting.service"]._send_health_alert_email(items, alert_type)
        return self.env["mail.mail"].search([("id", ">", dernier)], limit=1)

    def test_alerte_courriel_dans_la_langue_des_operateurs_anglophones(self):
        """Société en anglais, alerte déclenchée dans un cadre francophone."""
        self._operateurs("en_CA")
        env = self._env("fr_CA")
        service = self._service("Service Alerte A9", server_url="https://a9.example.invalid")
        panne = self._courriel_alerte(
            env, [{"service": service, "status": "down", "error": "boom"}], "down")
        self.assertEqual(panne.subject, "ALERT: 1 hosting service(s) offline")
        for attendu in ("Service alert", ">OFFLINE<", "For any assistance"):
            self.assertIn(attendu, panne.body_html)
        for absent in ("Alerte de service", "HORS LIGNE", ">DOWN<", "Pour toute assistance"):
            self.assertNotIn(absent, panne.body_html)
        lent = self._courriel_alerte(env, [{"service": service, "response_time_ms": 6000,
                                            "threshold_ms": 5000}], "slow")
        self.assertEqual(lent.subject, "WARNING: 1 slow hosting service(s)")

    def test_alerte_courriel_dans_la_langue_des_operateurs_francophones(self):
        self._operateurs("fr_CA")
        env = self._env("en_CA")
        service = self._service("Service Alerte A9", server_url="https://a9.example.invalid")
        panne = self._courriel_alerte(
            env, [{"service": service, "status": "down", "error": "boom"}], "down")
        self.assertEqual(panne.subject, "ALERTE : 1 service(s) d'hébergement hors ligne")
        for attendu in ("Alerte de service", ">HORS LIGNE<", "Pour toute assistance"):
            self.assertIn(attendu, panne.body_html)
        for absent in ("Service alert", ">OFFLINE<", ">DOWN<"):
            self.assertNotIn(absent, panne.body_html)
        retour = self._courriel_alerte(env, [{"service": service, "response_time_ms": 40}],
                                       "recovered")
        self.assertEqual(retour.subject,
                         "RÉTABLI : 1 service(s) d'hébergement de nouveau en ligne")

    def _ntfy(self, env, items, alert_type):
        with patch.object(type(self.env["hosting.ntfy"]), "send") as envoi:
            env["hosting.service"]._send_ntfy_alert(items, alert_type=alert_type)
        return envoi.call_args.kwargs

    def test_ntfy_dans_la_langue_des_operateurs(self):
        service = self._service("Service Ntfy A9")
        panne = [{"service": service, "status": "down", "error": "boom"}]
        retour = [{"service": service, "response_time_ms": 40}]

        self._operateurs("en_CA")
        envoi = self._ntfy(self._env("fr_CA"), panne, "down")
        self.assertEqual(envoi["title"], "ALERT: 1 service(s) offline")
        self.assertIn("[OFFLINE]", envoi["body"])
        self.assertEqual(self._ntfy(self._env("fr_CA"), retour, "recovered")["title"],
                         "RECOVERED: 1 service(s) back online")

        self._operateurs("fr_CA")
        envoi = self._ntfy(self._env("en_CA"), panne, "down")
        self.assertEqual(envoi["title"], "ALERTE : 1 service(s) hors ligne")
        self.assertIn("[HORS LIGNE]", envoi["body"])
        self.assertEqual(self._ntfy(self._env("en_CA"), retour, "recovered")["title"],
                         "RÉTABLI : 1 service(s) de nouveau en ligne")

    # -- activités dans l'env de l'assigné (piège 63) ----------------------
    def _expiration(self, assigne, cadre):
        service = self._service("Service Expire A9", state="active", user_id=assigne.id,
                                date_expiration=fields.Date.today() + timedelta(days=10))
        # Les autres services (prod copiée) ont déjà leur activité : seul le nôtre compte.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE hosting_service SET expiration_activity_created = true WHERE id != %s",
            (service.id,))
        self.env.invalidate_all()
        self._env(cadre)["hosting.service"]._cron_check_expirations()
        return service

    def test_activite_d_expiration_dans_la_langue_de_l_assigne_anglophone(self):
        service = self._expiration(self.usager_en, "fr_CA")
        activite, avis = self._activite_et_avis(service)
        self.assertEqual(activite.summary, "Service expires in 10 days")
        self.assertIn("will expire on", str(activite.note))
        self.assertIn("assigned to you", avis.subject or "")
        self.assertIn("Service expires in 10 days", avis.subject or "")

    def test_activite_d_expiration_dans_la_langue_de_l_assigne_francophone(self):
        service = self._expiration(self.usager_fr, "en_CA")
        activite, avis = self._activite_et_avis(service)
        self.assertEqual(activite.summary, "Le service expire dans 10 jours")
        self.assertIn("expirera le", str(activite.note))
        self.assertNotIn("assigned to you", avis.subject or "")
        self.assertIn("Le service expire dans 10 jours", avis.subject or "")

    def test_activite_de_configuration_dans_la_langue_de_l_assigne(self):
        """Gabarit « une fois » : activité dans la langue de l'assigné (anglophone),
        fil du service dans celle de qui applique (francophone), en vrai HTML."""
        gabarit_m = self.env["hosting.maintenance.template"].create({
            "name": "Gabarit Langue A9", "software_id": self.logiciel.id,
            "line_ids": [(0, 0, {
                "name": "Configurer la langue A9", "maintenance_type": "other",
                "frequency": "once", "days_after_activation": 3,
                "create_activity": True, "instructions": False,
            })],
        })
        self.assertTrue(gabarit_m.line_ids)
        service = self._service("Service Config A9", user_id=self.usager_en.id)
        service.with_env(self._env("fr_CA"))._create_maintenance_from_templates()
        activite, avis = self._activite_et_avis(service, resume="Configurer la langue A9")
        self.assertIn("Setup task for Service Config A9", str(activite.note))
        self.assertIn("assigned to you", avis.subject or "")
        fil = self.env["mail.message"].search([
            ("model", "=", "hosting.service"), ("res_id", "=", service.id),
            ("body", "ilike", "Configurer la langue A9"),
            ("message_type", "=", "notification"),
        ], limit=1)
        self.assertIn("<strong>Activités de configuration créées :</strong>", str(fil.body))
        self.assertIn("<li>Configurer la langue A9</li>", str(fil.body))
        self.assertNotIn("&lt;strong&gt;", str(fil.body))

    # -- journal d'audit des services --------------------------------------
    def test_audit_du_service_dans_la_langue_des_operateurs_anglophones(self):
        self._operateurs("en_CA")
        service = self._env("fr_CA")["hosting.service"].create({
            "name": "Service Audit A9", "partner_id": self.client.id,
            "software_id": self.logiciel.id})
        service.write({"state": "active"})
        sid, code = service.id, service.code
        service.unlink()
        lignes = self._audit("hosting.service", sid).mapped("description")
        self.assertIn(f"Service created: Service Audit A9 ({code})", lignes)
        self.assertIn("Service Service Audit A9 state: Draft → Active", lignes)
        self.assertIn(f"Service deleted: Service Audit A9 ({code})", lignes)

    def test_audit_du_service_dans_la_langue_des_operateurs_francophones(self):
        self._operateurs("fr_CA")
        service = self._env("en_CA")["hosting.service"].create({
            "name": "Service Audit A9", "partner_id": self.client.id,
            "software_id": self.logiciel.id})
        service.write({"state": "active"})
        sid, code = service.id, service.code
        service.unlink()
        lignes = self._audit("hosting.service", sid).mapped("description")
        self.assertIn(f"Service créé : Service Audit A9 ({code})", lignes)
        self.assertIn("État du service Service Audit A9 : Brouillon → Actif", lignes)
        self.assertIn(f"Service supprimé : Service Audit A9 ({code})", lignes)

    # -- sondes : messages stockés dans la langue des opérateurs ------------
    def test_sonde_http_du_travail_planifie_dans_la_langue_des_operateurs(self):
        """`_do_health_check(env=ops)` : le message d'expiration stocké sur la mesure."""
        self._operateurs("fr_CA")
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("hosting.health_alert_threshold", "1")
        service = self._service("Service Sonde A9", state="active",
                                server_url="https://sonde-a9.example.invalid")
        self._seul_actif(service)
        Service = type(self.env["hosting.service"])
        with patch("requests.get", side_effect=requests.Timeout("essai")), \
             patch.object(Service, "_is_in_maintenance_window", return_value=False), \
             patch.object(Service, "_send_health_alert_email"), \
             patch.object(Service, "_send_ntfy_alert"), \
             patch(f"{MODULE}.time.sleep"):
            self._env("en_CA")["hosting.service"]._cron_health_check()
        mesure = self.env["hosting.health.check"].search(
            [("service_id", "=", service.id)], order="id desc", limit=1)
        self.assertEqual(mesure.status, "timeout")
        self.assertEqual(mesure.error_message, "La requête a expiré après 10 secondes")

    def test_bouton_de_sonde_notifie_l_usager_et_stocke_pour_les_operateurs(self):
        self._operateurs("fr_CA")
        service = self._service("Service Bouton A9", state="active",
                                server_url="https://bouton-a9.example.invalid")
        with patch("requests.get", side_effect=requests.Timeout("essai")):
            avis = service.with_env(self._env("en_CA")).action_run_health_check()
        self.assertEqual(avis["params"]["title"], "Health check finished")
        self.assertEqual(avis["params"]["message"], "Service timed out")
        mesure = self.env["hosting.health.check"].search(
            [("service_id", "=", service.id)], order="id desc", limit=1)
        self.assertEqual(mesure.error_message, "La requête a expiré après 10 secondes")

    def test_sonde_du_reveil_dans_la_langue_des_operateurs(self):
        self._operateurs("en_CA")
        service = self._service("Service Reveil A9", health_kind="softphone_wake")
        Users = type(self.env["res.users"])
        cadre = service.with_env(self._env("fr_CA"))
        if hasattr(Users, "softphone_wake_selftest"):
            with patch.object(Users, "softphone_wake_selftest",
                              side_effect=RuntimeError("panne d'essai")):
                erreur = cadre._do_softphone_wake_check(force=True)[3]
            self.assertTrue(erreur.startswith("The probe itself raised: RuntimeError"), erreur)
        else:
            erreur = cadre._do_softphone_wake_check(force=True)[3]
            self.assertEqual(erreur, "bf_softphone is not installed on this tenant: nothing to test.")

    def _sonde_avis(self, operateurs, cadre, mesure=False):
        if ("nextcloud.calendar.sync.config" not in self.env
                or "bf_change_notice_due" not in self.env["calendar.event"]._fields):
            raise unittest.SkipTest("calendar_nextcloud_sync et bf_calendar_invite requis")
        self._operateurs(operateurs)
        projet = self.env["project.project"].create({"name": "Constats A9"})
        self.env["nextcloud.calendar.sync.config"].create({
            "name": "Agenda A9", "nextcloud_base_url": "https://nextcloud.example.invalid",
            "nextcloud_user": "essai", "caldav_path": "/remote.php/dav/calendars/essai/a9/",
        })
        service = self._service("Avis A9", health_kind="calendar_notices",
                                watchdog_project_id=projet.id)
        Config = type(self.env["nextcloud.calendar.sync.config"])
        releve = {"mesure": mesure, "erreur": "injoignable", "ecarts": [], "vus": 3}
        Event = type(self.env["calendar.event"])
        # Le registre (moitié 1) est vidé : les rencontres de la prod copiée ne
        # doivent ni changer le statut ni allonger le constat (coupé à 500 car.).
        with patch.object(Config, "bf_ecarts_non_rapatries", return_value=releve), \
             patch.object(Event, "_bf_notice_overdue", lambda self_: self_.browse()):
            premier = service.with_env(self._env(cadre))._do_calendar_notices_check(force=True)
            service.with_env(self._env(cadre))._do_calendar_notices_check(force=True)
        tache = self.env["project.task"].search([("project_id", "=", projet.id)])
        commentaire = tache.message_ids.filtered(lambda m: "Avis A9" in str(m.body))[:1]
        return premier, tache, commentaire

    def test_sonde_des_avis_dans_la_langue_des_operateurs_anglophones(self):
        premier, tache, commentaire = self._sonde_avis("en_CA", "fr_CA")
        self.assertEqual(premier[0], "down")
        self.assertIn("unable to re-read the remote copy (injoignable)", premier[3])
        self.assertNotIn("impossible de relire", premier[3])
        self.assertEqual(len(tache), 1)
        self.assertIn("Appointment change notices were not sent", tache.name)
        # Second passage : le constat s'ajoute en commentaire, balises comprises.
        self.assertIn("<b>Avis A9</b>", str(commentaire.body))
        self.assertNotIn("&lt;b&gt;", str(commentaire.body))

    def test_sonde_des_avis_dans_la_langue_des_operateurs_francophones(self):
        premier, tache, _commentaire = self._sonde_avis("fr_CA", "en_CA")
        self.assertIn("impossible de relire la copie distante (injoignable)", premier[3])
        self.assertIn("Des avis de modification de rendez-vous ne sont pas partis", tache.name)

    # -- tableau de bord (hosting_dashboard.py) -----------------------------
    def _dernier_audit_du_tableau(self):
        return self.env["hosting.audit.log"].sudo().search(
            [("res_model", "=", "hosting.dashboard")], order="id desc", limit=1)

    def test_tableau_audit_de_la_sonde_manuelle_dans_la_langue_des_operateurs(self):
        service = self._service("Service Tableau A9", state="active",
                                server_url="https://tableau-a9.example.invalid")
        self._seul_actif(service)
        for operateurs, cadre, attendu in (
            ("en_CA", "fr_CA", "Manual health check started from the dashboard"),
            ("fr_CA", "en_CA", "Vérification de santé manuelle lancée depuis le tableau de bord"),
        ):
            with self.subTest(operateurs=operateurs):
                self._operateurs(operateurs)
                with patch("requests.get", side_effect=requests.Timeout("essai")):
                    self._env(cadre)["hosting.dashboard"].action_run_health_checks()
                self.assertEqual(self._dernier_audit_du_tableau().description, attendu)

    def test_tableau_erreur_de_sonde_stockee_traduite(self):
        """Le message stocké par la sonde du tableau n'est plus l'anglais en dur."""
        self._operateurs("fr_CA")
        service = self._service("Service Tableau A9", state="active",
                                server_url="https://tableau-a9.example.invalid")
        self._seul_actif(service)
        with patch("requests.get", side_effect=requests.Timeout("essai")):
            self._env("fr_CA")["hosting.dashboard"].action_run_health_checks()
        mesure = self.env["hosting.health.check"].search(
            [("service_id", "=", service.id)], order="id desc", limit=1)
        self.assertEqual(mesure.error_message, "La requête a expiré après 10 secondes")

    def test_tableau_actualisation_complete(self):
        """Journal dans la langue des opérateurs (anglais), erreur rendue dans celle
        de l'usager qui clique (français)."""
        self._operateurs("en_CA")
        service = self._service("Service Tableau A9", state="active",
                                server_url="https://tableau-a9.example.invalid")
        self._seul_actif(service)
        Software = type(self.env["hosting.software"])
        Service = type(self.env["hosting.service"])
        with patch("requests.get", side_effect=requests.Timeout("essai")), \
             patch.object(Software, "_check_version", lambda self_: None), \
             patch.object(Service, "_check_docker_versions",
                          side_effect=RuntimeError("panne docker")):
            bilan = self._env("fr_CA")["hosting.dashboard"].action_refresh_all()
        self.assertEqual(self._dernier_audit_du_tableau().description,
                         "Full dashboard refresh (health + versions + Docker)")
        self.assertIn("Erreur de vérification Docker : panne docker", bilan["errors"])

    # -- parc (hosting_endpoint.py) ------------------------------------------
    def test_etiquette_fin_de_support_stockee_dans_la_langue_de_la_societe(self):
        for operateurs, cadre, attendu in (
            ("fr_CA", "en_CA", "EOL Windows 10 (oct. 2025)"),
            ("en_CA", "fr_CA", "Windows 10 EOL (Oct. 2025)"),
        ):
            with self.subTest(operateurs=operateurs):
                self._operateurs(operateurs)
                poste = self._env(cadre)["hosting.endpoint"].create({
                    "name": f"Poste EOL {operateurs}", "partner_id": self.client.id,
                    "os": "windows_10"})
                self.assertEqual(poste.os_eol_tag, attendu)

    def test_audit_du_champ_sensible_dans_la_langue_des_operateurs(self):
        for operateurs, cadre, attendu in (
            ("en_CA", "fr_CA", "Sensitive field changed on endpoint"),
            ("fr_CA", "en_CA", "Champ sensible modifié sur le poste"),
        ):
            with self.subTest(operateurs=operateurs):
                self._operateurs(operateurs)
                poste = self._env(cadre)["hosting.endpoint"].create({
                    "name": f"Poste BitLocker {operateurs}", "partner_id": self.client.id,
                    "bitlocker_recovery_key": "123456-ESSAI"})
                lignes = self._audit("hosting.endpoint", poste.id)
                self.assertTrue(lignes)
                self.assertTrue(lignes[-1].description.startswith(attendu),
                                lignes[-1].description)

    def _courriel_garantie(self, responsable, cadre):
        self._service("Service Parc A9", user_id=responsable.id)
        self.env["hosting.endpoint"].create({
            "name": "Portable Garantie A9", "partner_id": self.client.id,
            "endpoint_type": "laptop", "lifecycle_state": "deployed",
            "warranty_end": fields.Date.today() + timedelta(days=30)})
        dernier = self.env["mail.mail"].search([], order="id desc", limit=1).id or 0
        with patch.object(type(self.env["mail.mail"]), "send", lambda self_, *a, **k: None):
            self._env(cadre)["hosting.endpoint"]._cron_warranty_alerts()
        return self.env["mail.mail"].search(
            [("id", ">", dernier), ("email_to", "=", responsable.email)], limit=1)

    def test_courriel_de_garantie_dans_la_langue_du_responsable_anglophone(self):
        self._operateurs("fr_CA")
        courriel = self._courriel_garantie(self.usager_en, "fr_CA")
        self.assertEqual(courriel.subject,
                         "[Fleet] Client Langue A9 — 1 endpoint(s) with warranty ≤ 90 d")
        for attendu in ("Hello Avery Essai,", "Laptop", "30 d<", "Warranty end", "— Hosting module"):
            self.assertIn(attendu, courriel.body_html)
        for absent in ("Bonjour", "Portable<", "Fin de garantie"):
            self.assertNotIn(absent, courriel.body_html)

    def test_courriel_de_garantie_dans_la_langue_du_responsable_francophone(self):
        self._operateurs("en_CA")
        courriel = self._courriel_garantie(self.usager_fr, "en_CA")
        self.assertEqual(courriel.subject,
                         "[Parc] Client Langue A9 — 1 poste(s) garantie ≤ 90 j")
        for attendu in ("Bonjour Morgane Essai,", ">Portable<", "30 j<", "Fin de garantie",
                        "— Module Hébergement"):
            self.assertIn(attendu, courriel.body_html)
        for absent in ("Hello", "Laptop", "Warranty end"):
            self.assertNotIn(absent, courriel.body_html)

    # -- serveurs (hosting_server.py) ----------------------------------------
    def test_audit_du_serveur_dans_la_langue_des_operateurs(self):
        for operateurs, cadre, attendu in (
            ("en_CA", "fr_CA", "Server Serveur A9 en_CA: state 'Active' → 'Decommissioned'"),
            ("fr_CA", "en_CA", "Serveur Serveur A9 fr_CA : state 'Actif' → 'Décommissionné'"),
        ):
            with self.subTest(operateurs=operateurs):
                self._operateurs(operateurs)
                serveur = self._env(cadre)["hosting.server"].create({
                    "name": f"Serveur A9 {operateurs}", "code": f"SA9{operateurs[:2]}",
                    "hostname": f"serveur-a9-{operateurs[:2]}.example.invalid"})
                serveur.write({"state": "decommissioned"})
                self.assertIn(attendu, self._audit("hosting.server", serveur.id).mapped("description"))

    def test_erreurs_de_validation_dans_la_langue_de_l_usager(self):
        for cadre, nom_hote, url in (
            ("en_CA", "contains characters that are not allowed", "cannot point to localhost"),
            ("fr_CA", "contient des caractères non autorisés", "ne peut pas pointer vers localhost"),
        ):
            with self.subTest(cadre=cadre):
                env = self._env(cadre)
                with self.assertRaisesRegex(ValidationError, nom_hote), self.env.cr.savepoint():
                    env["hosting.server"].create({
                        "name": "Serveur invalide", "code": f"SINV{cadre}",
                        "hostname": "mauvais hote!"})
                with self.assertRaisesRegex(ValidationError, url), self.env.cr.savepoint():
                    env["hosting.service"].create({
                        "name": "Service invalide", "partner_id": self.client.id,
                        "software_id": self.logiciel.id, "server_url": "http://localhost"})

    # -- gabarit de courriel (hosting_email_template.py) ---------------------
    def test_pied_et_pastille_dans_la_langue_de_la_societe_passee(self):
        societe = self.env.company
        societe.write({
            "brand_privacy_url": "https://exemple.test/vie-privee",
            "brand_terms_url": "https://exemple.test/conditions",
            "brand_email_footer_html": False,  # celui de la prod porte ses propres liens
        })
        en = societe.with_context(lang="en_CA")
        fr = societe.with_context(lang="fr_CA")
        corps_en = gabarit.get_email_wrapper("T", "<p>c</p>", company=en)
        corps_fr = gabarit.get_email_wrapper("T", "<p>c</p>", company=fr)
        self.assertIn('vie-privee" style="color:#9CA3AF; text-decoration:underline;">Privacy<', corps_en)
        self.assertIn('conditions" style="color:#9CA3AF; text-decoration:underline;">Terms<', corps_en)
        self.assertIn(">Confidentialité<", corps_fr)
        self.assertIn('conditions" style="color:#9CA3AF; text-decoration:underline;">Conditions<', corps_fr)
        self.assertIn("For any assistance", gabarit.get_contact_footer(company=en))
        self.assertIn("Pour toute assistance", gabarit.get_contact_footer(company=fr))
        pastille = gabarit.get_status_badge("down", "small", company=fr, label="Hors ligne")
        self.assertIn(">HORS LIGNE<", pastille)
        self.assertNotIn("DOWN", pastille)
        self.assertIn(">DOWN<", gabarit.get_status_badge("down", "small", company=fr))

    # -- noms d'action ---------------------------------------------------------
    def test_noms_d_action_dans_la_langue_de_l_usager(self):
        service = self._service("Service Action A9")
        groupe = self.env["hosting.endpoint.group"].create(
            {"name": "Groupe A9", "partner_id": self.client.id})
        poste = self.env["hosting.endpoint"].create(
            {"name": "Poste Action A9", "partner_id": self.client.id})
        for cadre, attendus in (
            ("en_CA", ("Health history - Service Action A9", "Endpoints — Groupe A9",
                       "IT fleet - Client Langue A9", "Expiring in 30 days", "Licenses — ")),
            ("fr_CA", ("Historique de santé - Service Action A9", "Postes — Groupe A9",
                       "Parc informatique - Client Langue A9", "Expirant dans 30 jours",
                       "Licences — ")),
        ):
            with self.subTest(cadre=cadre):
                env = self._env(cadre)
                self.assertEqual(service.with_env(env).action_view_health_history()["name"], attendus[0])
                self.assertEqual(groupe.with_env(env).action_view_endpoints()["name"], attendus[1])
                self.assertEqual(self.client.with_env(env).action_view_hosting_endpoints()["name"],
                                 attendus[2])
                self.assertEqual(env["hosting.dashboard"].action_view_expiring_30()["name"], attendus[3])
                self.assertTrue(poste.with_env(env).action_view_licenses()["name"].startswith(attendus[4]))

    # -- correctifs de langue, second passage --------------------
    def test_courriel_de_garantie_balise_strong_rendue_et_noms_echappes(self):
        """hosting_endpoint.py, `_cron_warranty_alerts` : la phrase d'intro porte une
        vraie balise <strong>, et les noms (client, responsable) y sont échappés.

        Avant : `env._("…<strong>%s</strong>…", _esc(partner.name))` ; un Markup en
        argument de `_()` fait échapper par Odoo 18 toute la phrase traduite, et la
        balise partait en clair (`&lt;strong&gt;`) dans le courriel.
        """
        client = self.env["res.partner"].create({
            "name": "Parc <Dupont> & Fils", "is_company": True, "has_sla": True})
        responsable = new_test_user(
            self.env, login="langue_a9_strong", name="Avery & <Essai>", lang="en_CA",
            groups="base.group_user,hosting_management.group_hosting_user",
            email="avery.strong.a9@example.org", notification_type="inbox")
        self.env["hosting.service"].create({
            "name": "Service Parc Strong A9", "partner_id": client.id,
            "software_id": self.logiciel.id, "user_id": responsable.id})
        self.env["hosting.endpoint"].create({
            "name": "Portable Strong A9", "partner_id": client.id,
            "endpoint_type": "laptop", "lifecycle_state": "deployed",
            "warranty_end": fields.Date.today() + timedelta(days=30)})
        dernier = self.env["mail.mail"].search([], order="id desc", limit=1).id or 0
        with patch.object(type(self.env["mail.mail"]), "send", lambda self_, *a, **k: None):
            self._env("fr_CA")["hosting.endpoint"]._cron_warranty_alerts()
        courriel = self.env["mail.mail"].search(
            [("id", ">", dernier), ("email_to", "=", responsable.email)], limit=1)
        self.assertTrue(courriel)
        corps = courriel.body_html
        self.assertIn(
            "Here are the endpoints of the <strong>Parc &lt;Dupont&gt; &amp; Fils</strong> fleet",
            corps)
        self.assertNotIn("&lt;strong&gt;", corps)
        self.assertIn("<p>Hello Avery &amp; &lt;Essai&gt;,</p>", corps)
        self.assertNotIn("<Dupont>", corps)
        self.assertNotIn("<Essai>", corps)

    def _societe_en_langue_inactive(self):
        """Le partenaire de la société porte une langue DÉSACTIVÉE (de_DE).

        Écrite en SQL : le champ `lang` refuse une langue inactive à l'écriture, mais une
        langue désactivée après coup reste au partenaire. fr_CA n'est jamais désactivée
        (piège 39).
        """
        allemand = self.env["res.lang"].with_context(active_test=False).search(
            [("code", "=", "de_DE")])
        self.assertTrue(allemand)
        if allemand.active:
            allemand.active = False
        self.env.flush_all()
        self.env.cr.execute("UPDATE res_partner SET lang = 'de_DE' WHERE id = %s",
                            (self.env.company.partner_id.id,))
        self.env.invalidate_all()
        self.assertNotIn("de_DE", dict(self.env["res.lang"].get_installed()))
        self.assertEqual(self.env.company.partner_id.lang, "de_DE")

    def test_langue_inactive_de_la_societe_creation_de_poste(self):
        """hosting_endpoint.py, `_company_lang` : la langue de la société n'est prise
        que si elle est installée, sinon en_US.

        Avant : `company.partner_id.lang or …` sans vérification ; `with_context(lang=
        'de_DE').env._` levait « Invalid language code » au calcul de `os_eol_tag` et à
        l'audit du champ sensible, donc à la création d'un poste (et à la synchro Action1).
        """
        self._societe_en_langue_inactive()
        poste = self._env("fr_CA")["hosting.endpoint"].create({
            "name": "Poste Langue Inactive A9", "partner_id": self.client.id,
            "os": "windows_10", "bitlocker_recovery_key": "123456-ESSAI"})
        self.assertEqual(poste.os_eol_tag, "Windows 10 EOL (Oct. 2025)")
        lignes = self._audit("hosting.endpoint", poste.id)
        self.assertTrue(lignes)
        self.assertTrue(lignes[-1].description.startswith("Sensitive field changed on endpoint"),
                        lignes[-1].description)

    def test_langue_inactive_de_la_societe_modification_de_serveur(self):
        """hosting_server.py, `write` : journal d'audit par `reader_env`.

        Avant : `with_context(lang=company.partner_id.lang …)` puis
        `_description_selection(env)` levait « Invalid language code ».
        """
        serveur = self._env("fr_CA")["hosting.server"].create({
            "name": "Serveur Inactif A9", "code": "SA9INA",
            "hostname": "serveur-a9-inactif.example.invalid"})
        self._societe_en_langue_inactive()
        serveur.write({"state": "decommissioned"})
        self.assertIn("Server Serveur Inactif A9: state 'Active' → 'Decommissioned'",
                      self._audit("hosting.server", serveur.id).mapped("description"))

    def test_langue_inactive_de_la_societe_actualiser_tout(self):
        """hosting_dashboard.py, `_audit_lang` : « Actualiser tout » ne lève plus.

        Avant : `company.partner_id.lang or …` sans vérification ; le journal
        `journal.env._(…)` levait « Invalid language code ».
        """
        service = self._service("Service Tableau Inactif A9", state="active",
                                server_url="https://tableau-inactif-a9.example.invalid")
        self._seul_actif(service)
        self._societe_en_langue_inactive()
        Software = type(self.env["hosting.software"])
        Service = type(self.env["hosting.service"])
        with patch("requests.get", side_effect=requests.Timeout("essai")), \
             patch.object(Software, "_check_version", lambda self_: None), \
             patch.object(Service, "_check_docker_versions", lambda self_: None):
            self._env("fr_CA")["hosting.dashboard"].action_refresh_all()
        self.assertEqual(self._dernier_audit_du_tableau().description,
                         "Full dashboard refresh (health + versions + Docker)")

    def test_langue_inactive_au_recapitulatif_retombe_sur_une_langue_installee(self):
        """hosting_digest.py, `_digest_lang` : la langue du destinataire, puis celle de
        la société, ne sont prises que si elles sont installées.

        Avant : `recipient.lang or company.partner_id.lang or …` sans vérification ; une
        langue inactive (au destinataire, ou à la société pour un destinataire sans
        langue) faisait lever `digest.env._` au rendu du récapitulatif.
        """
        digest = self._env("fr_CA")["hosting.digest"].create({
            "name": "Recap Inactif A9", "user_ids": [(6, 0, self.usager_en.ids)]})
        donnees = self._donnees_recap(digest)
        self._societe_en_langue_inactive()
        # Destinataire sans langue : repli sur la société (inactive), donc en_US.
        self.env.cr.execute("UPDATE res_partner SET lang = NULL WHERE id = %s",
                            (self.usager_en.partner_id.id,))
        self.env.invalidate_all()
        self.assertFalse(self.usager_en.lang)
        corps = digest._generate_digest_html(donnees, self.usager_en)
        self.assertIn("Hello Avery Essai,", corps)
        # Destinataire en langue inactive, société francophone : le français de la société.
        self.env.cr.execute("UPDATE res_partner SET lang = 'de_DE' WHERE id = %s",
                            (self.usager_en.partner_id.id,))
        self.env.invalidate_all()
        self._operateurs("fr_CA")
        corps = digest._generate_digest_html(donnees, self.usager_en)
        self.assertIn("Bonjour Avery Essai,", corps)

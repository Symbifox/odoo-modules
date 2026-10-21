# -*- coding: utf-8 -*-
import re
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.service.model import get_public_method
from odoo.tests import HttpCase, new_test_user, tagged

GESTION = "hosting_management.group_hosting_manager"


@tagged("post_install", "-at_install")
class TestStatusPage(HttpCase):
    """La page d'état d'un client hébergé ."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("hosting.health_alert_min_consecutive", "2")
        logiciel = cls.env["hosting.software"].create({
            "name": "Nuage Essai", "code": "nuage-essai-page", "software_type": "self_hosted"})
        version = cls.env["hosting.software.version"].create({
            "software_id": logiciel.id, "version": "31.4.1", "support_status": "supported"})
        ancienne = cls.env["hosting.software.version"].create({
            "software_id": logiciel.id, "version": "31.4.0", "support_status": "supported"})
        Partner = cls.env["res.partner"]
        cls.client_a = Partner.create({"name": "Érablière Alpha Essai", "is_company": True})
        cls.contact_a = Partner.create({"name": "Contact Alpha", "parent_id": cls.client_a.id})
        cls.client_b = Partner.create({"name": "Brasserie Bêta Essai", "is_company": True})
        Service = cls.env["hosting.service"]
        base = {"software_id": logiciel.id, "installed_version_id": version.id, "state": "active"}
        cls.svc_a = Service.create(dict(base, name="Nuage Alpha", partner_id=cls.client_a.id,
                                        server_url="https://nuage.alpha-essai.example",
                                        docker_container="conteneur-secret-alpha"))
        cls.svc_a_contact = Service.create(dict(base, name="Coffre Alpha", partner_id=cls.client_a.id,
                                                server_url="https://coffre.alpha-essai.example"))
        # Rattaché à un CONTACT, puis à une société ENFANT : jamais sur la page de la société.
        cls.svc_contact = Service.create(dict(base, name="Contact Gamma", partner_id=cls.contact_a.id,
                                              server_url="https://gamma.contact-essai.example"))
        cls.filiale = Partner.create({"name": "Filiale Delta", "is_company": True, "parent_id": cls.client_a.id})
        cls.svc_filiale = Service.create(dict(base, name="Filiale Delta Nuage", partner_id=cls.filiale.id,
                                              server_url="https://nuage.delta-essai.example"))
        cls.svc_a_sans_sonde = Service.create(dict(base, name="Outil Alpha", partner_id=cls.client_a.id))
        cls.svc_a_inactif = Service.create(dict(base, name="Ancien Alpha", partner_id=cls.client_a.id,
                                                state="cancelled"))
        cls.svc_b = Service.create(dict(base, name="Nuage Bêta", partner_id=cls.client_b.id,
                                        server_url="https://nuage.beta-essai.example"))
        maintenant = fields.Datetime.now()
        for svc in (cls.svc_a, cls.svc_a_contact, cls.svc_b):
            cls._releve(svc, "up", maintenant - timedelta(minutes=2))
        cls.env["hosting.health.check"].create({
            "service_id": cls.svc_a.id, "status": "degraded", "check_date": maintenant - timedelta(minutes=1),
            "error_message": "erreur-secrete-amont 10.9.8.7", "http_status_code": 502})
        cls.env["hosting.update.log"].create({
            "service_id": cls.svc_a.id, "update_date": maintenant - timedelta(days=3),
            "from_version_id": ancienne.id, "to_version_id": version.id, "success": True,
            "notes": "note-interne-secrete"})
        cls.env["hosting.update.log"].create({
            "service_id": cls.svc_b.id, "update_date": maintenant - timedelta(days=2),
            "from_version_id": ancienne.id, "to_version_id": version.id, "success": True})
        cls.client_a.action_hosting_status_publish()
        cls.client_b.action_hosting_status_publish()
        cls.jeton_a = cls.client_a.hosting_status_token
        cls.jeton_b = cls.client_b.hosting_status_token

    @classmethod
    def _releve(cls, service, statut, quand):
        return cls.env["hosting.health.check"].create(
            {"service_id": service.id, "status": statut, "check_date": quand})

    def _get(self, partner_id, jeton):
        return self.url_open("/etat/%s/%s" % (partner_id, jeton), allow_redirects=False)

    # ------------------------------------------------------------------
    # La page
    # ------------------------------------------------------------------

    def test_la_page_du_client_et_rien_d_autre(self):
        r = self._get(self.client_a.id, self.jeton_a)
        self.assertEqual(r.status_code, 200)
        page = r.text
        for attendu in ("Nuage Alpha", "Coffre Alpha", "Outil Alpha", "nuage.alpha-essai.example",
                        "31.4.1", "Érablière Alpha Essai"):
            self.assertIn(attendu, page)
        # Rien de l'autre client, qui partage le logiciel et la version.
        for interdit in ("Bêta", "beta-essai", self.client_b.name, self.jeton_b,
                         "Contact Gamma", "contact-essai", "Filiale Delta", "delta-essai"):
            self.assertNotIn(interdit, page)
        # Rien d'interne : codes, conteneur, notes, message et code d'erreur de la sonde.
        for interdit in (self.svc_a.code, self.svc_b.code, "conteneur-secret-alpha",
                         "note-interne-secrete", "erreur-secrete-amont", "10.9.8.7", "Ancien Alpha"):
            self.assertNotIn(interdit, page)
        self.assertFalse(re.search(r"HST-\d", page))
        # Rien d'externe ni de script : la mise en page du site chargeait le script de
        # statistiques, qui envoie l'adresse de la page (jeton compris), et des polices.
        self.assertNotIn("<script", page)
        self.assertFalse(re.search(r"(?:src|href)=\"(?:https?:)?//", page), "ressource externe")

    def test_les_en_tetes(self):
        for r in (self._get(self.client_a.id, self.jeton_a), self._get(self.client_a.id, "x" * 43)):
            with self.subTest(statut=r.status_code):
                self.assertEqual(r.headers.get("Referrer-Policy"), "no-referrer")
                self.assertIn("noindex", r.headers.get("X-Robots-Tag", ""))
                self.assertIn("no-store", r.headers.get("Cache-Control", ""))

    def test_en_francais_et_au_fuseau_du_client(self):
        self.client_a.write({"lang": "fr_CA", "tz": "America/Toronto"})
        journal = self.env["hosting.update.log"].search([("service_id", "=", self.svc_a.id)])
        # 01 h 30 UTC = 21 h 30 la veille à Toronto.
        journal.update_date = (fields.Datetime.now() - timedelta(days=3)).replace(hour=1, minute=30)
        veille = (journal.update_date - timedelta(days=1)).date().isoformat()
        page = self._get(self.client_a.id, self.jeton_a).text
        self.assertIn("<title>État de vos services</title>", page)
        self.assertIn('lang="fr-CA"', page)
        self.assertIn(veille, page)

    def test_un_seul_404_pour_toutes_les_portes_fermees(self):
        cas = {
            "jeton faux": (self.client_a.id, "x" * 43),
            "jeton de l'autre client": (self.client_a.id, self.jeton_b),
            "jeton non ascii": (self.client_a.id, "é" * 43),
            "client inconnu": (10 ** 9, self.jeton_a),
            "contact du client": (self.contact_a.id, self.jeton_a),
        }
        for nom, (pid, jeton) in cas.items():
            with self.subTest(nom):
                self.assertEqual(self._get(pid, jeton).status_code, 404)
        # Un contact publié par écriture directe (hors du bouton) n'a toujours pas de page.
        jeton_contact = "c" * 43
        self.contact_a.sudo().write({"hosting_status_published": True, "hosting_status_token": jeton_contact})
        self.assertEqual(self._get(self.contact_a.id, jeton_contact).status_code, 404, "contact publié")
        self.client_a.action_hosting_status_unpublish()
        self.assertEqual(self._get(self.client_a.id, self.jeton_a).status_code, 404, "dépubliée")
        self.client_a.action_hosting_status_publish()
        ancien = self.jeton_a
        self.client_a.action_hosting_status_regenerate_token()
        self.assertEqual(self._get(self.client_a.id, ancien).status_code, 404, "ancienne adresse")
        self.assertEqual(self._get(self.client_a.id, self.client_a.hosting_status_token).status_code, 200)
        self.client_a.active = False
        self.assertEqual(self._get(self.client_a.id, self.client_a.hosting_status_token).status_code, 404)

    # ------------------------------------------------------------------
    # L'état lissé
    # ------------------------------------------------------------------

    def _etat(self, service):
        return self.client_a._hosting_status_etat(
            service, fields.Datetime.now(), self.client_a._hosting_status_seuil())

    def test_un_seul_releve_en_defaut_ne_met_pas_au_rouge(self):
        self.assertEqual(self._etat(self.svc_a), "up")

    def test_deux_releves_en_defaut(self):
        maintenant = fields.Datetime.now()
        self._releve(self.svc_a, "degraded", maintenant)
        self.svc_a.invalidate_recordset()
        self.assertEqual(self._etat(self.svc_a), "error")
        self._releve(self.svc_a_contact, "timeout", maintenant - timedelta(seconds=30))
        self._releve(self.svc_a_contact, "down", maintenant)
        self.svc_a_contact.invalidate_recordset()
        self.assertEqual(self._etat(self.svc_a_contact), "unreachable")

    def test_sonde_muette_service_non_surveille_maintenance(self):
        self.assertEqual(self._etat(self.svc_a_sans_sonde), "not_monitored")
        vieux = self.env["hosting.service"].create({
            "name": "Vieux Alpha", "partner_id": self.client_a.id, "software_id": self.svc_a.software_id.id,
            "server_url": "https://vieux.alpha-essai.example", "state": "active"})
        self._releve(vieux, "up", fields.Datetime.now() - timedelta(minutes=40))
        vieux.invalidate_recordset()
        self.assertEqual(self._etat(vieux), "unknown")
        self.svc_a.health_silence_until = fields.Datetime.now() + timedelta(minutes=10)
        self.assertEqual(self._etat(self.svc_a), "maintenance")

    # ------------------------------------------------------------------
    # Les droits
    # ------------------------------------------------------------------

    def test_publier_et_copier_reserves_aux_gestionnaires(self):
        interne = new_test_user(self.env, login="essai_page_interne", groups="base.group_user")
        usager = new_test_user(self.env, login="essai_page_hebergement",
                               groups="base.group_user,hosting_management.group_hosting_user")
        portail = new_test_user(self.env, login="essai_page_portail", groups="base.group_portal")
        for u in (interne, usager, portail):
            for action in ("action_hosting_status_publish", "action_hosting_status_unpublish",
                           "action_hosting_status_regenerate_token", "action_hosting_status_copy_link"):
                with self.subTest(user=u.login, action=action), self.assertRaises(AccessError):
                    getattr(self.client_a.with_user(u), action)()
            with self.subTest(user=u.login, champ="jeton"), self.assertRaises(AccessError):
                self.client_a.with_user(u).read(["hosting_status_token"])
        # Les méthodes qui préparent la page ne sont pas appelables par RPC.
        Model = self.env["res.partner"].with_user(portail)
        for nom in ("_hosting_status_valeurs", "_hosting_status_etat", "_hosting_status_services",
                    "_hosting_status_url", "_hosting_status_seuil"):
            with self.subTest(methode=nom), self.assertRaises(AccessError):
                get_public_method(Model, nom)

    def test_creer_une_page_publiee_reserve_aux_gestionnaires(self):
        """Le `groups=` d'un champ n'est pas contrôlé à la création par Odoo 18."""
        contacts = new_test_user(self.env, login="essai_page_contacts",
                                 groups="base.group_user,base.group_partner_manager")
        Partner = self.env["res.partner"].with_user(contacts)
        jeton = "Z" * 43
        with self.assertRaises(AccessError):
            Partner.create({"name": "Leurre", "is_company": True,
                            "hosting_status_published": True, "hosting_status_token": jeton})
        with self.assertRaises(AccessError):
            Partner.with_context(default_hosting_status_published=True,
                                 default_hosting_status_token=jeton).create({"name": "Leurre 2", "is_company": True})
        resultat = Partner.load(["name", "is_company", "hosting_status_published", "hosting_status_token"],
                                [["Leurre 3", "1", "1", jeton]])
        self.assertFalse(resultat["ids"])
        self.assertFalse(self.env["res.partner"].search_count([("name", "like", "Leurre")]))
        # Une création ordinaire passe toujours.
        self.assertTrue(Partner.create({"name": "Société ordinaire", "is_company": True}))

    def test_la_page_appartient_au_client_commercial(self):
        with self.assertRaises(ValidationError):
            self.contact_a.action_hosting_status_publish()
        gestionnaire = new_test_user(self.env, login="essai_page_gestion", groups="base.group_user," + GESTION)
        action = self.client_a.with_user(gestionnaire).action_hosting_status_copy_link()
        self.assertIn("/etat/%s/%s" % (self.client_a.id, self.client_a.hosting_status_token),
                      action["params"]["message"])

    def test_jeton_ecrit_a_la_main_refuse(self):
        with self.assertRaises(ValidationError):
            self.client_a.hosting_status_token = "1234"

    def test_la_fiche_contact_s_ouvre_sans_le_groupe(self):
        interne = new_test_user(self.env, login="essai_page_fiche", groups="base.group_user")
        Model = self.client_a.with_user(interne)
        arch = Model.get_views([(False, "form")])["views"]["form"]["arch"]
        noms = set(re.findall(r'<field name="([^"]+)"', arch)) & set(Model._fields)
        self.assertNotIn("hosting_status_published", noms)
        self.assertNotIn("hosting_status_token", noms)
        # Ni le bouton ni la page n'arrivent au client web ; rien d'autre du module n'y est.
        self.assertFalse([n for n in noms if Model._fields[n]._module == "bf_hosting_status"])

    # ------------------------------------------------------------------
    # Les fenêtres de maintenance
    # ------------------------------------------------------------------

    def _fenetre(self, debut, fin, etat="announced", nom="Mise à niveau du serveur partagé"):
        return self.env["hosting.maintenance.window"].create({
            "name": nom, "date_start": debut, "date_end": fin, "state": etat,
            "impact": "Environ 45 minutes d'interruption.",
            "service_ids": [(6, 0, (self.svc_a | self.svc_b).ids)]})

    def test_une_fenetre_partagee_ne_cite_que_les_services_du_client(self):
        maintenant = fields.Datetime.now()
        self._fenetre(maintenant + timedelta(days=2), maintenant + timedelta(days=2, hours=2))
        page = self._get(self.client_a.id, self.jeton_a).text
        self.assertIn("Mise à niveau du serveur partagé", page)
        self.assertIn("Environ 45 minutes", page)
        bloc = page[page.index("Mise à niveau du serveur partagé"):]
        bloc = bloc[:bloc.index("</div>\n                        </div>") if "</div>\n                        </div>" in bloc else 600]
        self.assertIn("Nuage Alpha", bloc)
        for interdit in ("Bêta", "beta-essai"):
            self.assertNotIn(interdit, page)

    def test_brouillon_et_annulee_ne_sortent_pas(self):
        maintenant = fields.Datetime.now()
        self._fenetre(maintenant + timedelta(days=2), maintenant + timedelta(days=2, hours=2),
                      etat="draft", nom="Fenêtre brouillon")
        self._fenetre(maintenant + timedelta(days=2), maintenant + timedelta(days=2, hours=2),
                      etat="cancelled", nom="Fenêtre annulée")
        page = self._get(self.client_a.id, self.jeton_a).text
        self.assertNotIn("Fenêtre brouillon", page)
        self.assertNotIn("Fenêtre annulée", page)

    def test_pendant_la_fenetre_le_service_est_en_maintenance(self):
        maintenant = fields.Datetime.now()
        self._fenetre(maintenant - timedelta(minutes=10), maintenant + timedelta(minutes=50))
        self._releve(self.svc_a, "degraded", maintenant)
        self.svc_a.invalidate_recordset()
        lignes = {l["name"]: l["state"] for l in self.client_a._hosting_status_valeurs()["services"]}
        self.assertEqual(lignes["Nuage Alpha"], "maintenance")
        self.assertEqual(lignes["Outil Alpha"], "not_monitored", "hors fenêtre : inchangé")

    def test_une_fenetre_finie_passe_a_l_historique(self):
        maintenant = fields.Datetime.now()
        self._fenetre(maintenant - timedelta(days=5), maintenant - timedelta(days=5, hours=-2),
                      nom="Fenêtre d'il y a cinq jours")
        valeurs = self.client_a._hosting_status_valeurs()
        self.assertFalse(valeurs["windows_ahead"])
        self.assertEqual([w["name"] for w in valeurs["windows_past"]], ["Fenêtre d'il y a cinq jours"])

"""L'API du téléphone, jouée en HTTP par une personne ordinaire.

🔴 Jouée en INTERNE ORDINAIRE (feuilles de temps + projet), jamais en admin :
un administrateur voit tout, et un essai joué en admin ne prouve rien sur les
droits.

🔴 Chaque refus est suivi d'une lecture de la BASE, pas seulement du code de
réponse : une erreur rendue en JSON n'annule rien toute seule.
"""
import base64
import hashlib
import json
import math
import urllib.parse
from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, new_test_user, tagged

API = "/bf_timer/mobile/v1"
SCHEMA = "com.bluefoxconsultant.chronometre://auth"
GROUPES = "base.group_user,hr_timesheet.group_hr_timesheet_user,project.group_project_user"


def _defi(verificateur):
    condense = hashlib.sha256(verificateur.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(condense).decode().rstrip("=")


@tagged("post_install", "-at_install", "bf_timesheet_timer_mobile")
class TestApi(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("bf_timer.rounding_mode", "none")
        cls.verificateur = "verificateur-du-telephone-de-la-personne-ordinaire"

        def personne(login, groupes=GROUPES):
            usager = new_test_user(cls.env, login=login, groups=groupes)
            employe = cls.env["hr.employee"].create({"name": login, "user_id": usager.id})
            return usager, employe

        cls.personne, cls.employe = personne("chrono_ordinaire")
        cls.voisin, _ = personne("chrono_voisin")
        cls.approbateur, _ = personne(
            "chrono_approbateur", GROUPES + ",hr_timesheet.group_hr_timesheet_approver")
        cls.sans_groupe = new_test_user(cls.env, login="chrono_sans_groupe",
                                        groups="base.group_user")

        cls.projet = cls.env["project.project"].create({
            "name": "Projet ouvert", "allow_timesheets": True,
            "privacy_visibility": "employees"})
        cls.tache = cls.env["project.task"].create(
            {"name": "Tâche ouverte du chrono", "project_id": cls.projet.id})
        cls.autre_tache = cls.env["project.task"].create(
            {"name": "Autre tâche ouverte", "project_id": cls.projet.id})
        cls.projet_prive = cls.env["project.project"].create({
            "name": "Projet réservé", "allow_timesheets": True,
            "privacy_visibility": "followers"})
        cls.tache_privee = cls.env["project.task"].create(
            {"name": "Tâche réservée du chrono", "project_id": cls.projet_prive.id})

    # ── Outils ────────────────────────────────────────────────────────
    def _jeton(self, usager=None):
        Device = self.env["bf.timer.device"]
        code = Device._issue_pending((usager or self.personne).id,
                                     challenge=_defi(self.verificateur))
        appareil, jeton = Device._exchange(code, self.verificateur)
        return appareil, jeton

    def _get(self, chemin, jeton=None):
        entetes = {"Authorization": "Bearer %s" % jeton} if jeton else {}
        return self.url_open(API + chemin, headers=entetes)

    def _post(self, chemin, charge, jeton=None):
        entetes = {"Content-Type": "application/json"}
        if jeton:
            entetes["Authorization"] = "Bearer %s" % jeton
        return self.url_open(API + chemin, data=json.dumps(charge), headers=entetes)

    def _demarrer(self, usager, tache=None, il_y_a_minutes=0):
        donnees = self.env["bf.timer"].with_user(usager).start_timer((tache or self.tache).id)
        chrono = self.env["bf.timer"].browse(donnees["id"])
        if il_y_a_minutes:
            chrono.start_time = fields.Datetime.now() - timedelta(minutes=il_y_a_minutes)
        self.env.flush_all()
        return chrono

    def _lignes(self, tache=None):
        self.env.invalidate_all()
        return self.env["account.analytic.line"].search(
            [("task_id", "=", (tache or self.tache).id)])

    def _instantane(self, chrono):
        self.env.invalidate_all()
        return chrono.read(["is_active", "is_paused", "claimed_at", "start_time",
                            "accumulated_seconds", "write_date"])[0]

    def _debut_appariement(self, **params):
        valeurs = {"redirect": SCHEMA, "state": "etat-42",
                   "code_challenge": _defi(self.verificateur),
                   "code_challenge_method": "S256", "device_name": "Pixel 10 d'essai"}
        valeurs.update(params)
        valeurs = {k: v for k, v in valeurs.items() if v is not None}
        return self.url_open(API + "/auth/start?" + urllib.parse.urlencode(valeurs),
                             allow_redirects=False)

    @staticmethod
    def _requete_de(reponse):
        return urllib.parse.parse_qs(urllib.parse.urlsplit(reponse.headers["Location"]).query)

    # ── Découverte ────────────────────────────────────────────────────
    def test_le_ping_repond_sans_jeton_et_porte_la_marque(self):
        reponse = self._get("/ping")
        self.assertEqual(reponse.status_code, 200)
        charge = reponse.json()
        self.assertEqual(charge["module"], "bf_timesheet_timer_mobile")
        self.assertEqual(charge["api"], 1)
        self.assertEqual(set(charge["branding"]), {"name", "primary", "dark", "logo_url"})

    # ── Appariement ───────────────────────────────────────────────────
    def test_appariement_complet_par_le_navigateur(self):
        self.authenticate("chrono_ordinaire", "chrono_ordinaire")
        reponse = self._debut_appariement()
        self.assertEqual(reponse.status_code, 302)
        self.assertTrue(reponse.headers["Location"].startswith(SCHEMA + "?"))
        requete = self._requete_de(reponse)
        self.assertEqual(requete["state"], ["etat-42"])
        code = requete["code"][0]

        echange = self._post("/auth/exchange", {
            "code": code, "code_verifier": self.verificateur,
            "device_name": "Google Pixel 10 (Chronomètre)", "app_version": "0.1.0"})
        self.assertEqual(echange.status_code, 200)
        charge = echange.json()
        self.assertEqual(charge["user"]["login"], "chrono_ordinaire")
        self.assertIn("branding", charge)
        appareil = self.env["bf.timer.device"].sudo().with_context(active_test=False).search(
            [("user_id", "=", self.personne.id)])
        self.assertEqual(len(appareil), 1)
        self.assertEqual(appareil.name, "Google Pixel 10 (Chronomètre)")
        self.assertEqual(appareil.app_version, "0.1.0")
        self.assertEqual(self._get("/etat", charge["token"]).status_code, 200)

    def test_sans_session_on_passe_par_la_connexion(self):
        reponse = self._debut_appariement()
        self.assertIn(reponse.status_code, (302, 303))
        self.assertIn("/web/login", reponse.headers["Location"])

    def test_redirection_non_permise_est_refusee(self):
        self.authenticate("chrono_ordinaire", "chrono_ordinaire")
        reponse = self._debut_appariement(redirect="https://ailleurs.example/vol")
        self.assertEqual(reponse.status_code, 400)
        self.assertFalse(self.env["bf.timer.device"].sudo().with_context(active_test=False).search(
            [("user_id", "=", self.personne.id)]))

    def test_sans_le_groupe_des_feuilles_de_temps(self):
        self.authenticate("chrono_sans_groupe", "chrono_sans_groupe")
        reponse = self._debut_appariement()
        self.assertEqual(self._requete_de(reponse)["error"], ["no_access"])
        self.assertFalse(self.env["bf.timer.device"].sudo().with_context(active_test=False).search(
            [("user_id", "=", self.sans_groupe.id)]))

    def test_sans_defi_pkce(self):
        self.authenticate("chrono_ordinaire", "chrono_ordinaire")
        self.assertEqual(self._requete_de(self._debut_appariement(code_challenge=None))["error"],
                         ["pkce_required"])
        self.assertEqual(self._requete_de(
            self._debut_appariement(code_challenge_method="plain"))["error"], ["pkce_required"])

    def test_plafond_d_appareils(self):
        for _i in range(10):
            self._jeton()
        self.authenticate("chrono_ordinaire", "chrono_ordinaire")
        self.assertEqual(self._requete_de(self._debut_appariement())["error"],
                         ["too_many_devices"])

    def test_echange_avec_un_mauvais_verificateur(self):
        self.authenticate("chrono_ordinaire", "chrono_ordinaire")
        code = self._requete_de(self._debut_appariement())["code"][0]
        self.url_open("/web/session/logout", allow_redirects=False)
        reponse = self._post("/auth/exchange", {"code": code, "code_verifier": "pas-le-bon"})
        self.assertEqual(reponse.status_code, 401)
        self.assertFalse(self.env["bf.timer.device"].sudo().with_context(active_test=False).search(
            [("user_id", "=", self.personne.id)]),
            "L'appareil en attente doit être jeté au premier échec.")

    # ── Jeton ─────────────────────────────────────────────────────────
    def test_sans_jeton_ou_jeton_bidon(self):
        self.assertEqual(self._get("/etat").status_code, 401)
        self.assertEqual(self._get("/etat", "bidon").status_code, 401)
        self.assertEqual(self._post("/chrono/demarrer", {"task_id": self.tache.id}).status_code, 401)

    def test_un_appareil_revoque_recoit_401(self):
        appareil, jeton = self._jeton()
        self.assertEqual(self._get("/etat", jeton).status_code, 200)
        appareil.active = False
        self.env.flush_all()
        self.assertEqual(self._get("/etat", jeton).status_code, 401)

    def test_un_telephone_revoque_puis_reactive_reste_dehors(self):
        """🔴 La bascule « Actif » ne doit pas faire revivre un jeton révoqué."""
        appareil, jeton = self._jeton()
        self.assertEqual(self._post("/auth/logout", {}, jeton).status_code, 200)
        appareil.sudo().write({"active": True})
        self.env.flush_all()
        self.assertEqual(self._get("/etat", jeton).status_code, 401)

    def test_un_code_non_textuel_rend_401_et_pas_500(self):
        self.assertEqual(self._post("/auth/exchange", {"code": 123}).status_code, 401)
        self.assertEqual(self._post("/auth/exchange", {"code": ["a"],
                                                       "code_verifier": 5}).status_code, 401)
        code = self.env["bf.timer.device"]._issue_pending(
            self.personne.id, challenge=_defi(self.verificateur))
        self.assertEqual(self._post("/auth/exchange", {"code": code,
                                                       "code_verifier": 42}).status_code, 401)

    def test_un_usager_archive_recoit_401(self):
        _appareil, jeton = self._jeton()
        self.personne.active = False
        self.env.flush_all()
        self.assertEqual(self._get("/etat", jeton).status_code, 401)

    def test_se_deconnecter_revoque(self):
        appareil, jeton = self._jeton()
        self.assertEqual(self._post("/auth/logout", {}, jeton).status_code, 200)
        self.env.invalidate_all()
        self.assertFalse(appareil.active)
        self.assertEqual(self._get("/etat", jeton).status_code, 401)

    # ── Lire ──────────────────────────────────────────────────────────
    def test_l_etat_en_une_lecture(self):
        _appareil, jeton = self._jeton()
        actif = self._demarrer(self.personne, il_y_a_minutes=20)
        en_attente = self._demarrer(self.personne, self.autre_tache, il_y_a_minutes=15)
        self.env["bf.timer"].with_user(self.personne).stop_timer(en_attente.id)
        self.env.flush_all()
        charge = self._get("/etat", jeton).json()
        self.assertEqual(set(charge), {"maintenant", "actifs", "en_attente", "taches",
                                       "totaux", "arrondi", "gabarits", "url_appareils"})
        self.assertTrue(charge["maintenant"].endswith("Z"))
        self.assertEqual([c["id"] for c in charge["actifs"]], [actif.id])
        self.assertTrue(charge["actifs"][0]["start_time_iso"].endswith("Z"))
        self.assertEqual([c["timer_id"] for c in charge["en_attente"]], [en_attente.id])
        # Arrêté à l'instant au navigateur : visible, et marqué réclamé.
        self.assertTrue(charge["en_attente"][0]["claimed"])
        self.assertEqual(set(charge["totaux"]), {"jour", "semaine"})

    def test_le_lien_vers_mes_appareils_suit_l_installation(self):
        _appareil, jeton = self._jeton()
        # ⚠️ L'essai pose lui-même l'état du module : il doit passer sur une base
        # où bf_devices n'existe pas du tout, pas seulement là où il est installé.
        module = self.env["ir.module.module"].search([("name", "=", "bf_devices")])
        if not module:
            module = self.env["ir.module.module"].create({"name": "bf_devices", "state": "uninstalled"})
        module.state = "installed"
        self.env.flush_all()
        self.assertEqual(self._get("/etat", jeton).json()["url_appareils"], "/my/appareils")
        module.state = "uninstalled"
        self.env.flush_all()
        self.assertIsNone(self._get("/etat", jeton).json()["url_appareils"])

    def test_l_echange_rend_la_marque_comme_le_ping(self):
        code = self.env["bf.timer.device"]._issue_pending(
            self.personne.id, challenge=_defi(self.verificateur))
        charge = self._post("/auth/exchange", {
            "code": code, "code_verifier": self.verificateur}).json()
        self.assertEqual(set(charge), {"token", "user", "branding"})
        self.assertEqual(set(charge["branding"]), set(self._get("/ping").json()["branding"]))

    def test_une_tache_lisible_dans_un_projet_illisible(self):
        """🔴 Assignée dans un projet réservé : la tâche se lit, le projet non.

        Le nom du projet sort comme Odoo l'affiche dans un many2one ; sans ça,
        toute la liste répondait 403.
        """
        tache = self.env["project.task"].create({
            "name": "Tâche assignée du projet réservé", "project_id": self.projet_prive.id,
            "user_ids": [(6, 0, [self.personne.id])]})
        self.env.flush_all()
        self.assertTrue(self.env["project.task"].with_user(self.personne).search(
            [("id", "=", tache.id)]), "Prémisse : la tâche se lit.")
        self.assertFalse(self.projet_prive.with_user(self.personne).has_access("read"),
                         "Prémisse : le projet ne se lit pas.")
        _appareil, jeton = self._jeton()
        self.assertEqual(self._post("/tache/epingler", {"task_id": tache.id, "epingle": True},
                                    jeton).status_code, 200)
        demarre = self._post("/chrono/demarrer", {"task_id": tache.id}, jeton)
        self.assertEqual(demarre.status_code, 200, demarre.text)
        self.assertEqual(demarre.json()["chrono"]["project_name"], "Projet réservé")
        etat = self._get("/etat", jeton)
        self.assertEqual(etat.status_code, 200, etat.text)
        self.assertIn(tache.id, [t["task_id"] for t in etat.json()["taches"]])
        self.assertIn(tache.id, [c["task_id"] for c in etat.json()["actifs"]])
        trouvees = self._get("/taches?q=assign%C3%A9e", jeton)
        self.assertEqual(trouvees.status_code, 200, trouvees.text)
        self.assertIn(tache.id, [t["task_id"] for t in trouvees.json()["taches"]])
        une = self._get("/tache/%d" % tache.id, jeton)
        self.assertEqual(une.status_code, 200, une.text)
        self.assertEqual(une.json()["tache"]["project_name"], "Projet réservé")
        self.assertEqual(une.json()["tache"]["project_color"], 0,
                         "Aucun autre champ du projet ne sort.")
        chrono = demarre.json()["chrono"]["id"]
        self.assertEqual(self._post("/chrono/apercu", {"timer_id": chrono}, jeton).status_code, 200)

    def test_le_telephone_prend_l_employe_de_la_societe_de_la_tache(self):
        societe_b = self.env["res.company"].create({"name": "Société B de l'employé"})
        self.personne.write({"company_ids": [(4, societe_b.id)]})
        employe_b = self.env["hr.employee"].create({
            "name": "chrono_ordinaire en B", "user_id": self.personne.id,
            "company_id": societe_b.id})
        projet_b = self.env["project.project"].create({
            "name": "Projet B de l'employé", "allow_timesheets": True,
            "company_id": societe_b.id, "privacy_visibility": "employees"})
        tache_b = self.env["project.task"].create(
            {"name": "Tâche B de l'employé", "project_id": projet_b.id})
        _appareil, jeton = self._jeton()
        self.env.flush_all()
        reponse = self._post("/chrono/demarrer", {"task_id": tache_b.id}, jeton)
        self.assertEqual(reponse.status_code, 200, reponse.text)
        self.env.invalidate_all()
        chrono = self.env["bf.timer"].browse(reponse.json()["chrono"]["id"])
        self.assertEqual(chrono.employee_id, employe_b)
        self.assertNotEqual(chrono.employee_id, self.employe)

    def test_la_recherche_respecte_la_visibilite(self):
        _appareil, jeton = self._jeton()
        ids = [t["task_id"] for t in self._get(
            "/taches?q=du%20chrono", jeton).json()["taches"]]
        self.assertIn(self.tache.id, ids)
        self.assertNotIn(self.tache_privee.id, ids)
        par_numero = self._get("/taches?q=%d" % self.tache_privee.id, jeton).json()["taches"]
        self.assertNotIn(self.tache_privee.id, [t["task_id"] for t in par_numero])
        self.assertEqual(self._get("/tache/%d" % self.tache_privee.id, jeton).status_code, 404)
        self.assertEqual(self._post("/chrono/demarrer", {"task_id": self.tache_privee.id},
                                    jeton).status_code, 404)
        self.assertEqual(self._post("/tache/epingler", {"task_id": self.tache_privee.id,
                                                        "epingle": True}, jeton).status_code, 404)
        self.env.invalidate_all()
        self.assertFalse(self.env["bf.timer"].search([("task_id", "=", self.tache_privee.id)]))
        self.assertFalse(self.env["bf.timer.pinned.task"].search(
            [("task_id", "=", self.tache_privee.id)]))

    def test_le_numero_tape_vient_en_premier(self):
        _appareil, jeton = self._jeton()
        taches = self._get("/taches?q=%d" % self.tache.id, jeton).json()["taches"]
        self.assertEqual(taches[0]["task_id"], self.tache.id)
        self.assertEqual(set(taches[0]), {
            "task_id", "task_name", "project_id", "project_name", "project_color",
            "stage_name", "is_closed", "is_pinned", "has_active_timer", "allow_timesheets"})

    def test_une_tache_d_une_autre_societe_de_la_personne(self):
        """Sans sélecteur de sociétés, le téléphone voit toutes les siennes."""
        societe_b = self.env["res.company"].create({"name": "Société B du chrono"})
        self.personne.write({"company_ids": [(4, societe_b.id)]})
        projet_b = self.env["project.project"].create({
            "name": "Projet B", "allow_timesheets": True, "company_id": societe_b.id,
            "privacy_visibility": "employees"})
        tache_b = self.env["project.task"].create(
            {"name": "Tâche de la société B", "project_id": projet_b.id})
        _appareil, jeton = self._jeton()
        self.env.flush_all()
        reponse = self._get("/tache/%d" % tache_b.id, jeton)
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.json()["tache"]["task_id"], tache_b.id)

    # ── Agir ──────────────────────────────────────────────────────────
    def test_demarrer_puis_doublon_refuse_sans_rien_ecrire(self):
        _appareil, jeton = self._jeton()
        premier = self._post("/chrono/demarrer", {"task_id": self.tache.id}, jeton)
        self.assertEqual(premier.status_code, 200)
        self.assertTrue(premier.json()["chrono"]["start_time_iso"].endswith("Z"))
        second = self._post("/chrono/demarrer", {"task_id": self.tache.id}, jeton)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(second.json()["error"], "refused")
        self.env.invalidate_all()
        self.assertEqual(self.env["bf.timer"].search_count(
            [("user_id", "=", self.personne.id), ("task_id", "=", self.tache.id)]), 1)

    def test_pause_puis_reprendre(self):
        _appareil, jeton = self._jeton()
        chrono = self._demarrer(self.personne, il_y_a_minutes=10)
        pause = self._post("/chrono/pause", {"timer_id": chrono.id}, jeton)
        self.assertEqual(pause.status_code, 200)
        self.assertTrue(pause.json()["chrono"]["is_paused"])
        self.assertEqual(self._post("/chrono/pause", {"timer_id": chrono.id}, jeton).status_code, 400)
        reprise = self._post("/chrono/reprendre", {"timer_id": chrono.id}, jeton)
        self.assertEqual(reprise.status_code, 200)
        self.assertFalse(reprise.json()["chrono"]["is_paused"])
        self.assertGreaterEqual(reprise.json()["chrono"]["accumulated_seconds"], 600)

    def test_l_apercu_ne_mute_rien(self):
        _appareil, jeton = self._jeton()
        chrono = self._demarrer(self.personne, il_y_a_minutes=50)
        avant = self._instantane(chrono)
        reponse = self._post("/chrono/apercu", {"timer_id": chrono.id}, jeton)
        self.assertEqual(reponse.status_code, 200)
        charge = reponse.json()
        self.assertEqual(charge["etat"], "actif")
        self.assertGreaterEqual(charge["elapsed_seconds"], 3000)
        self.assertEqual(charge["suggested_minutes"], math.ceil(charge["elapsed_seconds"] / 60))
        self.assertEqual(self._instantane(chrono), avant)
        self.assertFalse(self._lignes())

    def test_enregistrer_depuis_un_chrono_qui_tourne(self):
        _appareil, jeton = self._jeton()
        chrono = self._demarrer(self.personne, il_y_a_minutes=50)
        reponse = self._post("/chrono/enregistrer", {
            "timer_id": chrono.id, "minutes": 45, "description": "Depuis le téléphone"}, jeton)
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.json()["minutes"], 45)
        lignes = self._lignes()
        self.assertEqual(len(lignes), 1)
        self.assertEqual(lignes.name, "Depuis le téléphone")
        self.assertAlmostEqual(lignes.unit_amount, 0.75, places=6)
        self.assertEqual(lignes.employee_id, self.employe)
        self.assertFalse(chrono.exists())

    def test_enregistrer_depuis_un_chrono_en_attente(self):
        _appareil, jeton = self._jeton()
        chrono = self._demarrer(self.personne, il_y_a_minutes=40)
        self.env["bf.timer"].with_user(self.personne).stop_timer(chrono.id)
        self.env.flush_all()
        apercu = self._post("/chrono/apercu", {"timer_id": chrono.id}, jeton).json()
        self.assertEqual(apercu["etat"], "en_attente")
        self.assertTrue(apercu["arrete_le"].endswith("Z"))
        reponse = self._post("/chrono/enregistrer", {"timer_id": chrono.id, "minutes": 30}, jeton)
        self.assertEqual(reponse.status_code, 200)
        lignes = self._lignes()
        self.assertEqual(len(lignes), 1)
        self.assertAlmostEqual(lignes.unit_amount, 0.5, places=6)
        self.assertEqual(lignes.name, self.tache.name)
        self.assertFalse(chrono.exists())

    def test_25_minutes_s_ecrivent_comme_au_navigateur(self):
        """Le dialogue du navigateur et l'assistant écrivent 0,42 h, pas 0,4167."""
        _appareil, jeton = self._jeton()
        chrono = self._demarrer(self.personne, il_y_a_minutes=30)
        reponse = self._post("/chrono/enregistrer", {"timer_id": chrono.id, "minutes": 25}, jeton)
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(self._lignes().unit_amount, 0.42)

    def test_sous_un_palier_on_ecrit_le_palier_comme_au_navigateur(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_timer.rounding_mode", "round_all")
        self.env["ir.config_parameter"].sudo().set_param("bf_timer.rounding_increment", "5")
        _appareil, jeton = self._jeton()
        chrono = self._demarrer(self.personne, il_y_a_minutes=3)
        reponse = self._post("/chrono/enregistrer", {"timer_id": chrono.id, "minutes": 3}, jeton)
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(self._lignes().unit_amount, 0.08)

    def test_la_date_est_le_jour_de_la_personne_au_premier_demarrage(self):
        from datetime import date, datetime
        self.personne.tz = "America/Toronto"
        _appareil, jeton = self._jeton()
        chrono = self._demarrer(self.personne)
        # Premier démarrage le 13 à 22:30 à Montréal, reprise le 14 à 01:00.
        chrono.write({"first_start": datetime(2026, 9, 14, 2, 30),
                      "start_time": datetime(2026, 9, 14, 5, 0)})
        self.env.flush_all()
        # create_date d'un AUTRE jour : une implémentation qui le lirait échoue.
        self.env.cr.execute("UPDATE bf_timer SET create_date = %s WHERE id = %s",
                            (datetime(2026, 9, 10, 15, 0), chrono.id))
        self.env.invalidate_all()
        reponse = self._post("/chrono/enregistrer", {"timer_id": chrono.id, "minutes": 30}, jeton)
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(self._lignes().date, date(2026, 9, 13))

    def test_l_etat_repond_quand_une_epinglee_devient_illisible(self):
        _appareil, jeton = self._jeton()
        self.assertEqual(self._post("/tache/epingler", {"task_id": self.autre_tache.id,
                                                        "epingle": True}, jeton).status_code, 200)
        self.autre_tache.project_id = self.projet_prive
        self.env.flush_all()
        self.env.invalidate_all()
        reponse = self._get("/etat", jeton)
        self.assertEqual(reponse.status_code, 200)
        self.assertNotIn(self.autre_tache.id, [t["task_id"] for t in reponse.json()["taches"]])

    def test_minutes_invalides_refusees_sans_rien_ecrire(self):
        _appareil, jeton = self._jeton()
        chrono = self._demarrer(self.personne, il_y_a_minutes=5)
        avant = self._instantane(chrono)
        for minutes in (0, -5, "30", 12.5, True, None):
            reponse = self._post("/chrono/enregistrer",
                                 {"timer_id": chrono.id, "minutes": minutes}, jeton)
            self.assertEqual(reponse.status_code, 400, minutes)
        self.assertEqual(self._instantane(chrono), avant)
        self.assertFalse(self._lignes())

    def test_un_echec_a_mi_chemin_ne_laisse_rien(self):
        """🔴 L'arrêt passe, la feuille de temps échoue : le chrono doit TOURNER encore.

        L'employé archivé fait refuser la ligne APRÈS que ``stop_timer`` a écrit.
        Sans le point de reprise, la réponse est bien un 400 et le chrono reste
        pourtant arrêté en base.
        """
        _appareil, jeton = self._jeton()
        chrono = self._demarrer(self.personne, il_y_a_minutes=30)
        avant = self._instantane(chrono)
        self.employe.active = False
        self.env.flush_all()
        reponse = self._post("/chrono/enregistrer", {"timer_id": chrono.id, "minutes": 30}, jeton)
        self.assertEqual(reponse.status_code, 400)
        self.assertEqual(self._instantane(chrono), avant)
        self.assertTrue(chrono.is_active)
        self.assertFalse(self._lignes())

    def test_le_chrono_d_autrui_est_intouchable(self):
        _appareil, jeton = self._jeton()
        chrono_voisin = self._demarrer(self.voisin, il_y_a_minutes=25)
        avant = self._instantane(chrono_voisin)
        for chemin, charge in (("/chrono/apercu", {}), ("/chrono/pause", {}),
                               ("/chrono/reprendre", {}), ("/chrono/abandonner", {}),
                               ("/chrono/enregistrer", {"minutes": 25})):
            charge = dict(charge, timer_id=chrono_voisin.id)
            self.assertEqual(self._post(chemin, charge, jeton).status_code, 404, chemin)
        self.assertEqual(self._instantane(chrono_voisin), avant)
        self.assertFalse(self._lignes())

    def test_un_approbateur_ne_lit_pas_le_chrono_d_un_collegue(self):
        """🔴 La règle « manager full access » lui ouvre tous les chronos de l'équipe."""
        _appareil, jeton = self._jeton(self.approbateur)
        chrono_voisin = self._demarrer(self.voisin, il_y_a_minutes=25)
        self.assertTrue(self.env["bf.timer"].with_user(self.approbateur).search(
            [("id", "=", chrono_voisin.id)]), "Prémisse : l'approbateur voit ce chrono.")
        reponse = self._post("/chrono/apercu", {"timer_id": chrono_voisin.id}, jeton)
        self.assertEqual(reponse.status_code, 404)
        self.assertNotIn("task_name", reponse.json())
        self.assertEqual(self._post("/chrono/abandonner", {"timer_id": chrono_voisin.id},
                                    jeton).status_code, 404)
        self.env.invalidate_all()
        self.assertTrue(chrono_voisin.exists())

    def test_abandonner(self):
        _appareil, jeton = self._jeton()
        chrono = self._demarrer(self.personne, il_y_a_minutes=5)
        self.assertEqual(self._post("/chrono/abandonner", {"timer_id": chrono.id},
                                    jeton).status_code, 200)
        self.env.invalidate_all()
        self.assertFalse(chrono.exists())
        self.assertFalse(self._lignes())

    def test_epingler_et_desepingler(self):
        _appareil, jeton = self._jeton()
        Epingle = self.env["bf.timer.pinned.task"]
        oui = self._post("/tache/epingler", {"task_id": self.tache.id, "epingle": True}, jeton)
        self.assertEqual(oui.status_code, 200)
        self.env.invalidate_all()
        self.assertEqual(Epingle.search_count([("user_id", "=", self.personne.id),
                                               ("task_id", "=", self.tache.id)]), 1)
        self.assertTrue(self._get("/tache/%d" % self.tache.id, jeton).json()["tache"]["is_pinned"])
        self.assertEqual(self._post("/tache/epingler", {"task_id": self.tache.id, "epingle": "oui"},
                                    jeton).status_code, 400)
        non = self._post("/tache/epingler", {"task_id": self.tache.id, "epingle": False}, jeton)
        self.assertEqual(non.status_code, 200)
        self.env.invalidate_all()
        self.assertFalse(Epingle.search([("user_id", "=", self.personne.id)]))

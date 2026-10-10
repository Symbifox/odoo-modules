"""Mes activités au téléphone, et la fiche de tâche plus modifiable.

Les essais portent sur ce qui casserait sans bruit : l'activité d'un collègue
lue ou fermée par identifiant deviné, une maintenance « faite » dont la
planification reste due, une description mise en forme aplatie par un champ
texte, un geste refusé à mi-chemin dont la moitié resterait en base, une
activité qui suit une rencontre déplacée seule.
"""

import json
import uuid
from datetime import date, timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, TransactionCase, tagged

BASE = "/bf_calendar/mobile/v1"


def _groupes(env):
    return [env.ref("base.group_user").id, env.ref("project.group_project_user").id]


def _todo(env):
    return env.ref("mail.mail_activity_data_todo")


@tagged("post_install", "-at_install", "bf_calendar_mobile")
class TestActivites(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groupes = _groupes(cls.env)
        cls.user = cls.env["res.users"].create({
            "name": "Banc Activités", "login": "banc.activites.t",
            "email": "banc.activites.t@example.org", "tz": "America/Toronto",
            "groups_id": [(6, 0, groupes)],
        })
        cls.autre = cls.env["res.users"].create({
            "name": "Banc Activités Autre", "login": "banc.activites.autre.t",
            "email": "banc.activites.autre.t@example.org",
            "groups_id": [(6, 0, groupes)],
        })
        cls.projet = cls.env["project.project"].create({
            "name": "Banc activités",
            "privacy_visibility": "followers",
            "message_partner_ids": [(6, 0, (cls.user | cls.autre).partner_id.ids)],
        })
        cls.tache = cls.env["project.task"].create({
            "name": "Tâche aux activités", "project_id": cls.projet.id,
            "user_ids": [(6, 0, cls.user.ids)],
        })
        cls.aujourdhui = fields.Date.context_today(cls.tache.with_user(cls.user))

    def _activite(self, user=None, jours=0, fiche=None, **extra):
        fiche = fiche or self.tache
        return fiche.activity_schedule(
            activity_type_id=_todo(self.env).id,
            date_deadline=self.aujourdhui + timedelta(days=jours),
            summary=extra.pop("summary", "Rappel"),
            user_id=(user or self.user).id, **extra)

    # ---------------------------------------------------------------- lire

    def test_mes_activites_seulement_jusqu_a_l_horizon_retards_compris(self):
        retard = self._activite(jours=-3)
        jour = self._activite(jours=0)
        loin = self._activite(jours=40)
        self._activite(user=self.autre, jours=0)
        res = self.env["mail.activity"].with_user(self.user).mobile_mine(
            fields.Date.to_string(self.aujourdhui + timedelta(days=14)))
        ids = [a["id"] for a in res["activities"]]
        self.assertEqual(ids, [retard.id, jour.id])
        self.assertNotIn(loin.id, ids)
        etats = {a["id"]: a["state"] for a in res["activities"]}
        self.assertEqual(etats, {retard.id: "overdue", jour.id: "today"})
        self.assertEqual(res["total"], 2)
        self.assertFalse(res["truncated"])

    def test_la_charge_nomme_la_fiche_et_son_modele(self):
        self._activite(summary="Relancer le client")
        res = self.env["mail.activity"].with_user(self.user).mobile_mine()
        a = res["activities"][0]
        self.assertEqual(a["res_model"], "project.task")
        self.assertEqual(a["res_id"], self.tache.id)
        self.assertEqual(a["res_name"], "Tâche aux activités")
        self.assertTrue(a["model_label"])
        self.assertEqual(a["summary"], "Relancer le client")
        self.assertTrue(a["mine"])
        self.assertFalse(a["closes_record"])
        self.assertTrue(a["url"].endswith("/odoo/project.task/%s" % self.tache.id))

    def test_date_mal_formee_est_une_erreur_pas_un_plantage(self):
        with self.assertRaises(UserError):
            self.env["mail.activity"].with_user(self.user).mobile_mine("demain")

    def test_assignee_sur_une_fiche_fermee_listee_et_fermable_comme_au_bureau(self):
        """Odoo laisse lire ET fermer toute activité qui m'est assignée, fiche
        fermée ou non (``mail.activity._check_access`` et ses règles) : le
        téléphone fait comme le bureau, ni plus ni moins."""
        prive = self.env["project.project"].create({
            "name": "Fermé", "privacy_visibility": "followers"})
        tache = self.env["project.task"].create({"name": "Secrète", "project_id": prive.id})
        activite = self._activite(fiche=tache)
        res = self.env["mail.activity"].with_user(self.user).mobile_mine()
        self.assertIn(activite.id, [a["id"] for a in res["activities"]])
        activite.with_user(self.user).mobile_done()
        self.assertFalse(activite.exists().filtered("active"))

    # ---------------------------------------------------------------- agir

    def test_fait_ferme_l_activite_avec_le_mot(self):
        activite = self._activite()
        res = activite.with_user(self.user).mobile_done("Appelé, il rappelle lundi")
        self.assertEqual(res, {"ok": True, "closed_record": False})
        self.assertFalse(activite.exists().filtered("active"))
        corps = self.tache.message_ids[:1].body or ""
        self.assertIn("Appelé, il rappelle lundi", corps)

    def test_fait_sur_l_activite_d_un_collegue_hors_de_ma_fiche_est_refuse(self):
        prive = self.env["project.project"].create({
            "name": "Fermé 2", "privacy_visibility": "followers"})
        tache = self.env["project.task"].create({"name": "Autre", "project_id": prive.id})
        activite = self._activite(user=self.autre, fiche=tache)
        with self.assertRaises(AccessError):
            activite.with_user(self.user).mobile_done()
        self.assertTrue(activite.exists())

    def test_reporter_change_la_date(self):
        activite = self._activite()
        cible = self.aujourdhui + timedelta(days=3)
        res = activite.with_user(self.user).mobile_reschedule(fields.Date.to_string(cible))
        self.assertEqual(activite.date_deadline, cible)
        self.assertEqual(res["activity"]["state"], "planned")

    def test_reporter_sans_date_est_refuse(self):
        activite = self._activite()
        with self.assertRaises(UserError):
            activite.with_user(self.user).mobile_reschedule("")

    def test_reporter_une_activite_qui_suit_une_rencontre_est_refuse(self):
        if "calendar_event_id" not in self.env["mail.activity"]._fields:
            self.skipTest("module agenda absent")
        event = self.env["calendar.event"].create({
            "name": "Rencontre liée",
            "start": fields.Datetime.now(), "stop": fields.Datetime.now() + timedelta(hours=1),
        })
        activite = self._activite(calendar_event_id=event.id)
        with self.assertRaises(UserError):
            activite.with_user(self.user).mobile_reschedule(
                fields.Date.to_string(self.aujourdhui + timedelta(days=2)))

    def test_planifier_sur_une_tache_a_mon_nom_et_pas_automatique(self):
        res = self.env["mail.activity"].with_user(self.user).mobile_create({
            "res_model": "project.task", "res_id": self.tache.id,
            "activity_type_id": _todo(self.env).id,
            "date": fields.Date.to_string(self.aujourdhui + timedelta(days=1)),
            "summary": "Préparer la rencontre", "note": "Ligne 1\nLigne 2",
        })
        activite = self.env["mail.activity"].browse(res["activity"]["id"])
        self.assertEqual(activite.user_id, self.user)
        self.assertFalse(activite.automated)
        self.assertEqual(activite.summary, "Préparer la rencontre")
        self.assertIn("<p>Ligne 1</p><p>Ligne 2</p>", str(activite.note))

    def test_planifier_hors_des_fiches_permises_est_refuse(self):
        # 🔴 Par quelqu'un qui POURRAIT écrire sur le contact : sinon le refus
        # d'accès d'Odoo (AccessError hérite de UserError) passait pour le nôtre
        # et l'essai restait vert sans la garde (mutation survivante).
        with self.assertRaisesRegex(UserError, "n'accepte pas"):
            self.env["mail.activity"].mobile_create({
                "res_model": "res.partner", "res_id": self.env.user.partner_id.id,
                "activity_type_id": _todo(self.env).id,
                "date": fields.Date.to_string(self.aujourdhui),
            })

    def test_planifier_un_type_reunion_est_refuse(self):
        reunion = self.env["mail.activity.type"].search([("category", "=", "meeting")], limit=1)
        if not reunion:
            self.skipTest("aucun type Réunion")
        with self.assertRaises(UserError):
            self.env["mail.activity"].with_user(self.user).mobile_create({
                "res_model": "project.task", "res_id": self.tache.id,
                "activity_type_id": reunion.id,
                "date": fields.Date.to_string(self.aujourdhui),
            })

    def test_types_offerts_sans_reunion(self):
        res = self.env["mail.activity"].with_user(self.user).mobile_types("project.task")
        categories = {t["category"] for t in res["types"]}
        self.assertNotIn("meeting", categories)
        self.assertIn(_todo(self.env).id, [t["id"] for t in res["types"]])


@tagged("post_install", "-at_install", "bf_calendar_mobile")
class TestMaintenance(TransactionCase):
    """🔴 « Fait » sur une maintenance doit poser la prochaine échéance."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "hosting.maintenance.schedule" not in cls.env:
            return
        # Le montage minimal de `hosting_management` (ses propres essais font pareil).
        client = cls.env["res.partner"].create({"name": "Client de banc"})
        serveur = cls.env["hosting.server"].create({
            "name": "banc-t", "code": "SRV-T", "hostname": "banc.invalid"})
        logiciel = cls.env["hosting.software"].create({"name": "Logiciel de banc", "code": "lbanc"})
        cls.service = cls.env["hosting.service"].create({
            "name": "Service de banc", "partner_id": client.id,
            "server_id": serveur.id, "software_id": logiciel.id})
        cls.planif = cls.env["hosting.maintenance.schedule"].create({
            "name": "Optimisation de la base",
            "service_id": cls.service.id,
            "frequency": "monthly",
            "next_due": date.today() + timedelta(days=2),
            "user_id": cls.env.user.id,
        })

    def setUp(self):
        super().setUp()
        if "hosting.maintenance.schedule" not in self.env:
            self.skipTest("hosting_management absent")

    def _activite(self):
        type_maint = self.env.ref("hosting_management.mail_activity_type_hosting_maintenance")
        return self.planif.activity_ids.filtered(lambda a: a.activity_type_id == type_maint)

    def test_fait_passe_par_la_cloture_de_la_maintenance(self):
        activite = self._activite()
        self.assertEqual(len(activite), 1)
        self.assertTrue(activite._mobile_payload()["closes_record"])
        echeance_avant = self.planif.next_due
        res = activite.mobile_done("Fait depuis le téléphone")
        self.assertEqual(res, {"ok": True, "closed_record": True})
        self.assertEqual(self.planif.last_performed, date.today())
        self.assertNotEqual(self.planif.next_due, echeance_avant)
        # La clôture refait une activité pour la prochaine échéance.
        suivante = self._activite()
        self.assertEqual(len(suivante), 1)
        self.assertNotEqual(suivante.id, activite.id)
        self.assertIn("Fait depuis le téléphone", "".join(self.planif.message_ids.mapped("body")))

    def test_sans_droit_sur_la_maintenance_rien_n_est_journalise(self):
        """🔴 La clôture journalise EN SUDO avant d'écrire : sans le contrôle
        préalable, un employé sans droit sur la maintenance laisserait une
        trace « maintenance complétée » d'une chose non faite."""
        # Un rôle qui LIT l'hébergement sans pouvoir l'écrire : sans droit de
        # lecture, Odoo refusait avant même le journal et l'essai ne prouvait
        # rien (mutation survivante).
        lecteurs = self.env["res.groups"].create({"name": "Lecture hébergement"})
        for modele in ("hosting.maintenance.schedule", "hosting.service", "hosting.server"):
            self.env["ir.model.access"].create({
                "name": "lecture %s" % modele, "group_id": lecteurs.id,
                "model_id": self.env["ir.model"]._get_id(modele),
                "perm_read": True, "perm_write": False, "perm_create": False, "perm_unlink": False,
            })
        employe = self.env["res.users"].create({
            "name": "Employé lecteur", "login": "employe.maint.t",
            "email": "employe.maint.t@example.org",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id, lecteurs.id])],
        })
        activite = self._activite()
        activite.user_id = employe
        Journal = self.env["hosting.audit.log"].sudo()
        domaine = [("res_model", "=", "hosting.maintenance.schedule"), ("res_id", "=", self.planif.id)]
        avant = Journal.search_count(domaine)
        # 🔴 Pas `assertRaises` : dans un essai Odoo il ouvre un point de reprise
        # et DÉFAIT ce qui précède l'exception, journal compris. L'essai ne
        # voyait donc rien, et la garde pouvait sauter (mutation survivante).
        refus = None
        try:
            activite.with_user(employe).mobile_done()
        except AccessError as exc:
            refus = exc
        self.assertIsNotNone(refus)
        self.assertEqual(Journal.search_count(domaine), avant)
        self.assertFalse(self.planif.last_performed)

    def test_une_autre_activite_sur_la_maintenance_se_ferme_normalement(self):
        autre = self.planif.activity_schedule(
            activity_type_id=_todo(self.env).id, summary="Vérifier les journaux",
            user_id=self.env.uid)
        self.assertFalse(autre._mobile_payload()["closes_record"])
        avant = self.planif.last_performed
        autre.mobile_done()
        self.assertEqual(self.planif.last_performed, avant)


@tagged("post_install", "-at_install", "bf_calendar_mobile")
class TestFicheTache(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env["res.users"].create({
            "name": "Banc Fiche", "login": "banc.fiche.t",
            "email": "banc.fiche.t@example.org",
            "groups_id": [(6, 0, _groupes(cls.env))],
        })
        cls.projet = cls.env["project.project"].create({"name": "Banc fiche"})

    def _tache(self, description=False):
        return self.env["project.task"].create({
            "name": "Fiche", "project_id": self.projet.id,
            "user_ids": [(6, 0, self.user.ids)], "description": description,
        })

    def test_la_fiche_rend_la_description_et_les_activites(self):
        tache = self._tache('<div data-oe-version="2.0"><p>Ligne A</p><p>Ligne B</p></div>')
        tache.activity_schedule(activity_type_id=_todo(self.env).id, user_id=self.user.id)
        res = tache.with_user(self.user).mobile_detail()["task"]
        self.assertEqual(res["description"], "Ligne A\nLigne B")
        self.assertTrue(res["description_editable"])
        self.assertEqual(len(res["activities"]), 1)
        self.assertTrue(res["detail"])

    def test_la_liste_ne_porte_pas_la_description(self):
        tache = self._tache("<p>Long texte</p>")
        self.assertNotIn("description", tache._mobile_payload())

    def test_description_simple_reecrite_en_paragraphes(self):
        tache = self._tache("<p>Avant</p>")
        res = tache.with_user(self.user).mobile_write({"description_text": "Un\n\nDeux <b>"})
        self.assertEqual(str(tache.description), "<p>Un</p><p><br></p><p>Deux &lt;b&gt;</p>")
        self.assertEqual(res["task"]["description"], "Un\n\nDeux <b>")

    def test_description_riche_refusee_plutot_qu_aplatie(self):
        riche = "<ul><li>Point un</li><li>Point <strong>deux</strong></li></ul>"
        tache = self._tache(riche)
        self.assertFalse(tache.with_user(self.user).mobile_detail()["task"]["description_editable"])
        with self.assertRaises(UserError):
            tache.with_user(self.user).mobile_write({"description_text": "Écrasé"})
        self.assertIn("<li>", str(tache.description))

    def test_une_classe_de_mise_en_forme_rend_la_description_riche(self):
        colonnes = ('<div class="o_text_columns"><div class="row">'
                    '<div class="col-6"><p>Gauche</p></div><div class="col-6"><p>Droite</p></div>'
                    '</div></div>')
        for riche in (colonnes, '<p class="text-center">Centré</p>', '<div class="alert alert-info"><p>Note</p></div>'):
            tache = self._tache(riche)
            self.assertFalse(
                tache.with_user(self.user).mobile_detail()["task"]["description_editable"], riche)
        # Une classe vide n'est pas une mise en forme.
        self.assertTrue(self._tache('<p class="">Simple</p>').with_user(self.user)
                        .mobile_detail()["task"]["description_editable"])

    def test_commentaire_est_une_note_interne(self):
        tache = self._tache()
        res = tache.with_user(self.user).mobile_comment("Vu avec Marie\nOn livre jeudi")
        message = self.env["mail.message"].browse(res["message_id"])
        self.assertEqual(message.subtype_id, self.env.ref("mail.mt_note"))
        self.assertEqual(message.author_id, self.user.partner_id)
        self.assertIn("<p>Vu avec Marie</p><p>On livre jeudi</p>", str(message.body))

    def test_commentaire_vide_refuse(self):
        with self.assertRaises(UserError):
            self._tache().with_user(self.user).mobile_comment("   ")


@tagged("post_install", "-at_install", "bf_calendar_mobile")
class TestRoutesActivites(HttpCase):
    """Le contrat de bout en bout : jeton, idempotence, refus défait en entier."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        def usager(login):
            return cls.env["res.users"].create({
                "name": login, "login": login, "email": "%s@example.org" % login,
                "groups_id": [(6, 0, _groupes(cls.env))],
            })

        cls.user = usager("routes.activites.t")
        cls.autre = usager("routes.activites.autre.t")
        cls.projet = cls.env["project.project"].create({"name": "Banc routes"})
        cls.tache = cls.env["project.task"].create({
            "name": "Tâche routes", "project_id": cls.projet.id,
            "user_ids": [(6, 0, cls.user.ids)]})
        Device = cls.env["bf.email.mobile.device"]
        cls.jeton = Device._issue(cls.user.id, name="Banc activites").device_token

    def _get(self, route, jeton=None):
        return self.url_open(BASE + route, headers={
            "Authorization": "Bearer %s" % (jeton or self.jeton)}, timeout=30)

    def _post(self, route, charge, jeton=None):
        return self.url_open(
            BASE + route, data=json.dumps(charge),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer %s" % (jeton or self.jeton)}, timeout=30)

    def _activite(self, user=None):
        return self.tache.activity_schedule(
            activity_type_id=_todo(self.env).id, user_id=(user or self.user).id,
            summary="Par la route")

    def test_ping_annonce_l_api_8(self):
        self.assertEqual(self.url_open(BASE + "/ping").json()["api"], 8)

    def test_sans_jeton_401(self):
        r = self.url_open(BASE + "/activities")
        self.assertEqual(r.status_code, 401)
        r = self.url_open(BASE + "/activity/done", data="{}",
                          headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 401)

    def test_liste_puis_fait_rejoue_une_seule_fois(self):
        activite = self._activite()
        liste = self._get("/activities").json()
        self.assertIn(activite.id, [a["id"] for a in liste["activities"]])
        cle = str(uuid.uuid4())
        a = self._post("/activity/done", {"activity_id": activite.id, "client_uuid": cle})
        b = self._post("/activity/done", {"activity_id": activite.id, "client_uuid": cle})
        self.assertEqual((a.status_code, b.status_code), (200, 200), a.text)
        self.assertTrue(b.json()["replay"])
        self.assertFalse(activite.exists().filtered("active"))

    def test_activite_inconnue_ou_corps_non_objet_sans_500(self):
        self.assertEqual(self._post("/activity/done", {"activity_id": 999999}).status_code, 404)
        r = self.url_open(BASE + "/activity/reschedule", data="[1, 2]", headers={
            "Content-Type": "application/json", "Authorization": "Bearer %s" % self.jeton})
        self.assertEqual(r.status_code, 404)
        r = self._post("/activity/reschedule", {"activity_id": self._activite().id, "date": "x"})
        self.assertEqual(r.status_code, 400)

    def test_planifier_refuse_ne_laisse_rien(self):
        avant = self.env["mail.activity"].search_count([])
        r = self._post("/activity/create", {
            "res_model": "project.task", "res_id": self.tache.id,
            "activity_type_id": _todo(self.env).id, "date": "pas une date"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.env["mail.activity"].search_count([]), avant)

    def test_une_activite_deja_faite_et_gardee_rend_404(self):
        """Type « garder les activités faites » : l'activité faite est archivée,
        pas supprimée. La refaire doit rendre 404, pas un second « fait »."""
        garde = self.env["mail.activity.type"].create({"name": "Gardée", "keep_done": True})
        activite = self.tache.activity_schedule(activity_type_id=garde.id, user_id=self.user.id)
        premier = self._post("/activity/done", {"activity_id": activite.id, "client_uuid": str(uuid.uuid4())})
        self.assertEqual(premier.status_code, 200, premier.text)
        self.assertFalse(activite.with_context(active_test=False).exists().active)
        messages = len(self.tache.message_ids)
        second = self._post("/activity/done", {"activity_id": activite.id, "client_uuid": str(uuid.uuid4())})
        self.assertEqual(second.status_code, 404)
        self.tache.invalidate_recordset(["message_ids"])
        self.assertEqual(len(self.tache.message_ids), messages)
        reporte = self._post("/activity/reschedule", {"activity_id": activite.id, "date": "2026-12-01"})
        self.assertEqual(reporte.status_code, 404)

    def test_commentaire_par_la_route(self):
        r = self._post("/task/comment", {"task_id": self.tache.id, "body": "Depuis le téléphone"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Depuis le téléphone", self.tache.message_ids[:1].body or "")
        self.assertTrue(r.json()["task"]["detail"])

    def test_fiche_par_la_route_porte_le_detail(self):
        r = self._get("/task?id=%s" % self.tache.id)
        self.assertTrue(r.json()["task"]["detail"])
        self.assertIn("activities", r.json()["task"])

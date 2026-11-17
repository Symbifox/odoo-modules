"""La route publique du dépôt de l'agent de conteneurs.

C'est la seule surface PUBLIQUE du lot, et la du 2026-11-12 a
constaté qu'aucun essai ne la touchait. Ce qui est tenu ici :

* le jeton : absent du réglage → 503 ; en-tête absent ou faux → 401 ; et rien
  n'est écrit dans les trois cas ;
* la charge est authentifiée, pas digne de confiance : un nom de conteneur qui
  n'en est pas un, un état de sonde inventé, un compteur qui n'est pas un
  nombre, un détail de 900 caractères, un `inf` JSON — rien de tout ça ne doit
  entrer en base ni lever ;
* la route n'écrit QUE sur des fiches qui existent déjà et qui ont demandé
  cette sonde. Elle ne crée aucun service, et un conteneur qu'Odoo ne connaît
  pas n'est pas une erreur — c'est le cas de la plupart ;
* un même conteneur peut porter plusieurs fiches (allocations par client) :
  les deux reçoivent le dépôt.
"""

import json

from odoo.tests import HttpCase, tagged

JETON = "jeton-d-essai-de-la-route"
CONTENEUR = "essai-conteneur"


@tagged("post_install", "-at_install")
class TestRouteAgentConteneurs(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.icp = cls.env["ir.config_parameter"].sudo()
        cls.partner = cls.env["res.partner"].create({"name": "Client de la route"})
        cls.software = cls.env["hosting.software"].create(
            {"name": "Logiciel de la route", "code": "route27469"})
        cls.service = cls.env["hosting.service"].create({
            "name": "Conteneur de la route",
            "partner_id": cls.partner.id,
            "software_id": cls.software.id,
            "state": "active",
            "health_kind": "docker",
            "docker_container": CONTENEUR,
        })

    def _poster(self, charge, jeton=JETON, brut=None):
        entetes = {"Content-Type": "application/json"}
        if jeton is not None:
            entetes["X-Agent-Token"] = jeton
        data = brut if brut is not None else json.dumps(charge).encode()
        return self.url_open("/api/hosting/agent/containers/public",
                             data=data, headers=entetes)

    def _un_conteneur(self, **kw):
        fait = {"container": CONTENEUR, "running": True, "health": "healthy"}
        fait.update(kw)
        return {"host": "banc", "containers": [fait]}

    # ── Le jeton ─────────────────────────────────────────────────────────
    def test_sans_jeton_configure_la_route_refuse(self):
        self.icp.set_param("hosting.agent_api_token", "")
        rep = self._poster(self._un_conteneur())
        self.assertEqual(rep.status_code, 503, rep.text)
        self.service.invalidate_recordset()
        self.assertFalse(self.service.agent_report_at)

    def test_un_jeton_faux_ne_depose_rien(self):
        self.icp.set_param("hosting.agent_api_token", JETON)
        rep = self._poster(self._un_conteneur(), jeton="pas-le-bon")
        self.assertEqual(rep.status_code, 401, rep.text)
        self.service.invalidate_recordset()
        self.assertFalse(self.service.agent_report_at)

    def test_sans_en_tete_du_tout(self):
        self.icp.set_param("hosting.agent_api_token", JETON)
        rep = self._poster(self._un_conteneur(), jeton=None)
        self.assertEqual(rep.status_code, 401, rep.text)

    # ── Les formes de charge ─────────────────────────────────────────────
    def test_un_corps_qui_nest_pas_du_json(self):
        self.icp.set_param("hosting.agent_api_token", JETON)
        rep = self._poster(None, brut=b"pas du json")
        self.assertEqual(rep.status_code, 400, rep.text)

    def test_une_charge_sans_liste_de_conteneurs(self):
        self.icp.set_param("hosting.agent_api_token", JETON)
        rep = self._poster({"host": "banc"})
        self.assertEqual(rep.status_code, 400, rep.text)

    def test_le_plafond_de_conteneurs(self):
        self.icp.set_param("hosting.agent_api_token", JETON)
        rep = self._poster({"containers": [
            {"container": "c%d" % i, "running": True} for i in range(600)]})
        self.assertEqual(rep.status_code, 413, rep.text)

    def test_des_noms_qui_ne_sont_pas_des_conteneurs(self):
        self.icp.set_param("hosting.agent_api_token", JETON)
        rep = self._poster({"containers": [
            {"container": "../../etc/passwd", "running": True},
            {"container": "a b; rm -rf /", "running": True},
            {"container": 1234, "running": True},
            {"container": "", "running": True},
        ]})
        # Aucun nom utilisable : la route refuse au lieu d'écrire à moitié.
        self.assertEqual(rep.status_code, 400, rep.text)

    def test_des_valeurs_inventees_sont_ramenees_a_la_selection(self):
        self.icp.set_param("hosting.agent_api_token", JETON)
        # ⚠️ `running` reste un VRAI booléen ici : c'est le seul champ qui ne
        # se coerce pas (voir l'essai du booléen jeté). Les trois autres se
        # ramènent à ce que la sélection accepte.
        rep = self._poster(self._un_conteneur(
            running=True, health="inventée", restart_count="beaucoup",
            detail="X" * 900))
        self.assertEqual(rep.status_code, 200, rep.text)
        self.service.invalidate_recordset()
        self.assertTrue(self.service.agent_container_running)
        self.assertEqual(self.service.agent_container_health, "none")
        self.assertEqual(self.service.agent_restart_count, 0)
        self.assertLessEqual(len(self.service.agent_detail or ""), 200)

    def test_un_compteur_infini_ne_leve_pas(self):
        """`json.loads('1e400')` rend `inf`, et `int(inf)` lève OverflowError."""
        self.icp.set_param("hosting.agent_api_token", JETON)
        rep = self._poster(None, brut=json.dumps(
            {"containers": [{"container": CONTENEUR, "running": True,
                             "restart_count": 1e400}]}
        ).encode().replace(b"Infinity", b"1e400"))
        self.assertEqual(rep.status_code, 200, rep.text)
        self.service.invalidate_recordset()
        self.assertEqual(self.service.agent_restart_count, 0)

    # ── Ce que la route écrit, et ce qu'elle refuse d'écrire ─────────────
    def test_un_depot_valide_atterrit_sur_la_fiche(self):
        self.icp.set_param("hosting.agent_api_token", JETON)
        rep = self._poster(self._un_conteneur(
            health="unhealthy", restart_count=7, detail="etat=running"))
        self.assertEqual(rep.status_code, 200, rep.text)
        corps = rep.json()
        self.assertTrue(corps["success"])
        self.assertEqual(corps["matched"], 1)
        self.service.invalidate_recordset()
        self.assertTrue(self.service.agent_report_at)
        self.assertEqual(self.service.agent_container_health, "unhealthy")
        self.assertEqual(self.service.agent_restart_count, 7)

    def test_un_conteneur_inconnu_dodoo_ne_cree_rien(self):
        self.icp.set_param("hosting.agent_api_token", JETON)
        avant = self.env["hosting.service"].search_count([])
        rep = self._poster({"containers": [
            {"container": "conteneur-que-personne-ne-suit", "running": False}]})
        self.assertEqual(rep.status_code, 200, rep.text)
        self.assertEqual(rep.json()["matched"], 0)
        self.assertEqual(self.env["hosting.service"].search_count([]), avant,
                         "la route ne doit créer aucune fiche")

    def test_une_fiche_en_sonde_http_ne_recoit_rien(self):
        """La route n'écrit que sur les fiches qui ont DEMANDÉ cette sonde."""
        self.icp.set_param("hosting.agent_api_token", JETON)
        http = self.env["hosting.service"].create({
            "name": "Fiche en sonde HTTP",
            "partner_id": self.partner.id,
            "software_id": self.software.id,
            "state": "active",
            "server_url": "https://exemple.invalid/sante",
            "docker_container": CONTENEUR,
        })
        rep = self._poster(self._un_conteneur())
        self.assertEqual(rep.status_code, 200, rep.text)
        http.invalidate_recordset()
        self.assertFalse(http.agent_report_at)

    def test_deux_fiches_sur_un_meme_conteneur_recoivent_les_deux(self):
        self.icp.set_param("hosting.agent_api_token", JETON)
        jumelle = self.env["hosting.service"].create({
            "name": "Jumelle du même conteneur",
            "partner_id": self.partner.id,
            "software_id": self.software.id,
            "state": "active",
            "health_kind": "docker",
            "docker_container": CONTENEUR,
        })
        rep = self._poster(self._un_conteneur())
        self.assertEqual(rep.status_code, 200, rep.text)
        self.assertEqual(rep.json()["matched"], 2)
        jumelle.invalidate_recordset()
        self.assertTrue(jumelle.agent_report_at)

    def test_un_jeton_laisse_a_sa_valeur_de_gabarit_refuse(self):
        """Le 503 ne doit pas tenir qu'à la chaîne vide."""
        self.icp.set_param("hosting.agent_api_token", "CHANGE_ME")
        rep = self._poster(self._un_conteneur())
        self.assertEqual(rep.status_code, 503, rep.text)

    def test_la_route_nest_pas_ouverte_en_lecture(self):
        self.icp.set_param("hosting.agent_api_token", JETON)
        rep = self.url_open("/api/hosting/agent/containers/public")
        self.assertEqual(rep.status_code, 405, rep.text)

    def test_la_route_necrit_aucun_releve_de_sante(self):
        """C'est le cron qui juge : la route ne dépose que des faits."""
        self.icp.set_param("hosting.agent_api_token", JETON)
        avant = self.env["hosting.health.check"].search_count(
            [("service_id", "=", self.service.id)])
        self._poster(self._un_conteneur(health="unhealthy"))
        self.assertEqual(
            self.env["hosting.health.check"].search_count(
                [("service_id", "=", self.service.id)]),
            avant, "la route ne doit écrire aucun relevé de santé")

    def test_une_fiche_hors_de_portee_ne_recoit_rien(self):
        """⚠️ Le même périmètre que le cron : ni annulée, ni exemptée."""
        self.icp.set_param("hosting.agent_api_token", JETON)
        annulee = self.env["hosting.service"].create({
            "name": "Fiche annulée du même conteneur",
            "partner_id": self.partner.id, "software_id": self.software.id,
            "state": "cancelled", "health_kind": "docker",
            "docker_container": CONTENEUR,
        })
        exemptee = self.env["hosting.service"].create({
            "name": "Fiche exemptée du même conteneur",
            "partner_id": self.partner.id, "software_id": self.software.id,
            "state": "active", "health_kind": "docker",
            "docker_container": CONTENEUR,
            "health_exempt": True, "health_exempt_reason": "hors de portée",
        })
        rep = self._poster(self._un_conteneur())
        self.assertEqual(rep.json()["matched"], 1, rep.text)
        for fiche in (annulee, exemptee):
            fiche.invalidate_recordset()
            self.assertFalse(fiche.agent_report_at)

    def test_un_running_qui_nest_pas_un_booleen_est_jete(self):
        """🔴 `bool("false")` rend True : le champ dont dépend un DOWN ne se
        coerce pas, il se valide. Une charge mal formée ne doit fabriquer ni
        panne ni fausse santé."""
        self.icp.set_param("hosting.agent_api_token", JETON)
        rep = self._poster({"containers": [
            {"container": CONTENEUR, "running": "false", "health": "healthy"}]})
        self.assertEqual(rep.status_code, 400, rep.text)
        self.service.invalidate_recordset()
        self.assertFalse(self.service.agent_report_at)

    def test_la_reponse_ne_nomme_pas_les_conteneurs_suivis(self):
        """⚠️ Un porteur du jeton ne doit pas apprendre la liste des clients."""
        self.icp.set_param("hosting.agent_api_token", JETON)
        rep = self._poster({"containers": [
            {"container": "inconnu-de-la-base", "running": True}]})
        corps = rep.json()
        self.assertNotIn("expected_missing", corps)
        self.assertNotIn(CONTENEUR, json.dumps(corps))

# -*- coding: utf-8 -*-
"""Sondes de conteneurs et fin du 100 % qui ne mesure rien.

Ce que ces essais tiennent en place, et pourquoi chacun est écrit :

* 🔴 Un service que RIEN n'a mesuré ne rend plus 100 % de disponibilité ni
  « en ligne ». C'est le défaut qui a coûté la panne du 2026-11-06 : le service
  de réunion d'un client était à terre, sa fiche affichait `uptime_30d = 100,0`
  et le tableau de bord 100 % de disponibilité ;
* ⚠️ Et le correctif ne doit pas déplacer le mensonge : `is_service_up = False`
  sur un service sans mesure ne doit pas remplir la liste des pannes. Les deux
  assertions vont ensemble, un essai qui ne tient que la première laisse passer
  vingt-quatre fausses pannes ;
* un dépôt d'agent périmé rend « on ne sait pas », jamais « en panne » : une
  panne de l'agent ne doit pas se déguiser en panne de dix-huit services ;
* un relevé « on ne sait pas » n'alerte pas, ne résorbe pas une alerte en
  cours, et ne compte pas dans la disponibilité ;
* ⚠️ Il ne compte pas non plus dans la fenêtre d'amortissement : sans ça, le
  premier vrai défaut suivant un silence de l'agent alerterait tout de suite et
  l'amortissement ne vaudrait plus rien ;
* « le conteneur tourne » n'est pas « le service répond » quand l'image ne
  déclare aucune sonde : le constat doit le dire.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSondesConteneurs(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = cls.env["res.partner"].create({"name": "Locataire "})
        cls.logiciel = cls.env["hosting.software"].create({
            "name": "Logiciel de banc ",
            "code": "T27469",
            "software_type": "self_hosted",
        })

    def _service(self, nom, **extra):
        vals = {
            "name": nom,
            "partner_id": self.client.id,
            "software_id": self.logiciel.id,
            "state": "active",
        }
        vals.update(extra)
        return self.env["hosting.service"].create(vals)

    def _relever(self, service, statut, minutes=0):
        """Écrire un relevé daté, sans passer par la sonde."""
        return self.env["hosting.health.check"].create({
            "service_id": service.id,
            "status": statut,
            "check_date": fields.Datetime.now() - timedelta(minutes=minutes),
        })

    # ── Le mensonge d'origine ────────────────────────────────────────────
    def test_sans_releve_aucune_disponibilite_annoncee(self):
        aveugle = self._service("Service jamais sondé")
        self.assertEqual(aveugle.health_coverage, "no_data")
        self.assertEqual(aveugle.last_health_status, "no_data")
        # 🔴 Le coeur du défaut : 100,0 se lisait comme une bonne nouvelle.
        self.assertNotEqual(aveugle.uptime_30d, 100.0)
        self.assertEqual(aveugle.uptime_30d, 0.0)
        self.assertFalse(aveugle.is_service_up)
        # ⚠️ Le libellé est traduit dans la langue du lecteur : on juge sa
        # FORME (une mention, pas un pourcentage), jamais ses mots — un essai
        # qui compare « Not monitored » rougit le jour où le banc lit français.
        self.assertTrue(aveugle.uptime_30d_display)
        self.assertNotIn("%", aveugle.uptime_30d_display)

    def test_sans_releve_hors_de_la_liste_des_pannes(self):
        """⚠️ Le correctif ne doit pas peindre les aveugles en pannes."""
        aveugle = self._service("Service jamais sondé, pas en panne")
        action = self.env["hosting.dashboard"].action_view_health_issues()
        pannes = self.env["hosting.service"].search(action["domain"])
        self.assertNotIn(aveugle, pannes)
        sans_sonde = self.env["hosting.service"].search(
            self.env["hosting.dashboard"]
            .action_view_unmonitored_services()["domain"]
        )
        self.assertIn(aveugle, sans_sonde)

    def test_sans_nouvelles_n_est_pas_une_panne_mais_une_panne_en_est_une(self):
        """🔴 Les deux moitiés comptent.

        Un service dont l'agent ne dit plus rien ne doit pas gonfler la liste
        des pannes — et la liste doit quand même attraper une vraie panne,
        sinon l'essai ne prouverait que son propre vide.
        """
        muet = self._service("Service sans nouvelles de l'agent")
        self._relever(muet, "unknown")
        tombe = self._service("Service réellement tombé")
        self._relever(tombe, "down")
        action = self.env["hosting.dashboard"].action_view_health_issues()
        pannes = self.env["hosting.service"].search(action["domain"])
        self.assertNotIn(muet, pannes)
        self.assertIn(tombe, pannes)

    def test_un_service_qui_na_que_des_inconnus_reste_sans_releve(self):
        """⚠️ Les deux champs d'une même idée, sur la MÊME condition.

        `health_coverage` passait à « Éprouvé » dès qu'il existait un relevé,
        même `unknown` : la fiche montrait un badge vert, une barre de
        progression à 0 % — et CACHAIT la mention « Non surveillé », la seule
        qui disait juste.
        """
        service = self._service("Service dont l'agent ne dit rien")
        self._relever(service, "unknown")
        self._relever(service, "unknown")
        service.invalidate_recordset()
        self.assertEqual(service.health_coverage, "no_data")
        self.assertEqual(service.last_health_status, "unknown")
        self.assertEqual(service.uptime_30d, 0.0)
        self.assertNotIn("%", service.uptime_30d_display)

    def test_un_service_mesure_une_fois_est_eprouve(self):
        """L'autre moitié : une mesure, même ancienne, vaut « éprouvé »."""
        service = self._service("Service mesuré puis muet")
        self._relever(service, "up", minutes=120)
        self._relever(service, "unknown")
        service.invalidate_recordset()
        self.assertEqual(service.health_coverage, "probed")
        self.assertEqual(service.uptime_30d, 100.0)

    def test_exempte_ne_compte_pas_parmi_les_aveugles(self):
        # 🔴 AVEC une URL, et c'est tout le sujet : sans elle, la fiche sortait
        # du domaine du cron parce qu'elle n'avait rien à interroger, pas parce
        # qu'elle était exemptée. L'essai passait, et retirer le filtre
        # `health_exempt` ne faisait rien tomber (mutant 7 survivant, mesuré le
        # 2026-11-12). Une fiche qui qualifie AUTREMENT est la seule qui mesure
        # l'exemption.
        exempte = self._service(
            "Fournisseur externe", health_exempt=True,
            health_exempt_reason="Migadu : aucune sonde possible",
            server_url="https://migadu.example.invalid/sante",
        )
        self.assertEqual(exempte.health_coverage, "exempt")
        self.assertFalse(exempte.is_service_up)
        sans_sonde = self.env["hosting.service"].search(
            self.env["hosting.dashboard"]
            .action_view_unmonitored_services()["domain"]
        )
        self.assertNotIn(exempte, sans_sonde)
        # Et le cron ne la sonde pas : rien à éprouver, pas de relevé à écrire.
        domaine = self.env["hosting.service"]._domaine_sonde_active()
        sondes = self.env["hosting.service"].search(domaine)
        self.assertNotIn(exempte, sondes)
        # L'autre moitié : la MÊME fiche sans l'exemption y entrerait, sinon
        # l'assertion ci-dessus ne prouverait que l'absence d'URL.
        exempte.write({"health_exempt": False, "health_exempt_reason": False})
        self.assertIn(
            exempte, self.env["hosting.service"].search(domaine),
            "sans l'exemption, cette fiche doit entrer dans le domaine du cron")

    def test_un_service_docker_entre_dans_le_domaine_du_cron(self):
        """Une sonde de conteneur n'a pas d'URL : elle doit quand même tourner."""
        dock = self._service(
            "Conteneur sans URL", health_kind="docker",
            docker_container="essai-temoin",
        )
        domaine = self.env["hosting.service"]._domaine_sonde_active()
        self.assertIn(dock, self.env["hosting.service"].search(domaine))

    # ── La sonde de conteneur ────────────────────────────────────────────
    def _dock(self, **depot):
        vals = {
            "health_kind": "docker",
            "docker_container": "essai-temoin",
        }
        vals.update(depot)
        return self._service("Conteneur éprouvé", **vals)

    def test_depot_absent_rend_on_ne_sait_pas(self):
        statut, _ms, _code, msg = self._dock()._do_docker_check()
        self.assertEqual(statut, "unknown")
        self.assertIn("essai-temoin", msg)

    def test_depot_frais_et_conteneur_sain(self):
        dock = self._dock(
            agent_report_at=fields.Datetime.now(),
            agent_container_running=True,
            agent_container_health="healthy",
        )
        statut, _ms, _code, msg = dock._do_docker_check()
        self.assertEqual(statut, "up")
        self.assertFalse(msg)

    def test_conteneur_arrete_est_une_panne(self):
        dock = self._dock(
            agent_report_at=fields.Datetime.now(),
            agent_container_running=False,
            agent_detail="arrete (etat=exited, code=1, fin=2026-11-12T05:00:00)",
        )
        statut, _ms, _code, msg = dock._do_docker_check()
        self.assertEqual(statut, "down")
        self.assertIn("exited", msg)

    def test_conteneur_unhealthy_est_degrade(self):
        """🔴 C'est la forme exacte de la panne du 2026-11-06 : running + unhealthy."""
        dock = self._dock(
            agent_report_at=fields.Datetime.now(),
            agent_container_running=True,
            agent_container_health="unhealthy",
        )
        statut, _ms, _code, _msg = dock._do_docker_check()
        self.assertEqual(statut, "degraded")

    def test_sans_sonde_interne_le_vert_dit_sa_faiblesse(self):
        dock = self._dock(
            agent_report_at=fields.Datetime.now(),
            agent_container_running=True,
            agent_container_health="none",
        )
        statut, _ms, _code, msg = dock._do_docker_check()
        self.assertEqual(statut, "up")
        self.assertTrue(msg, "un vert de vivacité seule doit porter son constat")
        self.assertIn("essai-temoin", msg)

    def test_depot_perime_rend_on_ne_sait_pas_pas_une_panne(self):
        dock = self._dock(
            agent_report_at=fields.Datetime.now() - timedelta(minutes=90),
            agent_container_running=True,
            agent_container_health="healthy",
        )
        statut, _ms, _code, msg = dock._do_docker_check()
        self.assertEqual(statut, "unknown")
        self.assertIn("essai-temoin", msg)

    # ── Le canari de l'agent ─────────────────────────────────────────────
    def test_canari_crie_une_fois_quand_l_agent_se_tait(self):
        """⚠️ Périmètre EXPLICITE : la sonde lit toute la base en production.

        Sans l'argument, cet essai passait au banc (aucune fiche `docker` :
        la configuration du lot vit dans le script de pose) et rougissait sur
        la base réelle, où dix-sept vraies fiches répondent. Un essai vert qui
        ne mesure rien aujourd'hui et rougit demain.
        """
        muets = self.env["hosting.service"]
        for _ in range(3):
            muets |= self._dock(
                agent_report_at=fields.Datetime.now() - timedelta(minutes=90))
        canari = self._service("Agent de conteneurs (canari)",
                              health_kind="container_agent")
        statut, _ms, _code, msg = canari._do_container_agent_check(services=muets)
        self.assertEqual(statut, "down")
        self.assertTrue(msg)

    def test_le_canari_couvre_les_fiches_en_sonde_de_conteneur(self):
        """Et le périmètre réel se vérifie, sinon l'argument le cacherait."""
        dock = self._dock(agent_report_at=fields.Datetime.now())
        exempte = self._dock(agent_report_at=False)
        exempte.write({"health_exempt": True,
                       "health_exempt_reason": "hors de portée"})
        couverts = self.env["hosting.service"].search(
            self.env["hosting.service"]._domaine_sonde_conteneur())
        self.assertIn(dock, couverts)
        self.assertNotIn(exempte, couverts)

    def test_canari_vert_quand_tout_est_frais(self):
        frais = self._dock(agent_report_at=fields.Datetime.now(),
                           agent_container_running=True,
                           agent_container_health="healthy")
        canari = self._service("Agent de conteneurs (canari) 2",
                              health_kind="container_agent")
        statut, ms, _code, msg = canari._do_container_agent_check(services=frais)
        self.assertEqual(statut, "up")
        self.assertFalse(msg)
        # 🔴 Pas de millisecondes : la sonde connaît un ÂGE, pas une latence.
        # L'y mettre faisait partir un courriel « service lent » à chaque
        # passe du cron, soit ~1 400 par jour, malgré le silence d'alerte.
        self.assertIsNone(ms, "le canari ne doit pas rendre de temps de réponse")

    def test_un_depot_date_dans_le_futur_nest_pas_frais(self):
        """Deux juges, une seule règle sur l'horloge qui recule.

        Le canari refusait déjà un âge négatif ; la sonde du conteneur, elle,
        ne testait que « plus vieux que la limite ». Un dépôt daté dans le
        futur s'y lisait frais, donc le canari criait pendant que les dix-sept
        fiches restaient vertes.
        """
        dock = self._dock(
            agent_report_at=fields.Datetime.now() + timedelta(hours=3),
            agent_container_running=True,
            agent_container_health="healthy",
        )
        statut, _ms, _code, msg = dock._do_docker_check()
        self.assertEqual(statut, "unknown")
        self.assertIn("essai-temoin", msg)

    def test_canari_rien_a_surveiller_nest_pas_un_vert(self):
        canari = self._service("Agent de conteneurs (canari) 4",
                              health_kind="container_agent")
        vide = self.env["hosting.service"]
        statut, _ms, _code, _msg = canari._do_container_agent_check(services=vide)
        self.assertEqual(statut, "unknown")

    def test_un_depot_dans_le_futur_compte_comme_muet(self):
        """Si l'horloge recule, un dépôt mort ne doit pas se lire frais."""
        futur = self._dock(
            agent_report_at=fields.Datetime.now() + timedelta(hours=3),
            agent_container_running=True, agent_container_health="healthy")
        canari = self._service("Agent de conteneurs (canari) 5",
                              health_kind="container_agent")
        statut, _ms, _code, _msg = canari._do_container_agent_check(services=futur)
        self.assertEqual(statut, "down")

    def test_canari_signale_un_releve_partiel(self):
        frais = self._dock(agent_report_at=fields.Datetime.now(),
                           agent_container_running=True,
                           agent_container_health="healthy")
        muet = self._dock(agent_report_at=False)
        muet.docker_container = "essai-muet"
        canari = self._service("Agent de conteneurs (canari) 3",
                              health_kind="container_agent")
        statut, _ms, _code, msg = canari._do_container_agent_check(
            services=frais | muet)
        self.assertEqual(statut, "degraded")
        self.assertIn("essai-muet", msg)

    # ── « On ne sait pas » ne ment pas dans les chiffres ─────────────────
    def test_releve_inconnu_hors_de_la_disponibilite(self):
        service = self._service("Service à relevés mêlés")
        self._relever(service, "up", minutes=3)
        self._relever(service, "unknown", minutes=2)
        service.invalidate_recordset(["uptime_30d", "uptime_30d_display"])
        self.assertEqual(service.uptime_30d, 100.0)
        self.assertEqual(service.uptime_30d_display, "100.0 %")

    def test_releve_inconnu_n_alerte_pas(self):
        service = self._service("Service sans nouvelles")
        Service = self.env["hosting.service"]
        for _ in range(3):
            bascule = Service._sante_enregistrer(
                service, ("unknown", None, None, "agent muet"), 2)
            self.assertIsNone(bascule)
        self.assertFalse(service.health_alert_active)

    def test_releve_inconnu_ne_resorbe_pas_une_alerte(self):
        service = self._service("Service en panne puis sans nouvelles")
        Service = self.env["hosting.service"]
        Service._sante_enregistrer(service, ("down", None, None, "panne"), 2)
        bascule = Service._sante_enregistrer(service, ("down", None, None, "panne"), 2)
        self.assertEqual(bascule, "down")
        self.assertTrue(service.health_alert_active)
        bascule = Service._sante_enregistrer(service, ("unknown", None, None, "muet"), 2)
        self.assertIsNone(bascule)
        self.assertTrue(
            service.health_alert_active,
            "une panne signalée ne se résorbe pas parce qu'on a cessé de mesurer",
        )

    def test_releve_inconnu_hors_de_la_fenetre_d_amortissement(self):
        """⚠️ Sinon le premier vrai défaut après un silence alerte aussitôt."""
        service = self._service("Service muet puis en défaut")
        Service = self.env["hosting.service"]
        Service._sante_enregistrer(service, ("unknown", None, None, "muet"), 2)
        Service._sante_enregistrer(service, ("unknown", None, None, "muet"), 2)
        bascule = Service._sante_enregistrer(service, ("down", None, None, "panne"), 2)
        self.assertIsNone(
            bascule, "un seul relevé en défaut ne doit pas alerter à deux de seuil")
        bascule = Service._sante_enregistrer(service, ("down", None, None, "panne"), 2)
        self.assertEqual(bascule, "down")

    # ── La consolidation, dont l'erreur serait définitive ────────────────
    def test_la_consolidation_ne_compte_pas_les_inconnus(self):
        """🔴 Le seul endroit où se tromper est irréversible.

        La consolidation quotidienne écrit un pourcentage dans un registre
        permanent, PUIS supprime les relevés bruts. Les `unknown` comptaient
        au dénominateur et pas au numérateur : une journée d'agent muet se
        figeait à 0 % pour toujours.
        """
        service = self._service("Service consolidé")
        vieux = fields.Datetime.now() - timedelta(days=45)
        for statut in ("up", "up", "unknown", "unknown", "unknown"):
            self._relever(service, statut)
            self.env["hosting.health.check"].search(
                [("service_id", "=", service.id)], order="id desc", limit=1
            ).check_date = vieux
        # ⚠️ La consolidation agrège en SQL : sans ce vidage, elle ne voit
        # aucun des relevés que l'ORM garde encore en cache, et l'essai rend
        # « 0 instantané » en n'ayant rien éprouvé.
        self.env.flush_all()
        self.env["hosting.health.check"]._cron_consolidate_and_purge()
        self.env.invalidate_all()
        photo = self.env["hosting.health.daily.snapshot"].search(
            [("service_id", "=", service.id)])
        self.assertEqual(len(photo), 1)
        self.assertEqual(photo.check_count, 5)
        self.assertEqual(photo.up_count, 2)
        self.assertEqual(photo.unknown_count, 3)
        self.assertEqual(
            photo.uptime_pct, 100.0,
            "deux mesures sur deux au vert : 100 %, pas 40 %")

    def test_une_journee_sans_mesure_se_qualifie_dans_l_archive(self):
        """🔴 Le faux mauvais, et il est aussi définitif que le faux bon.

        Le tour 1 a retiré le 100 % d'une journée sans mesure ; reste le 0 %,
        que les décorations peignent en rouge et que les filtres « Below SLA »
        indexent — dans un registre permanent, après la purge des relevés
        bruts. `measured_count` est ce qui permet à la ligne de se qualifier.
        """
        service = self._service("Service consolidé sans mesure du tout")
        vieux = fields.Datetime.now() - timedelta(days=45)
        for statut in ("unknown", "unknown", "up"):
            self._relever(service, statut)
            self.env["hosting.health.check"].search(
                [("service_id", "=", service.id)], order="id desc", limit=1
            ).check_date = vieux
        self.env.flush_all()
        self.env["hosting.health.check"]._cron_consolidate_and_purge()
        self.env.invalidate_all()
        photo = self.env["hosting.health.daily.snapshot"].search(
            [("service_id", "=", service.id)])
        self.assertEqual(photo.measured_count, 1)
        self.assertEqual(photo.unknown_count, 2)
        self.assertIn("100.0", photo.display_name)

    def test_une_journee_sans_aucune_mesure_ne_vaut_pas_cent(self):
        service = self._service("Service consolidé sans mesure")
        vieux = fields.Datetime.now() - timedelta(days=45)
        for _ in range(4):
            self._relever(service, "unknown")
            self.env["hosting.health.check"].search(
                [("service_id", "=", service.id)], order="id desc", limit=1
            ).check_date = vieux
        # ⚠️ La consolidation agrège en SQL : sans ce vidage, elle ne voit
        # aucun des relevés que l'ORM garde encore en cache, et l'essai rend
        # « 0 instantané » en n'ayant rien éprouvé.
        self.env.flush_all()
        self.env["hosting.health.check"]._cron_consolidate_and_purge()
        self.env.invalidate_all()
        photo = self.env["hosting.health.daily.snapshot"].search(
            [("service_id", "=", service.id)])
        self.assertEqual(photo.unknown_count, 4)
        self.assertNotEqual(
            photo.uptime_pct, 100.0,
            "un 100 % dans un registre permanent, sans une seule mesure")
        self.assertEqual(photo.uptime_pct, 0.0)
        # ⚠️ Et c'est `measured_count` qui dit que ce 0 ne veut rien dire : les
        # filtres SLA et les décorations de la vue l'exigent.
        self.assertEqual(photo.measured_count, 0)
        self.assertNotIn("0.0%", photo.display_name)

    # ── Les deux nombres du tableau de bord s'additionnent ───────────────
    def test_surveilles_plus_sans_sonde_font_le_parc(self):
        """🔴 Un service MESURÉ hors des deux nombres, c'est un angle mort.

        La portée du tableau était « a une URL, ou n'est pas HTTP » : une fiche
        en sonde HTTP sans URL, alimentée par un autre écrivain (le PBX en
        production), tombait hors de « surveillés » ET de « sans sonde » — et
        hors de « services en panne ». Les deux nombres doivent faire le parc.
        """
        hors_portee = self._service("Sondé par un autre écrivain que le cron")
        self._relever(hors_portee, "up")
        aveugle = self._service("Jamais mesuré, pour le décompte")
        exempte = self._service("Hors de portée de toute sonde",
                                health_exempt=True,
                                health_exempt_reason="fournisseur externe")
        self.env.flush_all()
        self.env.invalidate_all()
        apercu = self.env["hosting.dashboard"]._get_uptime_overview()
        attendu = self.env["hosting.service"].search_count([
            ("state", "=", "active"), ("health_exempt", "=", False)])
        self.assertEqual(
            apercu["monitored_services"] + apercu["unmonitored_services"],
            attendu,
            "surveillés + sans sonde doit faire les fiches actives non exemptées")
        # Et les trois fiches de l'essai sont du bon côté.
        self.assertEqual(hors_portee.health_coverage, "probed")
        self.assertEqual(aveugle.health_coverage, "no_data")
        self.assertEqual(exempte.health_coverage, "exempt")

    def test_la_charge_du_tableau_passe_par_xmlrpc(self):
        """🔴 Deux transports, et un seul était éprouvé.

        Le tableau de bord part aussi par XML-RPC (outils MCP, skills), dont le
        sérialiseur d'Odoo REFUSE `None` : « cannot marshal None unless
        allow_none is enabled ». Le navigateur, lui, passe par JSON-RPC et
        accepte `null` — un parcours au navigateur vert ne dit donc rien du
        second transport. Mesuré en production cinq minutes après la pose.
        """
        from odoo.addons.base.controllers.rpc import OdooMarshaller

        # Une fiche sans aucun relevé dans la fenêtre : c'est elle qui portait
        # le `None` (disponibilité d'une fenêtre vide).
        self._service("Service sans relevé pour la charge",
                      server_url="https://charge.invalid/sante")
        self.env.flush_all()
        self.env.invalidate_all()
        charge = self.env["hosting.dashboard"].get_dashboard_data()
        # Le marshaller d'Odoo, pas un simulacre : c'est lui qui lève.
        OdooMarshaller(allow_none=False).dumps((charge,))

    # ── Le courriel « lent », et les trois façons de ne pas l'envoyer ────
    def test_un_vrai_lent_merite_son_courriel(self):
        """La moitié qui prouve que le contrôle discrimine."""
        service = self._service("Service vraiment lent",
                                server_url="https://lent.invalid/sante")
        self.assertTrue(service._est_lent("up", 9000, 5000))

    def test_un_rapide_ne_merite_rien(self):
        service = self._service("Service rapide",
                                server_url="https://vif.invalid/sante")
        self.assertFalse(service._est_lent("up", 120, 5000))

    def test_une_mesure_sans_milliseconde_ne_leve_pas(self):
        """🔴 `None > 5000` lève un TypeError, et la passe ENTIÈRE tomberait.

        Les sondes par dépôt rendent `None` comme temps de réponse : c'est le
        cas NORMAL depuis ce lot, pas un cas tordu.
        """
        service = self._service("Service sans latence mesurée",
                                server_url="https://muet.invalid/sante")
        self.assertFalse(service._est_lent("up", None, 5000))
        self.assertFalse(service._est_lent("up", 0, 5000))

    def test_une_sonde_par_depot_nest_jamais_lente(self):
        """🔴 Le canari rendait un ÂGE en millisecondes : un courriel par passe."""
        dock = self._dock(agent_report_at=fields.Datetime.now(),
                          agent_container_running=True,
                          agent_container_health="healthy")
        self.assertFalse(dock._est_lent("up", 300000, 5000))
        canari = self._service("Canari du lent", health_kind="container_agent")
        self.assertFalse(canari._est_lent("up", 300000, 5000))

    def test_un_silence_dalerte_tait_aussi_la_lenteur(self):
        """⚠️ Ce chemin ne lisait pas le silence : il partait quand même."""
        service = self._service(
            "Service lent sous silence",
            server_url="https://lent.invalid/sante",
            health_silence_until=fields.Datetime.now() + timedelta(days=3))
        self.assertFalse(service._est_lent("up", 9000, 5000))

    # ── Le bouton fait la même chose que le cron ─────────────────────────
    def test_bouton_eprouve_un_conteneur_sans_url(self):
        dock = self._dock(
            agent_report_at=fields.Datetime.now(),
            agent_container_running=True,
            agent_container_health="unhealthy",
        )
        avant = self.env["hosting.health.check"].search_count(
            [("service_id", "=", dock.id)])
        dock.action_run_health_check()
        apres = self.env["hosting.health.check"].search_count(
            [("service_id", "=", dock.id)])
        # 🔴 La garde « pas d'URL » doit rester APRÈS l'aiguillage : sinon le
        # bouton répond « aucune URL » sans rien éprouver, et n'écrit RIEN.
        # C'est donc le relevé écrit qui le prouve, pas un titre traduit.
        self.assertEqual(apres, avant + 1, "le bouton doit écrire un relevé")
        self.assertEqual(
            dock.last_health_status, "degraded",
            "le relevé du bouton doit porter le verdict de la sonde",
        )

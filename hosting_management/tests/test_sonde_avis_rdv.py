# -*- coding: utf-8 -*-
"""Le contrôle quotidien des avis de modification de rendez-vous.

Ce que ces essais tiennent en place, et pourquoi chacun a été écrit :

* un changement fait il y a cinq minutes n'est PAS une panne. Il attend
  légitimement que quelqu'un presse le bouton, et un contrôle qui crie dessus
  finit désarmé, ce qui emporte la vraie panne avec lui ;
* un changement d'hier que personne n'a annoncé en est une ;
* 🔴 la moitié indépendante compte autant que l'autre. Si le rapatriement
  s'arrête, Odoo n'apprend plus rien, plus aucun avis n'est dû, le registre est
  vert, et personne n'est prévenu de rien. Un essai qui ne vérifie que le
  registre rendrait exactement le même vert dans les deux cas ;
* un relevé qu'on n'a pas pu prendre n'est pas un relevé vide ;
* une tâche par écart, pas une par passage du cron.
"""

from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSondeAvisRdv(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.invite = cls.env["res.partner"].create({
            "name": "Invité Externe",
            "email": "externe@example.org",
        })
        cls.projet = cls.env["project.project"].create({
            "name": "Constats de veille (banc)",
        })
        cls.client = cls.env["res.partner"].create({"name": "Locataire de banc"})
        cls.logiciel = cls.env["hosting.software"].create({
            "name": "Agenda de banc",
            "code": "TAVIS",
            "software_type": "self_hosted",
        })
        # ⚠️ Une fiche d'agenda RÉELLE, même vide : sans elle, la boucle de la
        # moitié indépendante ne tourne sur rien et le simulacre n'est jamais
        # appelé. L'essai rendrait vert en n'ayant rien éprouvé, ce qui est
        # exactement le défaut que cette moitié existe pour attraper.
        cls.agenda = cls.env["nextcloud.calendar.sync.config"].create({
            "name": "Agenda de banc",
            "nextcloud_base_url": "https://nextcloud.example.invalid",
            "nextcloud_user": "banc",
            "caldav_path": "/remote.php/dav/calendars/banc/banc/",
        })
        cls.service = cls.env["hosting.service"].create({
            "name": "Avis de modification de rendez-vous",
            "partner_id": cls.client.id,
            "software_id": cls.logiciel.id,
            "health_kind": "calendar_notices",
            "watchdog_project_id": cls.projet.id,
        })

    def _rencontre(self, depuis_heures=48):
        """Une rencontre à venir, changée il y a `depuis_heures`."""
        start = fields.Datetime.now() + timedelta(days=7)
        event = self.env["calendar.event"].create({
            "name": "Statutaire de banc",
            "start": start,
            "stop": start + timedelta(minutes=30),
            "partner_ids": [(6, 0, [self.invite.id,
                                    self.env.user.partner_id.id])],
        })
        event.write({"stop": start + timedelta(hours=1)})
        # Le moment du changement se REMONTE dans le passé plutôt que d'attendre :
        # un essai qui dort deux jours n'est pas un essai.
        event.sudo().write({
            "bf_change_since": fields.Datetime.now() - timedelta(hours=depuis_heures),
        })
        return event

    def _sonder(self, ecarts=None, mesure=True, erreur=None):
        """Sonder avec une moitié distante simulée, et rien de réel au bout."""
        releve = {"mesure": mesure, "erreur": erreur,
                  "ecarts": ecarts or [], "vus": 12}
        Config = type(self.env["nextcloud.calendar.sync.config"])
        with patch.object(Config, "bf_ecarts_non_rapatries",
                          return_value=releve):
            return self.service._do_calendar_notices_check(force=True)

    # -- Le registre ---------------------------------------------------------

    def test_rien_a_signaler_rend_vert(self):
        statut, _ms, _code, erreur = self._sonder()
        self.assertEqual(statut, "up")
        self.assertFalse(erreur)

    def test_un_changement_tout_frais_n_est_pas_une_panne(self):
        self._rencontre(depuis_heures=0)
        statut, _ms, _code, _e = self._sonder()
        self.assertEqual(statut, "up", "le délai de grâce ne s'applique pas")

    def test_un_changement_d_hier_non_annonce_est_une_panne(self):
        rencontre = self._rencontre(depuis_heures=48)
        statut, _ms, _code, erreur = self._sonder()
        self.assertEqual(statut, "degraded")
        self.assertIn(rencontre.name, erreur)

    def test_l_avis_envoye_referme_le_constat(self):
        rencontre = self._rencontre(depuis_heures=48)
        rencontre._bf_send_change_notice()
        statut, _ms, _code, _e = self._sonder()
        self.assertEqual(statut, "up")

    # -- La moitié indépendante ---------------------------------------------

    def test_un_ecart_chez_nextcloud_est_plus_grave(self):
        """🔴 Le registre est vert et pourtant la chaîne est cassée.

        C'est le cas que la première moitié ne peut pas voir : rien n'est dû
        dans Odoo précisément parce qu'Odoo n'a rien appris.
        """
        statut, _ms, _code, erreur = self._sonder(
            ecarts=[{"href": "/dav/x.ics", "raison": "etag",
                     "modifie_le": "hier"}]
        )
        self.assertEqual(statut, "down")
        self.assertIn("sans qu'Odoo l'apprenne", erreur)

    def test_un_releve_impossible_n_est_pas_un_vert(self):
        """« Rien trouvé » et « rien regardé » ne se ressemblent pas."""
        statut, _ms, _code, erreur = self._sonder(
            mesure=False, erreur="ConnectionError : injoignable"
        )
        self.assertEqual(statut, "down")
        self.assertIn("impossible de relire", erreur)

    # -- La tâche ------------------------------------------------------------

    def test_une_tache_par_ecart_pas_une_par_passage(self):
        self._rencontre(depuis_heures=48)
        self._sonder()
        self._sonder()
        self._sonder()
        taches = self.env["project.task"].search([
            ("project_id", "=", self.projet.id),
        ])
        self.assertEqual(len(taches), 1, "le cron a ouvert une tâche par passage")
        self.assertIn("[veille:", taches.name)

    def test_sans_projet_designe_aucune_tache(self):
        """L'état par défaut n'écrit nulle part plutôt que n'importe où.

        🔴 Compté sur TOUTES les tâches, pas sur celles du projet de l'essai.
        Une tâche créée sans projet n'apparaît dans aucun projet : l'essai qui
        ne regardait que `self.projet` restait vert alors que la garde avait
        été retirée. Attrapé par mutation.
        """
        self.service.watchdog_project_id = False
        self._rencontre(depuis_heures=48)
        avant = self.env["project.task"].search_count([])
        statut, _ms, _code, _e = self._sonder()
        self.assertEqual(statut, "degraded", "le constat tient quand même")
        self.assertEqual(
            self.env["project.task"].search_count([]), avant,
            "une tâche a été ouverte alors qu'aucun projet n'est désigné",
        )

    def test_une_tache_refermee_peut_renaitre(self):
        """Sinon un écart réglé puis revenu ne serait plus jamais signalé."""
        self._rencontre(depuis_heures=48)
        self._sonder()
        tache = self.env["project.task"].search([
            ("project_id", "=", self.projet.id)], limit=1)
        tache.state = "1_done"
        self._sonder()
        self.assertEqual(self.env["project.task"].search_count([
            ("project_id", "=", self.projet.id)]), 2)

    # -- La cadence ----------------------------------------------------------

    def test_la_cadence_repete_la_derniere_mesure(self):
        """Le cron bat à la minute ; cette question se pose à la journée."""
        self._rencontre(depuis_heures=48)
        statut, _ms, _code, _e = self._sonder()
        self.assertEqual(statut, "degraded")
        self.env["hosting.health.check"].create({
            "service_id": self.service.id, "status": "degraded",
            "error_message": "mesure d'hier",
        })
        repete = self.service._do_calendar_notices_check()
        self.assertEqual(repete[0], "degraded")
        self.assertEqual(repete[3], "mesure d'hier")

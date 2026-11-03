# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Asking for a backup without Odoo running anything.

The host holds every container password, dumps every database and writes to S3.
Odoo therefore never runs a backup and never hands over a command: it writes a
request carrying a service, and an agent on the host comes for it and decides by
itself which script to run.

These tests pin the two halves of that promise. The refusals, because a button at
one click away must not be able to pile work onto the host: only the hosting
manager, only a service the in-house pipeline backs up, never twice at once, and
never twice within the cooldown. And the shape of what the agent reads, because the
day the answer carries a script name or a path, the doctrine is broken and a flaw in
Odoo becomes a command on the machine.
"""

import json

from odoo import fields
from psycopg2.errors import UniqueViolation

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestBackupRequest(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Client Demande"})
        cls.server = cls.env["hosting.server"].create({
            "name": "essai-demande",
            "code": "SRV-DEM",
            "hostname": "essai-demande.invalid",
        })
        cls.software = cls.env["hosting.software"].create({
            "name": "Produit Demande", "code": "dem",
        })
        cls.service = cls._make_service("Service a sauvegarder", "dem-app")
        cls.manager = cls.env["res.users"].create({
            "name": "Gestionnaire hebergement",
            "login": "gestion_demande_essai",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("hosting_management.group_hosting_manager").id,
            ])],
        })
        cls.reader = cls.env["res.users"].create({
            "name": "Lecteur hebergement",
            "login": "lecteur_demande_essai",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("hosting_management.group_hosting_user").id,
            ])],
        })

    @classmethod
    def _make_service(cls, name, container, **extra):
        vals = {
            "name": name,
            "partner_id": cls.partner.id,
            "server_id": cls.server.id,
            "software_id": cls.software.id,
            "docker_container": container,
            "state": "active",
            "backup_expectation": "pipeline",
        }
        vals.update(extra)
        return cls.env["hosting.service"].create(vals)

    def _demander(self, service=None, user=None):
        env = self.env(user=user) if user else self.env
        return env["hosting.backup.request"].create({
            "service_id": (service or self.service).id,
        })

    # ── Ce qui refuse une demande ─────────────────────────────────────────

    def test_only_the_hosting_manager_can_ask(self):
        """A hidden button is still callable over RPC.

        Creating a request makes real work happen on the host: a tenant goes into
        maintenance mode, a disk fills. A read-only role must not be able to.
        """
        with self.assertRaises(AccessError):
            self._demander(user=self.reader)
        # Et le gestionnaire, lui, passe.
        self.assertTrue(self._demander(user=self.manager))

    def test_a_service_outside_the_pipeline_is_refused(self):
        """Nothing on the host would answer, so the request would wait forever."""
        chez_fournisseur = self._make_service(
            "Service du fournisseur", "fourn-app",
            backup_expectation="provider",
            backup_expectation_reason="Backed up by the vendor.",
        )
        with self.assertRaises(ValidationError):
            self._demander(chez_fournisseur)

    def test_a_service_without_a_container_is_refused(self):
        """The agent would not know which instance to back up."""
        sans_conteneur = self._make_service("Service sans conteneur", False)
        with self.assertRaises(ValidationError):
            self._demander(sans_conteneur)

    def test_a_draft_service_is_refused(self):
        brouillon = self._make_service("Service brouillon", "brouillon-app",
                                       state="draft")
        with self.assertRaises(ValidationError):
            self._demander(brouillon)

    def test_a_service_without_a_server_is_refused(self):
        """🔴 Otherwise the request goes to the FIRST agent that polls.

        On a machine where the container does not exist, the script exits 0 with
        no snapshot and Odoo writes "failed" on a sound request the real host will
        never see. That is word for word the damage the host filter exists to
        prevent, so it is refused at the click, where the reason is readable.
        """
        orphelin = self._make_service("Service sans serveur", "orphelin-app")
        orphelin.write({"server_id": False})
        with self.assertRaises(ValidationError):
            self._demander(orphelin)

    def test_a_server_cannot_have_an_empty_hostname(self):
        """Le cas « serveur sans nom d'hote » est ferme EN AMONT.

        Une demande pour un tel serveur ne serait vue par aucun agent et mourrait
        en silence au bout d'une heure. Le garde de `_verifier_recevabilite` le
        refuse, mais il est inatteignable : le modele serveur a sa propre
        contrainte, et c'est elle qui tranche. Cet essai epingle cette contrainte,
        parce que c'est elle qui rend le garde superflu, et qu'une regression la
        rendrait soudain atteignable.
        """
        muet = self.env["hosting.server"].create({
            "name": "serveur muet", "code": "SRV-MUET", "hostname": "muet.invalid",
        })
        # ⚠️ L'`assertRaises` d'Odoo n'accepte pas un tuple : il fait
        # `issubclass(exception, AccessError)` sur l'argument, ce qui leve un
        # TypeError. Un seul type a la fois. Ici la contrainte du modele serveur
        # refuse l'espace par son expression de validation.
        with self.assertRaises(ValidationError):
            muet.sudo().write({"hostname": " "})

    def test_two_requests_at_once_on_the_same_service_are_refused(self):
        """Two passes on one instance get in each other's way.

        It is the same family as the verification database the host used to share
        between concurrent passes.
        """
        self._demander()
        with self.assertRaises(ValidationError):
            self._demander()

    def test_the_cooldown_refuses_a_second_request(self):
        """The button is one click away, and a backup is not free."""
        premiere = self._demander()
        premiere.write({"state": "done", "finished_date": fields.Datetime.now()})
        with self.assertRaises(ValidationError):
            self._demander()

    def test_the_cooldown_can_be_set_to_zero(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "hosting.backup_request_cooldown_minutes", "0"
        )
        premiere = self._demander()
        premiere.write({"state": "done", "finished_date": fields.Datetime.now()})
        self.assertTrue(self._demander())

    def test_a_cooldown_that_is_not_a_number_falls_back(self):
        """A broken parameter must not open the gate wide."""
        self.env["ir.config_parameter"].sudo().set_param(
            "hosting.backup_request_cooldown_minutes", "trente"
        )
        premiere = self._demander()
        premiere.write({"state": "done", "finished_date": fields.Datetime.now()})
        with self.assertRaises(ValidationError):
            self._demander()

    # ── Ce qu'une demande est, et n'est pas ───────────────────────────────

    def test_a_request_gets_a_reference(self):
        demande = self._demander()
        self.assertTrue(demande.name.startswith("BKR-"), demande.name)

    def test_a_request_is_not_rewritten(self):
        """A request is a dated fact: its service and its author do not move.

        🔴 The write goes through `with_user`, not through `self.env`: in a test
        `self.env` is the SUPERUSER, which every guard here lets through by design.
        A guard checked with `self.env` proves nothing, and this test failed exactly
        that way before it said what it meant.
        """
        demande = self._demander()
        autre = self._make_service("Autre service", "autre-app")
        comme_gestionnaire = demande.with_user(self.manager)
        with self.assertRaises(AccessError):
            comme_gestionnaire.write({"service_id": autre.id})
        with self.assertRaises(AccessError):
            comme_gestionnaire.write({"requested_by_id": self.manager.id})
        # Les champs `related` aussi : Odoo propage l'ecriture vers la SOURCE, donc
        # un write ici changerait le conteneur du service lui-meme.
        with self.assertRaises(AccessError):
            comme_gestionnaire.write({"docker_container": "autre-conteneur"})
        self.assertEqual(self.service.docker_container, "dem-app")

    def test_a_request_the_agent_never_closed_does_not_block_the_service(self):
        """An agent can die between taking a request and reporting back.

        Without a second deadline the request would stay "taken" for ever, and the
        "one live request per service" guard would then refuse any new one: the
        service would be blocked by a ghost.
        """
        demande = self._demander()
        demande.write({
            "state": "claimed",
            "claimed_date": fields.Datetime.subtract(
                fields.Datetime.now(), hours=5
            ),
        })
        self.env["hosting.backup.request"]._cron_expire_requests()
        self.assertEqual(demande.state, "failed")
        # ⚠️ Ne pas eprouver le TEXTE : ce message passe par les catalogues, et la
        # base d'essai est en français. Le contrat est qu'un message explique la
        # fermeture, pas qu'il contienne tel mot anglais. Le premier jet comparait
        # « never reported » a « n'a jamais rendu compte ».
        self.assertTrue(demande.message)
        self.assertTrue(demande.finished_date)
        # Et le service est de nouveau demandable.
        self.env["ir.config_parameter"].sudo().set_param(
            "hosting.backup_request_cooldown_minutes", "0"
        )
        self.assertTrue(self._demander())

    def test_cancelling_is_for_the_manager_and_only_while_waiting(self):
        demande = self._demander()
        with self.assertRaises(AccessError):
            demande.with_user(self.reader).action_cancel()
        demande.write({"state": "claimed"})
        with self.assertRaises(UserError):
            demande.action_cancel()
        demande.write({"state": "pending"})
        demande.action_cancel()
        self.assertEqual(demande.state, "cancelled")

    # ── Expiration ────────────────────────────────────────────────────────

    def test_a_request_nobody_came_for_expires(self):
        """An agent restarted the next day must not run yesterday's request.

        It could land in the middle of the nightly window, on an instance its
        author believed at rest.
        """
        demande = self._demander()
        # `.sudo()` est explicite : le champ est fige pour tout le monde, et seul le
        # systeme le remonte pour fabriquer une demande vieille.
        demande.sudo().write({
            "requested_date": fields.Datetime.subtract(
                fields.Datetime.now(), hours=4
            ),
        })
        self.env["hosting.backup.request"]._cron_expire_requests()
        self.assertEqual(demande.state, "expired")
        self.assertTrue(demande.message)

    def test_a_fresh_request_does_not_expire(self):
        demande = self._demander()
        self.env["hosting.backup.request"]._cron_expire_requests()
        self.assertEqual(demande.state, "pending")

    def test_a_claimed_request_does_not_expire(self):
        """Expiry only ever touches what nobody took: the host may be mid-dump."""
        demande = self._demander()
        demande.sudo().write({
            "state": "claimed",
            "claimed_date": fields.Datetime.now(),
            "requested_date": fields.Datetime.subtract(
                fields.Datetime.now(), hours=4
            ),
        })
        self.env["hosting.backup.request"]._cron_expire_requests()
        self.assertEqual(demande.state, "claimed")

    def test_the_database_itself_refuses_two_live_requests(self):
        """🔴 The guard is a `search` after the `create`: a TOCTOU.

        Two clicks in the same second, or two `call_kw` in flight, and neither
        transaction sees a live request. Only a partial unique index in the
        database can decide, so this test bypasses the Python guard entirely and
        checks that the index still refuses.
        """
        vivante = self._demander()
        with self.assertRaises(UniqueViolation):
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    """
                    INSERT INTO hosting_backup_request
                        (name, service_id, state, requested_by_id, requested_date)
                    VALUES ('BKR-ESSAI-DOUBLE', %s, 'pending', %s, now())
                    """,
                    (self.service.id, self.env.user.id),
                )
        self.assertEqual(vivante.state, "pending")

    def test_a_closed_request_does_not_block_the_index(self):
        """The index must only bind LIVE requests, or one backup per service ever."""
        self.env["ir.config_parameter"].sudo().set_param(
            "hosting.backup_request_cooldown_minutes", "0"
        )
        premiere = self._demander()
        premiere.sudo().write({"state": "done",
                               "finished_date": fields.Datetime.now()})
        self.assertTrue(self._demander())

    def test_a_claimed_request_without_a_date_is_still_closed(self):
        """⚠️ In SQL, `NULL < X` is not true.

        A request taken without a date escaped the stuck branch and stayed a
        permanent ghost, exactly the state that branch exists to close.
        """
        demande = self._demander()
        demande.sudo().write({
            "state": "claimed",
            "claimed_date": False,
            "requested_date": fields.Datetime.subtract(
                fields.Datetime.now(), hours=5
            ),
        })
        self.env["hosting.backup.request"]._cron_expire_requests()
        self.assertEqual(demande.state, "failed")

    # ── Le bouton de la fiche de service ──────────────────────────────────

    def test_the_service_button_creates_the_request(self):
        action = self.service.with_user(self.manager).action_request_backup_now()
        self.assertEqual(action["res_model"], "hosting.backup.request")
        demande = self.env["hosting.backup.request"].browse(action["res_id"])
        self.assertEqual(demande.service_id, self.service)
        self.assertEqual(demande.state, "pending")


@tagged("post_install", "-at_install")
class TestBackupRequestAPI(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Les deux routes de demandes veulent le jeton QUI LEUR EST PROPRE, pas
        # celui des rapports : c'est tout l'objet de la separation.
        cls.env["ir.config_parameter"].sudo().set_param(
            "hosting.backup_request_token", "test-token-demandes"
        )
        cls.env["ir.config_parameter"].sudo().set_param(
            "hosting.backup_api_token", "test-token-rapports"
        )
        cls.env["ir.config_parameter"].sudo().set_param(
            "hosting.backup_request_cooldown_minutes", "0"
        )
        cls.partner = cls.env["res.partner"].create({"name": "Client API Demande"})
        # ⚠️ Le nom de ce serveur doit correspondre a l'hote que `_prendre`
        # annonce : depuis le filtre par hote, une demande n'est servie qu'a la
        # machine qui porte le service. La comparaison se fait sur la premiere
        # etiquette, donc « essai-hote » apparie « essai-hote.exemple.test ».
        cls.server = cls.env["hosting.server"].create({
            "name": "essai-api-demande", "code": "SRV-APIDEM",
            "hostname": "essai-hote.exemple.test",
        })
        cls.software = cls.env["hosting.software"].create({
            "name": "Produit API Demande", "code": "apidem",
        })

    def _service(self, name, container, **extra):
        vals = {
            "name": name, "partner_id": self.partner.id,
            "server_id": self.server.id, "software_id": self.software.id,
            "docker_container": container, "state": "active",
            "backup_expectation": "pipeline",
        }
        vals.update(extra)
        return self.env["hosting.service"].create(vals)

    def _demande(self, service):
        return self.env["hosting.backup.request"].sudo().create({
            "service_id": service.id,
        })

    def _prendre(self, host="essai-hote", jeton="test-token-demandes",
                 charge_sup=None):
        charge = {"host": host}
        charge.update(charge_sup or {})
        return self.url_open(
            "/api/hosting/backup/requests/public",
            data=json.dumps(charge),
            headers={"Content-Type": "application/json",
                     "X-Backup-Request-Token": jeton},
        )

    def _rendre(self, charge, jeton="test-token-demandes"):
        return self.url_open(
            "/api/hosting/backup/requests/report/public",
            data=json.dumps(charge),
            headers={"Content-Type": "application/json",
                     "X-Backup-Request-Token": jeton},
        )

    def test_a_wrong_token_claims_nothing(self):
        service = self._service("Service jeton", "jeton-app")
        demande = self._demande(service)
        reponse = self._prendre(jeton="mauvais")
        self.assertEqual(reponse.status_code, 401)
        demande.invalidate_recordset()
        self.assertEqual(demande.state, "pending")

    def test_the_telemetry_token_does_not_open_the_request_routes(self):
        """🔴 Toute la raison d'etre du second jeton.

        Celui des rapports sert la telemetrie. S'il ouvrait aussi ces routes, un
        porteur pourrait prendre les demandes d'un gestionnaire et les fermer en
        « terminee » en citant une vraie execution, donc peindre un service en
        vert sans qu'une sauvegarde ait tourne.
        """
        service = self._service("Service deux jetons", "deuxjetons-app")
        demande = self._demande(service)
        reponse = self._prendre(jeton="test-token-rapports")
        self.assertEqual(reponse.status_code, 401)
        demande.invalidate_recordset()
        self.assertEqual(demande.state, "pending")

    def test_without_its_parameter_the_route_refuses_instead_of_falling_back(self):
        """Un repli sur l'autre jeton annulerait la separation : on refuse."""
        self.env["ir.config_parameter"].sudo().set_param(
            "hosting.backup_request_token", ""
        )
        try:
            service = self._service("Service sans parametre", "sansparam-app")
            self._demande(service)
            reponse = self._prendre(jeton="test-token-rapports")
            self.assertEqual(reponse.status_code, 500)
        finally:
            self.env["ir.config_parameter"].sudo().set_param(
                "hosting.backup_request_token", "test-token-demandes"
            )

    def test_claiming_returns_the_request_and_marks_it_taken(self):
        service = self._service("Service pris", "pris-app")
        demande = self._demande(service)
        reponse = self._prendre()
        self.assertEqual(reponse.status_code, 200)
        corps = reponse.json()
        self.assertTrue(corps["success"])
        refs = [r["reference"] for r in corps["requests"]]
        self.assertIn(demande.name, refs)
        demande.invalidate_recordset()
        self.assertEqual(demande.state, "claimed")
        self.assertEqual(demande.agent_host, "essai-hote")
        self.assertTrue(demande.claimed_date)

    def test_the_answer_never_carries_a_command(self):
        """🔴 The doctrine, pinned.

        Odoo hands over a service and a container. The script and the instance are
        resolved on the host, in a hard-coded table. If a key appears here that
        names a script, a path or an argument, a flaw in Odoo becomes a command on
        the machine that holds every container password.
        """
        service = self._service("Service doctrine", "doctrine-app")
        self._demande(service)
        corps = self._prendre().json()
        attendues = {
            "id", "reference", "service_code", "service_name", "container",
            "software",
        }
        for demande in corps["requests"]:
            self.assertEqual(set(demande), attendues, set(demande) - attendues)
            texte = json.dumps(demande).lower()
            for interdit in (
                "restic.sh", "/home/", "/usr/", "script", "command", "argv",
                "cmd", "shell", "bash", "docker",
            ):
                self.assertNotIn(interdit, texte, interdit)

    def test_a_request_is_handed_to_one_agent_only(self):
        """Two agents reading the same request would play it twice.

        ⚠️ Le MEME hote la retrouve, et c'est voulu : c'est la reprise d'une
        demande dont la reponse ne lui est jamais parvenue. La propriete qui
        compte est qu'un AUTRE agent ne l'obtienne pas.
        """
        service = self._service("Service une fois", "unefois-app")
        demande = self._demande(service)
        premier = self._prendre(host="essai-hote").json()
        self.assertEqual([r["reference"] for r in premier["requests"]],
                         [demande.name])
        autre = self._prendre(host="essai-hote-bis").json()
        self.assertEqual(autre["requests"], [])

    def test_a_service_that_no_longer_qualifies_is_cancelled_not_handed_over(self):
        """The service may have changed between the request and the agent's pass."""
        service = self._service("Service devenu exempt", "exempt-app")
        demande = self._demande(service)
        service.write({
            "backup_expectation": "stateless",
            "backup_expectation_reason": "Nothing worth keeping any more.",
        })
        corps = self._prendre().json()
        self.assertEqual(corps["requests"], [])
        demande.invalidate_recordset()
        self.assertEqual(demande.state, "cancelled")

    def test_the_claim_is_capped(self):
        """A queue that piled up during an outage must not empty at once."""
        self.env["ir.config_parameter"].sudo().set_param(
            "hosting.backup_request_cooldown_minutes", "0"
        )
        for i in range(8):
            self._demande(self._service(f"Service plafond {i}", f"plafond{i}-app"))
        # Demander plus que le plafond du serveur : c'est lui qu'on eprouve.
        corps = self._prendre(charge_sup={"max": 20}).json()
        self.assertEqual(len(corps["requests"]), 5)

    def test_reporting_writes_the_outcome(self):
        service = self._service("Service compte rendu", "rendu-app")
        demande = self._demande(service)
        self._prendre()
        reponse = self._rendre({
            "id": demande.id, "state": "done", "message": "2 snapshot(s).",
            "host": "essai-hote",
        })
        self.assertEqual(reponse.status_code, 200)
        demande.invalidate_recordset()
        self.assertEqual(demande.state, "done")
        # « snapshot » vient du compte rendu de l'agent, pas d'un catalogue : ce
        # texte-la n'est pas traduit, l'assertion est donc stable.
        self.assertIn("snapshot", demande.message)
        self.assertTrue(demande.finished_date)

    def test_an_unknown_state_is_refused(self):
        service = self._service("Service etat inconnu", "etatinconnu-app")
        demande = self._demande(service)
        self._prendre()
        reponse = self._rendre({"id": demande.id, "state": "whatever"})
        self.assertEqual(reponse.status_code, 400)
        demande.invalidate_recordset()
        self.assertEqual(demande.state, "claimed")

    def test_a_request_the_agent_did_not_take_cannot_be_closed(self):
        """Otherwise one call could close a request another agent is playing."""
        service = self._service("Service non pris", "nonpris-app")
        demande = self._demande(service)
        reponse = self._rendre({"id": demande.id, "state": "done"})
        self.assertEqual(reponse.status_code, 409)
        demande.invalidate_recordset()
        self.assertEqual(demande.state, "pending")

    def test_a_report_without_a_host_is_refused(self):
        """🔴 Omission is not permission.

        Without `host`, the caller walked straight through the guard and could
        close in "done" a request another host was still playing.
        """
        service = self._service("Service sans hote", "sanshote-app")
        demande = self._demande(service)
        self._prendre()
        reponse = self._rendre({"id": demande.id, "state": "done"})
        self.assertEqual(reponse.status_code, 400)
        demande.invalidate_recordset()
        self.assertEqual(demande.state, "claimed")

    def test_the_same_host_under_its_long_name_is_accepted(self):
        """The claim compares the first label, so the report must too.

        An agent claiming by its short name and reporting by its
        fully qualified name used to take a 409, and the cron then closed its
        request as failed two hours later.
        """
        service = self._service("Service nom long", "nomlong-app")
        demande = self._demande(service)
        self._prendre(host="essai-hote")
        reponse = self._rendre({
            "id": demande.id, "state": "done", "host": "essai-hote.exemple.test",
        })
        self.assertEqual(reponse.status_code, 200)
        demande.invalidate_recordset()
        self.assertEqual(demande.state, "done")

    def test_an_agent_gets_back_what_it_already_claimed(self):
        """If the answer never arrived, the request must come back to its agent.

        Otherwise it stays "claimed", nobody plays it, and the service is blocked
        by a ghost until the stuck deadline.
        """
        service = self._service("Service reprise", "reprise-app")
        demande = self._demande(service)
        self._prendre(host="essai-hote")
        demande.invalidate_recordset()
        self.assertEqual(demande.state, "claimed")
        corps = self._prendre(host="essai-hote").json()
        self.assertIn(demande.name, [r["reference"] for r in corps["requests"]])
        # Mais pas a un AUTRE hote : elle ne lui appartient pas.
        autre = self._prendre(host="essai-hote-bis").json()
        self.assertNotIn(demande.name, [r["reference"] for r in autre["requests"]])

    def test_an_on_demand_run_does_not_steal_the_daily_report(self):
        """🔴 The daily cron keeps only the most recent run per company and host.

        A click in the morning took the nightly run's slot: the team received a
        one-line report and the real one waited an hour. That morning email is
        exactly the artefact on which one reads whether the backups ran.
        """
        Run = self.env["hosting.backup.run"].sudo()
        nuit = Run.create({
            "run_date": fields.Datetime.subtract(fields.Datetime.now(), hours=3),
            "state": "success", "hostname": "essai-hote",
        })
        clic = Run.create({
            "run_date": fields.Datetime.now(), "state": "success",
            "hostname": "essai-hote", "on_demand": True,
        })
        self.assertFalse(nuit.on_demand)
        self.assertTrue(clic.on_demand)
        # Le cron ne retient pas l'execution demandee a la main.
        retenues = Run.search([
            ("report_sent", "=", False), ("on_demand", "=", False),
            ("hostname", "=", "essai-hote"),
        ])
        self.assertIn(nuit, retenues)
        self.assertNotIn(clic, retenues)

    def test_the_restic_report_route_carries_the_on_demand_flag(self):
        """🔴 Le drapeau doit etre pose par la route QUE L'AGENT APPELLE.

        Le premier jet le posait dans la route de rapport ZIP, et le parcours de
        bout en bout l'a dit : l'execution arrivait avec « a la demande = False »,
        donc elle aurait encore vole le creneau du rapport quotidien. Un essai sur
        le CHAMP seul ne l'aurait jamais vu.
        """
        charge = {
            "report_type": "restic",
            "timestamp": "2026-11-02 20:31:51",
            "hostname": "essai-hote-rapport",
            "on_demand": True,
            "summary": {"total": 1, "success": 1, "failed": 0, "skipped": 0},
            "results": [{
                "service": "On demand: essai",
                "status": "success", "duration": "5s", "exit_code": 0,
                "snapshots": [{
                    "service": "Essai", "container": "essai-rapport-app",
                    "destination": "Essai - Backups/Rapport",
                    "snapshot_id": "aaaa1111", "files_new": 1, "files_changed": 0,
                    "files_unmodified": 0, "data_added": 10,
                    "data_added_packed": 10, "total_files_processed": 1,
                    "total_bytes_processed": 10, "duration_sec": 1,
                }],
                "snapshot_count": 1, "error": "", "error_count": 0,
            }],
        }
        reponse = self.url_open(
            "/api/hosting/backup/report/public",
            data=json.dumps(charge),
            headers={"Content-Type": "application/json",
                     "X-Backup-Token": "test-token-rapports"},
        )
        self.assertEqual(reponse.status_code, 200)
        execution = self.env["hosting.backup.run"].sudo().browse(
            reponse.json()["backup_run_id"])
        self.assertTrue(execution.on_demand)
        # Et sans le drapeau, une execution ordinaire n'est pas marquee.
        charge["on_demand"] = False
        charge["timestamp"] = "2026-11-02 20:32:51"
        reponse = self.url_open(
            "/api/hosting/backup/report/public",
            data=json.dumps(charge),
            headers={"Content-Type": "application/json",
                     "X-Backup-Token": "test-token-rapports"},
        )
        ordinaire = self.env["hosting.backup.run"].sudo().browse(
            reponse.json()["backup_run_id"])
        self.assertFalse(ordinaire.on_demand)

    def test_a_non_ascii_token_is_a_401_not_a_500(self):
        """⚠️ `hmac.compare_digest` raises TypeError on a non-ASCII string.

        The call sits outside the routes' try block, so a header like
        `X-Backup-Token: é` answered with a 500 and a traceback where the right
        answer is a plain 401. Pre-existing, but both new routes inherit it.
        """
        service = self._service("Service jeton accent", "accent-app")
        demande = self._demande(service)
        reponse = self._prendre(jeton="jet\u00e9n-invalide")
        self.assertEqual(reponse.status_code, 401)
        demande.invalidate_recordset()
        self.assertEqual(demande.state, "pending")

    def test_an_unknown_request_is_a_404(self):
        reponse = self._rendre({"id": 999999999, "state": "done"})
        self.assertEqual(reponse.status_code, 404)

    def test_a_request_for_another_host_is_not_claimed(self):
        """The fleet has two machines, and a script cannot back up what is not there.

        Without this filter the first agent to poll took requests for containers
        absent from its machine: the script then exits 0 with no snapshot, Odoo
        writes "failed" on a perfectly sound request, and the real host never sees
        it.
        """
        ailleurs = self.env["hosting.server"].create({
            "name": "autre-machine", "code": "SRV-AILLEURS",
            "hostname": "autre-machine.exemple.test",
        })
        service = self._service("Service ailleurs", "ailleurs-app")
        service.write({"server_id": ailleurs.id})
        demande = self._demande(service)
        corps = self._prendre(host="essai-hote").json()
        self.assertNotIn(demande.name, [r["reference"] for r in corps["requests"]])
        demande.invalidate_recordset()
        self.assertEqual(demande.state, "pending")
        # Et la machine qui porte le service, elle, la prend. Le nom se compare
        # sur sa premiere etiquette : la base dit « autre-machine.exemple.test ».
        corps = self._prendre(host="autre-machine.local").json()
        self.assertIn(demande.name, [r["reference"] for r in corps["requests"]])

    def test_another_host_cannot_close_a_claimed_request(self):
        """🔴 Otherwise one could write "backup done" on a backup that never ran.

        The real agent's report would then land on a 409 and be lost.
        """
        service = self._service("Service vol", "vol-app")
        demande = self._demande(service)
        self._prendre()
        reponse = self._rendre({
            "id": demande.id, "state": "done",
            "message": "2 snapshot(s).", "host": "hote-qui-na-rien-pris",
        })
        self.assertEqual(reponse.status_code, 409)
        demande.invalidate_recordset()
        self.assertEqual(demande.state, "claimed")
        # Le vrai agent, lui, passe.
        self.assertEqual(self._rendre({
            "id": demande.id, "state": "done", "message": "ok", "host": "essai-hote",
        }).status_code, 200)

    def test_running_refreshes_the_clock(self):
        """A request waiting its turn in the agent's local queue must not age.

        Otherwise the cron closes it as stuck before it even starts.
        """
        service = self._service("Service chrono", "chrono-app")
        demande = self._demande(service)
        self._prendre()
        demande.sudo().write({
            "claimed_date": fields.Datetime.subtract(fields.Datetime.now(), hours=5),
        })
        self._rendre({"id": demande.id, "state": "running", "host": "essai-hote"})
        demande.invalidate_recordset()
        ecart = fields.Datetime.now() - demande.claimed_date
        self.assertLess(ecart.total_seconds(), 120, "le chrono n'a pas ete repose")

    def test_running_twice_does_not_pile_up_the_chatter(self):
        service = self._service("Service boucle", "boucle-app")
        demande = self._demande(service)
        self._prendre()
        self._rendre({"id": demande.id, "state": "running", "host": "essai-hote"})
        # ⚠️ Invalider AVANT de compter : la route a ecrit dans une autre
        # transaction, et le cache de l'essai ne porte pas encore son
        # message. Le premier jet comparait un compte perime a un frais.
        demande.invalidate_recordset()
        avant = len(demande.message_ids)
        for _ in range(3):
            self.assertEqual(self._rendre({
                "id": demande.id, "state": "running", "host": "essai-hote",
            }).status_code, 200)
        demande.invalidate_recordset()
        self.assertEqual(len(demande.message_ids), avant)

    def test_a_message_that_is_not_a_string_does_not_blow_up(self):
        """The payload is authenticated, not trusted: a dict used to raise a 500."""
        service = self._service("Service type", "type-app")
        demande = self._demande(service)
        self._prendre()
        reponse = self._rendre({
            "id": demande.id, "state": "done", "message": {"a": 1}, "host": "essai-hote",
        })
        self.assertEqual(reponse.status_code, 200)
        demande.invalidate_recordset()
        self.assertEqual(demande.state, "done")

    def test_an_agent_announcing_zero_gets_one_not_five(self):
        """A fallback must go towards the careful side.

        `max: 0` read as falsy and served the server's five.
        """
        for i in range(3):
            self._demande(self._service(f"Service zero {i}", f"zero{i}-app"))
        corps = self._prendre(host="essai-hote", charge_sup={"max": 0}).json()
        self.assertEqual(len(corps["requests"]), 1)

    def test_an_agent_asking_two_gets_two(self):
        for i in range(4):
            self._demande(self._service(f"Service deux {i}", f"deux{i}-app"))
        corps = self._prendre(host="essai-hote", charge_sup={"max": 2}).json()
        self.assertEqual(len(corps["requests"]), 2)

    def test_the_run_is_attached_so_the_service_turns_green(self):
        """A backup asked for must show up in Odoo, not six hours later.

        Only the master script talks to Odoo; an agent runs a child. Without the
        agent posting its own report and the request carrying the run, the manager
        clicks, reads "done", and the red banner on the form is still red.
        """
        service = self._service("Service rattache", "rattache-app")
        demande = self._demande(service)
        self._prendre()
        execution = self.env["hosting.backup.run"].sudo().create({
            "run_date": fields.Datetime.now(), "state": "success",
        })
        self._rendre({
            "id": demande.id, "state": "done", "message": "1 snapshot.",
            "run_id": execution.id, "host": "essai-hote",
        })
        demande.invalidate_recordset()
        self.assertEqual(demande.run_id, execution)

    def test_an_unknown_run_id_is_ignored_not_written(self):
        service = self._service("Service run faux", "runfaux-app")
        demande = self._demande(service)
        self._prendre()
        self._rendre({
            "id": demande.id, "state": "done", "run_id": 999999999,
            "host": "essai-hote",
        })
        demande.invalidate_recordset()
        self.assertFalse(demande.run_id)

    def test_the_agent_message_is_escaped_in_the_chatter(self):
        """🔴 A chatter body is HTML, and this text comes in over a token route.

        Inserted as is, a `<` breaks the display and a tag runs in the reader's
        browser. Same family as the escaped chatter HTML, taken from
        the other end.
        """
        service = self._service("Service echappement", "echap-app")
        demande = self._demande(service)
        self._prendre()
        self._rendre({
            "id": demande.id, "state": "failed",
            "message": "<img src=x onerror=alert(1)> & 2 > 1",
            "host": "essai-hote",
        })
        demande.invalidate_recordset()
        corps = "".join(demande.message_ids.mapped("body"))
        self.assertNotIn("<img", corps)
        self.assertIn("&lt;img", corps)
        # Le texte reste lisible : l'esperluette et le chevron sont echappes, pas
        # retires.
        self.assertIn("&amp;", corps)

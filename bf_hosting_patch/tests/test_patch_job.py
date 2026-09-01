# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Les ordres de mise à jour : la file, les gardes, et l'issue.

Ce qu'on veut prouver n'est pas que l'ordre part quand tout va bien — c'est
qu'il NE part PAS dans les cas où il ne doit pas, et que chaque garde
discrimine. Un test qui ne vérifie que le chemin heureux passerait encore si on
supprimait toutes les gardes.
"""

import json
import uuid

from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

BASE = "/symbifox/patch/v1"

# ⚠️ Pas de `env.cr.commit()` dans un `HttpCase` : Odoo 18 le refuse
# explicitement (« Cannot commit or rollback a cursor from inside a test »), et
# c'est inutile — la requête HTTP du test partage la transaction, elle voit donc
# les enregistrements créés dans `setUp`.


class JobCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Client d'ordres", "is_company": True,
        })
        cls.endpoint = cls.env["hosting.endpoint"].create({
            "name": f"job-{uuid.uuid4().hex[:8]}", "partner_id": cls.partner.id,
        })
        cls.system = cls.env["bf.patch.system"].create({
            "name": "job-linux",
            "endpoint_id": cls.endpoint.id,
            "machine_id": uuid.uuid4().hex,
            "patch_managed": True,
            "apply_allowed": True,
        })

    def _job(self, **values):
        return self.env["bf.patch.job"].create({
            "system_id": self.system.id, "scope": "security", **values,
        })


@tagged("post_install", "-at_install")
class TestJobQueue(JobCase):

    def test_claim_gives_the_oldest_first(self):
        """Une file est une file : le premier arrivé part le premier."""
        first = self._job()
        second = self._job(scope="all")
        claimed, refusal = self.env["bf.patch.job"]._claim_for(self.system)
        self.assertFalse(refusal)
        self.assertEqual(claimed, first)
        self.assertEqual(claimed.state, "claimed")
        self.assertEqual(second.state, "queued")

    def test_claim_marks_and_does_not_hand_it_twice(self):
        """Un ordre ramassé ne doit pas repartir à l'interrogation suivante."""
        job = self._job()
        self.env["bf.patch.job"]._claim_for(self.system)
        again, _refusal = self.env["bf.patch.job"]._claim_for(self.system)
        self.assertFalse(again, "l'ordre est reparti une deuxième fois")
        self.assertEqual(job.state, "claimed")

    def test_no_consent_no_order(self):
        """🔴 La garde qui compte : sans consentement local, rien ne sort.

        Et on vérifie qu'elle DISCRIMINE — le même ordre part dès que le
        consentement revient. Sans ce second volet, une garde qui refuserait
        toujours passerait le test.
        """
        self._job()
        self.system.apply_allowed = False
        job, refusal = self.env["bf.patch.job"]._claim_for(self.system)
        self.assertFalse(job)
        self.assertIn("consentement", refusal)

        self.system.apply_allowed = True
        job, refusal = self.env["bf.patch.job"]._claim_for(self.system)
        self.assertTrue(job, "la garde refuse même avec le consentement")
        self.assertFalse(refusal)

    def test_unmanaged_system_gets_nothing(self):
        self._job()
        self.system.patch_managed = False
        job, refusal = self.env["bf.patch.job"]._claim_for(self.system)
        self.assertFalse(job)
        self.assertIn("non suivi", refusal)

    def test_window_start_holds_the_order_back(self):
        """Un ordre daté du futur reste en file jusqu'à son heure."""
        from odoo import fields
        future = fields.Datetime.add(fields.Datetime.now(), hours=3)
        self._job(window_start=future)
        job, _refusal = self.env["bf.patch.job"]._claim_for(self.system)
        self.assertFalse(job, "un ordre à fenêtre future est parti trop tôt")

        past = fields.Datetime.subtract(fields.Datetime.now(), hours=1)
        self.env["bf.patch.job"].search([
            ("system_id", "=", self.system.id)]).window_start = past
        job, _refusal = self.env["bf.patch.job"]._claim_for(self.system)
        self.assertTrue(job, "l'ordre n'est pas parti passé sa fenêtre")

    def test_named_scope_needs_names(self):
        with self.assertRaises(ValidationError):
            self._job(scope="named", package_names="   ")

    def test_package_list_splits_both_separators(self):
        job = self._job(scope="named", package_names="bash, curl  openssl")
        self.assertEqual(job.package_list(), ["bash", "curl", "openssl"])

    def test_final_state_is_not_reopened(self):
        """🔴 Un agent qui rejoue son rapport ne réécrit pas l'issue."""
        job = self._job()
        job._record_result("failed", exit_code=1, output="cassé")
        self.assertFalse(job._record_result("done", exit_code=0, output="ok"))
        self.assertEqual(job.state, "failed")
        self.assertEqual(job.exit_code, 1)

    def test_expiry_cron_targets_claimed_too(self):
        """Un agent qui meurt après avoir pris l'ordre ne le fige pas à vie."""
        from odoo import fields
        old = fields.Datetime.subtract(fields.Datetime.now(), days=9)
        queued = self._job()
        claimed = self._job(scope="all")
        claimed.state = "claimed"
        recent = self._job(scope="all")
        for job in (queued, claimed):
            self.env.cr.execute(
                "UPDATE bf_patch_job SET create_date = %s WHERE id = %s",
                (old, job.id),
            )
        self.env["bf.patch.job"].invalidate_model(["create_date"])

        self.env["bf.patch.job"]._cron_expire_jobs()
        self.assertEqual(queued.state, "expired")
        self.assertEqual(claimed.state, "expired")
        self.assertEqual(recent.state, "queued", "un ordre récent a été périmé")


@tagged("post_install", "-at_install")
class TestJobApi(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Client d'ordres API", "is_company": True,
        })
        cls.endpoint = cls.env["hosting.endpoint"].create({
            "name": f"jobapi-{uuid.uuid4().hex[:8]}",
            "partner_id": cls.partner.id,
        })
        cls.endpoint.action_generate_enrol_code()
        system_model = cls.env["bf.patch.system"]
        cls.token = system_model._new_token()
        cls.system = system_model.create({
            "name": "jobapi-linux",
            "endpoint_id": cls.endpoint.id,
            "machine_id": uuid.uuid4().hex,
            "patch_managed": True,
            "apply_allowed": True,
            "agent_token_hash": system_model._hash_token(cls.token),
        })

    def _post(self, path, body, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return self.url_open(BASE + path, data=json.dumps(body).encode(),
                             headers=headers)

    def test_poll_without_token_is_refused(self):
        answer = self._post("/poll", {})
        self.assertEqual(answer.status_code, 401)

    def test_poll_returns_the_order_then_nothing(self):
        job = self.env["bf.patch.job"].create({
            "system_id": self.system.id, "scope": "all",
        })
        body = self._post("/poll", {}, token=self.token).json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["job"]["id"], job.id)
        self.assertEqual(body["job"]["scope"], "all")
        # Le machine-id voyage, pour que l'agent vérifie qu'il est le bon
        self.assertEqual(body["job"]["machine_id"], self.system.machine_id)

    def test_result_of_another_system_is_refused(self):
        """🔴 La fuite inter-machine : un jeton valide ne clôt QUE ses ordres."""
        other_endpoint = self.env["hosting.endpoint"].create({
            "name": f"autre-{uuid.uuid4().hex[:8]}",
            "partner_id": self.partner.id,
        })
        other = self.env["bf.patch.system"].create({
            "name": "autre-linux", "endpoint_id": other_endpoint.id,
            "machine_id": uuid.uuid4().hex, "patch_managed": True,
        })
        foreign = self.env["bf.patch.job"].create({
            "system_id": other.id, "scope": "all",
        })
        answer = self._post("/result",
                            {"job_id": foreign.id, "state": "done"},
                            token=self.token)
        self.assertEqual(answer.status_code, 404)
        foreign.invalidate_recordset()
        self.assertEqual(foreign.state, "queued", "l'ordre voisin a été touché")

    def test_result_refuses_an_invented_state(self):
        job = self.env["bf.patch.job"].create({
            "system_id": self.system.id, "scope": "all",
        })
        answer = self._post("/result",
                            {"job_id": job.id, "state": "triomphal"},
                            token=self.token)
        self.assertEqual(answer.status_code, 400)

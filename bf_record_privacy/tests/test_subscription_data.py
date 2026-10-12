"""The core route that serves the subtypes of a follower line, called over HTTP."""
import json

from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSubscriptionData(HttpCase):

    def test_the_route_names_nothing_behind_a_hidden_line(self):
        alice = new_test_user(self.env, login="rps_alice", groups="base.group_user")
        bob = new_test_user(self.env, login="rps_bob", groups="base.group_user")
        self.env["ir.rule"].create({
            "name": "RPS test: secret contacts for their creator",
            "model_id": self.env.ref("base.model_res_partner").id,
            "domain_force": "['|', ('ref', '!=', 'RPS-SECRET'), ('create_uid', '=', user.id)]",
            "groups": [(4, self.env.ref("base.group_user").id)],
        })
        secret = self.env["res.partner"].with_user(alice).sudo().create({"name": "RPS secret", "ref": "RPS-SECRET"})
        secret.message_subscribe(partner_ids=alice.partner_id.ids)
        line = self.env["mail.followers"].search([("res_model", "=", "res.partner"), ("res_id", "=", secret.id)])
        self.authenticate("rps_bob", "rps_bob")
        response = self.url_open("/mail/read_subscription_data", data=json.dumps({
            "jsonrpc": "2.0", "method": "call", "params": {"follower_id": line.id}}),
            headers={"Content-Type": "application/json"})
        error = response.json().get("error", {}).get("data", {}).get("message", "")
        self.assertTrue(error, "the route refuses")
        self.assertNotIn("res.partner", error)
        self.assertNotIn(str(secret.id), error)
        self.assertIn(self.env(context={"lang": bob.lang})._(
            "The followers of a record can only be seen by those who can read the record."), error)
        self.authenticate("rps_alice", "rps_alice")
        response = self.url_open("/mail/read_subscription_data", data=json.dumps({
            "jsonrpc": "2.0", "method": "call", "params": {"follower_id": line.id}}),
            headers={"Content-Type": "application/json"})
        self.assertIn("result", response.json(), "the person who follows the record still gets the subtypes")

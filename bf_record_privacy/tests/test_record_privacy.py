"""The two leaks of the Odoo 18 core, each gate proven on its own, by its own message.

A rule hides "secret" contacts from everyone but their creator: Alice creates one
and follows it, Bob and an administrator cannot read it. Invented data.
"""
from unittest.mock import MagicMock, patch

from odoo.addons.base.models import res_users
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

SECRET = "RP-SECRET-NAME-7Q2"
RULE = "RP test: secret contacts for their creator"


@tagged("post_install", "-at_install")
class TestRecordPrivacy(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(cls.env, login="rp_alice", groups="base.group_user")
        cls.bob = new_test_user(cls.env, login="rp_bob", groups="base.group_user,base.group_no_one")
        cls.admin = new_test_user(
            cls.env, login="rp_admin", groups="base.group_user,base.group_no_one,base.group_system")
        cls.env["ir.rule"].create({
            "name": RULE,
            "model_id": cls.env.ref("base.model_res_partner").id,
            "domain_force": "['|', ('ref', '!=', 'RP-SECRET'), ('create_uid', '=', user.id)]",
            "groups": [(4, cls.env.ref("base.group_user").id)],
        })
        partners = cls.env["res.partner"].with_user(cls.alice).sudo()
        cls.secret = partners.create({"name": SECRET, "ref": "RP-SECRET"})
        cls.public = partners.create({"name": "RP public contact"})
        (cls.secret | cls.public).message_subscribe(partner_ids=cls.alice.partner_id.ids)
        cls.Followers = cls.env["mail.followers"]
        cls.secret_line = cls.Followers.sudo().search(
            [("res_model", "=", "res.partner"), ("res_id", "=", cls.secret.id)])
        cls.public_line = cls.Followers.sudo().search(
            [("res_model", "=", "res.partner"), ("res_id", "=", cls.public.id)])

    def _debug(self):
        session = MagicMock()
        session.session.debug = "1"
        return patch.object(res_users, "request", session)

    def _in_language_of(self, user, source):
        # A database in French creates its users in French.
        return self.env(context={"lang": user.lang})._(source)

    def _refusal(self, user):
        with self._debug(), self.assertRaises(AccessError) as refusal:
            self.secret.with_user(user).read(["name"])
        return str(refusal.exception)

    # Gate 1: the access error in debug mode

    def test_in_debug_the_access_error_does_not_name_the_record(self):
        message = self._refusal(self.bob)
        self.assertIn("res.partner: %d" % self.secret.id, message, "debug lists the refused records")
        self.assertIn(RULE, message, "debug names the rules at fault")
        self.assertNotIn(SECRET, message)
        self.assertIn(self._in_language_of(self.bob, "name hidden"), message)

    def test_administrators_keep_the_name(self):
        self.assertIn(SECRET, self._refusal(self.admin))

    def test_the_neutral_name_does_not_stay_in_the_cache(self):
        # The core drops the cache of the records it named; code that catches the
        # refusal and goes on in sudo must read the real name.
        self._refusal(self.bob)
        self.assertEqual(self.secret.with_user(self.bob).sudo().display_name, SECRET)
        self.assertEqual(self.secret.with_user(self.alice).display_name, SECRET)

    # Gate 2: follower lines, by search

    def test_followers_of_a_hidden_record_are_not_found(self):
        self.assertTrue(self.secret_line and self.public_line, "Alice follows both contacts")
        domain = [("res_model", "=", "res.partner"), ("res_id", "in", (self.secret | self.public).ids)]
        self.assertEqual(self.Followers.with_user(self.bob).search(domain), self.public_line)
        self.assertEqual(self.Followers.with_user(self.bob).search_count(domain), 1)
        self.assertEqual(self.Followers.with_user(self.admin).search(domain), self.public_line)
        self.assertEqual(self.Followers.with_user(self.alice).search(domain), self.secret_line | self.public_line)
        groups = self.Followers.with_user(self.bob)._read_group(domain, ["res_id"], ["__count"])
        self.assertEqual(groups, [(self.public.id, 1)])

    def test_followers_of_a_hidden_record_are_not_read_by_id(self):
        # read() of a stored field goes through _search, not through _check_access.
        with self.assertRaises(AccessError):
            self.secret_line.with_user(self.bob).read(["partner_id"])
        self.assertTrue(self.public_line.with_user(self.bob).read(["partner_id"]))

    def test_lines_of_a_model_without_read_right_are_not_found(self):
        blacklisted = self.env["mail.blacklist"].create({"email": "rp-hidden@example.com"})
        blacklisted.message_subscribe(partner_ids=self.alice.partner_id.ids)
        domain = [("res_model", "=", "mail.blacklist"), ("res_id", "=", blacklisted.id)]
        self.assertTrue(self.Followers.sudo().search(domain))
        self.assertFalse(self.Followers.with_user(self.bob).search(domain))

    # Gate 2: follower lines, by check_access / has_access

    def test_followers_of_a_hidden_record_fail_the_access_check(self):
        with self.assertRaises(AccessError) as refusal:
            self.secret_line.with_user(self.bob).check_access("read")
        self.assertIn(self._in_language_of(self.bob, (
            "The followers of a record can only be seen by those who can read the record.")), str(refusal.exception))
        self.assertFalse(self.secret_line.with_user(self.bob).has_access("read"))
        self.assertTrue(self.secret_line.with_user(self.alice).has_access("read"))
        self.assertTrue(self.public_line.with_user(self.bob).has_access("read"))

    def test_both_gates_agree_on_an_orphan_line_of_a_model_without_rule(self):
        # The record is gone (deleted in SQL), its follower line stays: a list of
        # followers must not crash on it, for anyone.
        kind = self.env["mail.activity.type"].create({"name": "RP orphan type"})
        self.Followers.create({"res_model": kind._name, "res_id": kind.id, "partner_id": self.alice.partner_id.id})
        self.assertFalse(self.env["ir.rule"].with_user(self.bob)._compute_domain("mail.activity.type", "read"),
                         "a model without rule")
        self.env.cr.execute("DELETE FROM mail_activity_type WHERE id = %s", [kind.id])
        self.env.invalidate_all()
        domain = [("res_model", "=", "mail.activity.type"), ("res_id", "=", kind.id)]
        for user in (self.bob, self.admin):
            Followers = self.Followers.with_user(user)
            result = Followers.web_search_read(domain, {"partner_id": {"fields": {"display_name": {}}}})
            self.assertEqual(result["length"], 1)
            self.assertTrue(Followers.search(domain).has_access("read"))

    def test_without_the_read_right_has_access_answers_no(self):
        portal = new_test_user(self.env, login="rp_portal", groups="base.group_portal")
        self.assertFalse(self.public_line.with_user(portal).has_access("read"))

    def test_a_new_line_is_not_checked(self):
        line = self.Followers.with_user(self.bob).new(
            {"res_model": "res.partner", "res_id": self.secret.id, "partner_id": self.alice.partner_id.id})
        line.check_access("read")

    # What keeps working

    def test_followers_of_an_archived_readable_record_stay_visible(self):
        archived = self.env["res.partner"].create({"name": "RP archived contact", "active": False})
        archived.message_subscribe(partner_ids=self.alice.partner_id.ids)
        domain = [("res_model", "=", "res.partner"), ("res_id", "=", archived.id)]
        self.assertEqual(len(self.Followers.with_user(self.bob).search(domain)), 1)

    def test_the_chatter_still_lists_the_followers_of_a_readable_record(self):
        self.assertEqual(self.public.with_user(self.bob).message_follower_ids, self.public_line)
        self.assertEqual(self.secret.with_user(self.alice).message_follower_ids, self.secret_line)

    def test_a_search_that_reads_followers_does_not_loop(self):
        # A followed model whose own search reads follower lines without sudo. Rules
        # are evaluated in sudo by Odoo; such an override is the only way back in.
        Partner = type(self.env["res.partner"])
        original = Partner._search

        def _search(model, domain, *args, **kwargs):
            model.env["mail.followers"].search_count([("res_model", "=", model._name)])
            return original(model, domain, *args, **kwargs)

        domain = [("res_model", "=", "res.partner"), ("res_id", "in", (self.secret | self.public).ids)]
        with patch.object(Partner, "_search", _search):
            self.assertEqual(self.Followers.with_user(self.bob).search(domain), self.public_line)

    def test_a_rule_that_goes_through_followers_still_applies(self):
        # Global: the rules of one group add up with OR, and the first one would let it through.
        self.env["ir.rule"].create({
            "name": "RP test: followed contacts for their followers",
            "model_id": self.env.ref("base.model_res_partner").id,
            "domain_force": "['|', ('ref', '!=', 'RP-FOLLOWED'), "
                            "('message_follower_ids.partner_id', 'in', [user.partner_id.id])]",
        })
        followed = self.env["res.partner"].create({"name": "RP followed contact", "ref": "RP-FOLLOWED"})
        followed.message_subscribe(partner_ids=self.alice.partner_id.ids)
        domain = [("res_model", "=", "res.partner"), ("res_id", "=", followed.id)]
        self.assertTrue(self.Followers.with_user(self.alice).search(domain))
        self.assertFalse(self.Followers.with_user(self.bob).search(domain))

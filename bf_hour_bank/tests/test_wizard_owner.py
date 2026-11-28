"""This module's wizards are owner-scoped.

A TransientModel is a real table, and Odoo 18 adds no implicit "creator only"
restriction. With an ACL open to base.group_user and no record rule, any
internal user could read a colleague's wizard with a plain web_search_read.

What these tests prove, for EACH wizard of the module:

* every wizard the module declares and opens through an ACL, whatever the
  group (base.group_user, a functional group, or none), is in WIZARDS: a new
  one added without a rule makes the first test fail, not a review;
* each one carries one global rule, `[('create_uid', '=', user.id)]`, on the
  four operations;
* a colleague's search is blind to the author's wizard, and reading, writing
  or deleting it raises; so does an administrator's (the rule is global);
* the author keeps reading and writing their own wizard.

What they do not prove: the behaviour of a wizard listed in UNBUILT (its rule
is still checked; the reason is written next to it). And the counter-test (rule removed, the test fails) is
run by hand on a test database, with the rules deleted from the database and no
upgrade, since an upgrade would recreate them.
"""

import base64
import datetime

from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase

MODULE = "bf_hour_bank"

WIZARDS = (
    "hour.bank.invoice.wizard",
    "hour.bank.invoice.wizard.line",
    "hour.bank.send.wizard",
)

# Groups of the author AND the colleague: the same rights, so that only the
# record rule can tell them apart (a refusal from the ACL would prove nothing).
GROUPS = "base.group_user,account.group_account_invoice,project.group_project_manager"

# Explicit create values, for a record the generic filler cannot build alone:
# a dict, or a callable taking the test case and returning one.
VALUES = {}

# Context a wizard needs to open (its active record), per model: a dict, or a
# callable taking the test case and returning one.
CONTEXTS = {}

# Wizards whose behaviour cannot be exercised here, with the reason. Their rule
# is still checked by the first test; only the two behaviour tests skip them.
UNBUILT = {}


@tagged("post_install", "-at_install")
class TestWizardOwnerRule(TransactionCase):
    """A colleague's wizard is closed to them; the author keeps their own."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.author = new_test_user(
            cls.env, login="essai.author@bench.invalid",
            groups=GROUPS, email="essai.author@bench.invalid")
        cls.colleague = new_test_user(
            cls.env, login="essai.colleague@bench.invalid",
            groups=GROUPS, email="essai.colleague@bench.invalid")
        cls.admin = new_test_user(
            cls.env, login="essai.admin@bench.invalid",
            groups=GROUPS + ",base.group_system",
            email="essai.admin@bench.invalid")
        # Some targets (an expense, a timesheet) need the user's employee.
        if "hr.employee" in cls.env:
            for user in (cls.author, cls.colleague, cls.admin):
                cls.env["hr.employee"].sudo().create({
                    "name": user.name, "user_id": user.id,
                    "company_id": user.company_id.id})

    # ------------------------------------------------------------------
    def _value_for(self, field):
        """A value for a required field that has no default."""
        kind = field.type
        if kind == "many2one":
            return self._target(field.comodel_name).id
        if kind == "many2many":
            return [(6, 0, self._target(field.comodel_name).ids)]
        if kind == "binary":
            return base64.b64encode(b"essai")
        if kind == "selection":
            return field._description_selection(self.env)[0][0]
        if kind in ("char", "text", "html"):
            return "essai"
        if kind in ("integer", "float", "monetary"):
            return 1
        if kind == "boolean":
            return True
        if kind == "date":
            return datetime.date.today()
        if kind == "datetime":
            return datetime.datetime.now()
        if kind == "reference":
            return "res.partner,%s" % self.env.user.partner_id.id
        raise AssertionError("cannot fill %s (%s): add it to VALUES" % (field, kind))

    def _target(self, model):
        """A record the author can READ: one they find, else one they create.

        Readable by the author, because several wizards refuse a target their
        user cannot read; created as the author when the database has none.
        """
        Target = self.env[model]
        if Target._transient:
            # Flushed now: a parent wizard whose stored compute rebuilds its
            # lines (Command.clear) would otherwise run it at the next flush
            # and delete the line this test is about to create under it.
            record = self._create(model)
            self.env.flush_all()
            return record
        found = Target.with_user(self.author).search([], limit=1)
        if found:
            return found
        # Scenery the author may not create (a tenant without any Nextcloud
        # configuration, say): the superuser builds it, the wizard stays theirs.
        if not self._acl_allows(self.author, model, "create"):
            return self._create(model, as_superuser=True)
        return self._create(model)

    def _create(self, model, as_superuser=False):
        """One wizard row, created by the author (a target: maybe by the superuser)."""
        context = CONTEXTS.get(model, {})
        if callable(context):
            context = context(self)
        Wizard = self.env[model] if as_superuser else self.env[model].with_user(self.author)
        Wizard = Wizard.with_context(**context)
        vals = VALUES.get(model, {})
        vals = dict(vals(self) if callable(vals) else vals)
        defaults = Wizard.default_get(list(Wizard._fields))
        for name, field in Wizard._fields.items():
            if (not field.required or not field.store or field.automatic
                    or name in vals or defaults.get(name) not in (None, False)):
                continue
            vals[name] = self._value_for(field)
        return Wizard.create(vals)

    # ------------------------------------------------------------------
    def test_every_wizard_is_listed_and_carries_the_rule(self):
        declared = {
            name for name in self.env.registry.models
            if self.env[name]._transient
            and self.env[name]._original_module == MODULE
        }
        self.assertLessEqual(set(WIZARDS), declared)
        self.assertLessEqual(set(UNBUILT), set(WIZARDS))
        # Any group, not only base.group_user: a wizard
        # open to a functional group of several users leaks the same way.
        # A wizard that already carries its own OWNER rule (written before
        # these rules) is covered by it and stays out of WIZARDS. A company rule
        # does not cover it: two colleagues of one company read each other's
        # rows.
        opened = {
            name for name in declared
            if self.env["ir.model.access"].sudo().search_count([
                ("model_id.model", "=", name), ("perm_read", "=", True),
            ])
            and (name in WIZARDS or not self.env["ir.rule"].sudo().search_count([
                ("model_id.model", "=", name), ("domain_force", "ilike", "create_uid")]))
        }
        self.assertEqual(
            opened - set(WIZARDS), set(),
            "a wizard of %s is opened by an ACL and not in WIZARDS "
            "(so it has no owner rule)" % MODULE)
        for model in WIZARDS:
            with self.subTest(model=model):
                # The module's own rule, by its xmlid: a model may also keep a rule
                # of its own (bf_kitchen: one per company), which ANDs with it.
                rules = self.env.ref(
                    "%s.%s_rule_owner" % (MODULE, model.replace(".", "_")),
                    raise_if_not_found=False)
                self.assertTrue(rules, "no owner rule on %s" % model)
                self.assertEqual(rules.model_id.model, model)
                self.assertEqual(
                    self.env["ir.rule"].sudo().search_count([
                        ("model_id.model", "=", model),
                        ("domain_force", "ilike", "create_uid")]), 1,
                    "one owner rule expected on %s" % model)
                # ⚠️ The field is named `global`, not `global_`: core sets it
                # with setattr because `global` is a Python keyword.
                self.assertTrue(rules["global"], "the rule of %s is not global" % model)
                self.assertEqual(rules.domain_force, "[('create_uid', '=', user.id)]")
                self.assertTrue(
                    rules.perm_read and rules.perm_write
                    and rules.perm_create and rules.perm_unlink,
                    "the rule of %s does not cover the four operations" % model)

    def _acl_allows(self, user, model, operation):
        """Whether the ACL alone lets `user` do `operation` on `model`."""
        return self.env["ir.model.access"].with_user(user).check(
            model, operation, raise_exception=False)

    def test_a_colleague_cannot_reach_the_authors_wizard(self):
        """One subtest per model, user AND operation.

        🔴 Separate subtests, so that the counter-test (rules deleted) proves
        each refusal on its own: in one block, the first failing assertion
        hid the others. An operation the ACL already refuses to that user is
        skipped: its AccessError would come from the ACL, not from the rule.
        """
        for model in WIZARDS:
            if model in UNBUILT:
                continue
            for other in (self.colleague, self.admin):
                # A fresh wizard per user: without the rule, the colleague's
                # unlink SUCCEEDS, and the admin's search would then pass on a
                # deleted record instead of on the rule (counter-test, 11-26).
                wizard = self._create(model)
                checks = {
                    "search": lambda: self.assertFalse(
                        self.env[model].with_user(other).search([("id", "=", wizard.id)]),
                        "%s finds the author's %s" % (other.login, model)),
                    "read": lambda: self.assertRaises(
                        AccessError, wizard.with_user(other).read, ["create_uid"]),
                    # check_access, not write(): a wizard has no field that
                    # every module shares, and log columns are not writable.
                    "write": lambda: self.assertRaises(
                        AccessError, wizard.with_user(other).check_access, "write"),
                    "unlink": lambda: self.assertRaises(
                        AccessError, wizard.with_user(other).unlink),
                }
                measured = set()
                for operation, check in checks.items():
                    acl = "read" if operation == "search" else operation
                    if not self._acl_allows(other, model, acl):
                        continue
                    measured.add(operation)
                    with self.subTest(model=model, user=other.login, operation=operation):
                        self.env.invalidate_all()
                        check()
                # Skipping on the ACL must never leave a model unmeasured: with
                # another lineage's ACL, this test could pass on nothing.
                self.assertLessEqual(
                    {"search", "read"}, measured,
                    "%s: nothing measured for %s" % (model, other.login))

    def test_the_author_keeps_their_own_wizard(self):
        for model in WIZARDS:
            if model in UNBUILT:
                continue
            with self.subTest(model=model):
                wizard = self._create(model)
                self.env.invalidate_all()
                mine = self.env[model].with_user(self.author)
                self.assertEqual(mine.search([("id", "=", wizard.id)]), wizard)
                # Every field the author may read, computed ones included: a
                # compute that reads the wizard back under the author must
                # still work. fields_get() leaves out the fields a `groups`
                # attribute keeps from them (an encrypted key, for instance).
                values = wizard.with_user(self.author).read(list(mine.fields_get(
                    attributes=["type"])))[0]
                self.assertEqual(values["create_uid"][0], self.author.id)
                wizard.with_user(self.author).check_access("write")

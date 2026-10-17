import re

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import ValidationError

KINDS = [
    ("key", "Key"),
    ("card", "Access card"),
    ("fob", "Fob"),
    ("remote", "Remote control"),
    ("other", "Other"),
]
# Cards, fobs and remotes are switched off in an access system; keys need a rekey.
ELECTRONIC = ("card", "fob", "remote")

LEVELS = [
    ("single", "Single key"),
    ("change", "Change key"),
    ("master", "Master key"),
    ("grand", "Grand master key"),
    ("great", "Great grand master key"),
]
MASTER_LEVELS = ("master", "grand", "great")
_BY_HEIGHT = {0: "change", 1: "master", 2: "grand", 3: "great"}

def close_activities(activities, feedback):
    """Mark activities done. ⚠️ Odoo posts the "done" message as the current user and
    refuses an author without email: many key holders and managers have none."""
    if not activities:
        return
    if not activities.env.user.partner_id.email:
        activities = activities.with_user(SUPERUSER_ID)
    activities.sudo().action_feedback(feedback=feedback)


def schedule_activity(record, **values):
    """Schedule an activity. ⚠️ Odoo emails the assignee "from" the current user and refuses an
    author without email (many managers have none): then the activity is still created, in the
    assignee's list, without the email notice."""
    if not record.env.user.partner_id.email:
        record = record.with_context(mail_activity_quick_update=True)
    return record.activity_schedule(**values)


# Standard Key Coding System: I, O and Q are never used as letters.
_L = "A-HJ-NPR-Z"


class BfKeyType(models.Model):
    """A key type: every copy cut to the same bitting, or one access profile.

    Placed in a master key system, a type has a parent (the key one level up,
    which opens everything this one opens). The doors a type opens are its own
    doors and those of every type below it.
    """
    _name = "bf.key.type"
    _description = "Key type"
    _inherit = ["mail.thread"]
    _parent_store = True
    # ⚠️ Never order by a field reserved to managers (system, symbol): every list of copies
    # or hand-outs is ordered through this one, and a holder could no longer list their keys.
    _order = "name, id"

    name = fields.Char(required=True, tracking=True)
    kind = fields.Selection(KINDS, required=True, default="key", tracking=True)
    system_id = fields.Many2one(
        "bf.key.system", string="Master key system", index=True, ondelete="restrict",
        groups="bf_key_management.group_key_area_manager")
    symbol = fields.Char(
        tracking=True, groups="bf_key_management.group_key_area_manager",
        help="Its place in the master key system, in the standard notation: A (grand master), "
             "AA (master), AA1 (change key) in a three-level system. Never stamped on the key.")
    parent_id = fields.Many2one(
        "bf.key.type", string="Key above", index=True, ondelete="restrict",
        groups="bf_key_management.group_key_area_manager",
        help="The key one level up in the master key system.")
    # ⚠️ Reserved too: the path spells out the master key hierarchy (and enables child_of).
    parent_path = fields.Char(index=True, groups="bf_key_management.group_key_area_manager")
    child_ids = fields.One2many(
        "bf.key.type", "parent_id", string="Keys below",
        groups="bf_key_management.group_key_area_manager")
    level = fields.Selection(
        LEVELS, compute="_compute_level", store=True, recursive=True,
        groups="bf_key_management.group_key_area_manager")
    depth = fields.Integer(compute="_compute_level", store=True, recursive=True,
                           groups="bf_key_management.group_key_area_manager")
    is_master = fields.Boolean(string="Master key", compute="_compute_level", store=True, recursive=True,
                               groups="bf_key_management.group_key_area_manager")
    symbol_warning = fields.Char(
        string="Symbol check", compute="_compute_symbol_warning", groups="bf_key_management.group_key_area_manager")
    area_id = fields.Many2one(
        "bf.key.area", string="Area", index=True,
        help="The people responsible for this area approve requests for this key and see its copies.")
    blind_code = fields.Char(
        string="Blind code", help="Code stamped on the copies with their number. It says nothing about the door, "
             "the level or the address.")
    approval_required = fields.Boolean(
        string="Approval required", compute="_compute_approval_required", store=True, readonly=False,
        help="A request for this key waits for the approval of the person responsible for the area. "
             "On by default for master keys.")
    max_days = fields.Integer(
        string="Expected return after (days)",
        help="Proposed return date at hand-out: the end of the need. Zero: no date proposed.")
    currency_id = fields.Many2one(related="company_id.currency_id")
    deposit = fields.Monetary(help="Deposit proposed at hand-out, refunded at return.")
    loss_fee = fields.Monetary(string="Loss fee", help="Proposed on the incident when a copy is lost.")
    copy_ids = fields.One2many("bf.key.copy", "type_id", string="Copies")
    copy_count = fields.Integer(string="Copies", compute="_compute_copy_counts")
    issued_count = fields.Integer(string="In circulation", compute="_compute_copy_counts")
    opened_door_ids = fields.Many2many(
        "bf.key.door", string="Doors it opens", compute="_compute_opened_door_ids",
        groups="bf_key_management.group_key_area_manager")
    opened_door_count = fields.Integer(
        string="Doors it opens", compute="_compute_opened_door_ids", groups="bf_key_management.group_key_area_manager")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    active = fields.Boolean(default=True)
    note = fields.Text()

    _sql_constraints = [
        ("symbol_unique", "unique(system_id, symbol)",
         "Two key types of the same master key system cannot share a symbol."),
    ]

    # ------------------------------------------------------------------
    @api.depends("parent_id.depth", "system_id.levels", "kind")
    def _compute_level(self):
        for key_type in self:
            # Walk up rather than read the parent's depth: the parent may be in the same
            # batch and not computed yet.
            depth, parent, seen = 0, key_type.parent_id, set()
            while parent and parent.id not in seen:
                seen.add(parent.id)
                depth += 1
                parent = parent.parent_id
            key_type.depth = depth
            if key_type.kind != "key":
                level = False  # a card or a remote sits outside any master key system
            elif not key_type.system_id:
                level = "single"
            else:
                height = int(key_type.system_id.levels) - 1 - key_type.depth
                level = _BY_HEIGHT.get(max(height, 0), "change")
            key_type.level = level
            key_type.is_master = level in MASTER_LEVELS

    @api.depends("level")
    def _compute_approval_required(self):
        for key_type in self:
            key_type.approval_required = key_type.level in MASTER_LEVELS

    @api.depends("symbol", "parent_id.symbol", "system_id.levels", "level")
    def _compute_symbol_warning(self):
        for key_type in self:
            key_type.symbol_warning = key_type._symbol_problem() or False

    def _symbol_problem(self):
        """What is odd about the symbol, in the Standard Key Coding System. A warning, not
        a refusal: real systems drift from the notation, and refusing loses the record."""
        self.ensure_one()
        symbol = (self.symbol or "").strip().upper()
        if not symbol or not self.system_id:
            return ""
        if symbol.startswith("X"):
            return _("Cross keyed: it opens doors outside its own branch. Guides advise "
                     "against cross keying; check that it is intended.")
        if symbol in ("NMK",) or symbol.startswith("SKD"):
            return ""
        if re.search(r"[IOQ]", symbol):
            return _("The letters I, O and Q are not used in key symbols.")
        levels = int(self.system_id.levels)
        parent = (self.parent_id.symbol or "").strip().upper() if self.parent_id else ""
        if self.level == "change":
            if levels == 2:
                ok = re.fullmatch(r"\d+" + re.escape(parent), symbol) if parent else \
                    re.fullmatch(rf"\d+[{_L}]{{2}}", symbol)
            else:
                ok = re.fullmatch(re.escape(parent) + r"\d+", symbol) if parent else \
                    re.fullmatch(rf"[{_L}]+\d+", symbol)
            expected = _("a number after the master's symbol (AA1)") if levels > 2 else \
                _("a number before the master's symbol (1AA)")
        elif self.level == "master":
            ok = re.fullmatch(re.escape(parent) + rf"\d?[{_L}]", symbol) if parent else \
                re.fullmatch(rf"[{_L}]{{2}}", symbol)
            expected = _("the grand master's letter followed by a letter (AA, AB)")
        elif self.level == "grand" and levels == 4:
            ok = re.fullmatch(rf"[{_L}]", symbol)
            expected = _("a single letter (A, B)")
        else:
            return ""
        if not ok:
            return _("The symbol %(symbol)s does not follow the notation: expected %(expected)s.",
                     symbol=symbol, expected=expected)
        return ""

    @api.constrains("parent_id", "system_id")
    def _check_parent(self):
        if self._has_cycle():
            raise ValidationError(_("A key cannot sit above itself in the master key system."))
        for key_type in self.sudo():
            parent = key_type.parent_id
            if parent and parent.system_id != key_type.system_id:
                raise ValidationError(_(
                    "%(key)s and the key above it must belong to the same master key system.",
                    key=key_type.name))
            if parent and parent.kind != "key":
                raise ValidationError(_("Only a key can sit above another key."))
            if key_type.system_id and key_type.depth >= int(key_type.system_id.levels):
                raise ValidationError(_(
                    "%(key)s is deeper than the %(levels)s levels of its master key system.",
                    key=key_type.name, levels=key_type.system_id.levels))

    # ------------------------------------------------------------------
    def _compute_copy_counts(self):
        for key_type in self:
            copies = key_type.copy_ids
            key_type.copy_count = len(copies)
            key_type.issued_count = len(copies.filtered(lambda c: c.state == "issued"))

    def _self_and_below(self):
        """This type and every type below it in the master key system."""
        self.ensure_one()
        return self.sudo().with_context(active_test=False).search([("id", "child_of", self.id)])

    def _doors_opened(self):
        """Doors this type opens: those keyed to it or to a key below it, except the cylinders
        that are not master keyed (only their own key opens them), plus the doors that also
        accept it (cross keying, cards)."""
        self.ensure_one()
        Door = self.env["bf.key.door"].sudo()
        below = self._self_and_below()
        keyed = Door.search([
            ("keyed_type_id", "in", below.ids),
            "|", ("nmk", "=", False), ("keyed_type_id", "=", self.id),
        ])
        extra = Door.search([("extra_type_ids", "in", below.ids)]).filtered(
            lambda d: not d.nmk or self in d.extra_type_ids)
        return keyed | extra

    def _compute_opened_door_ids(self):
        for key_type in self:
            if not key_type.id:
                key_type.opened_door_ids = False
                key_type.opened_door_count = 0
                continue
            doors = key_type._doors_opened()
            key_type.opened_door_ids = doors
            key_type.opened_door_count = len(doors)

    # ------------------------------------------------------------------
    def action_view_copies(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": _("Copies"), "res_model": "bf.key.copy",
            "view_mode": "list,form", "domain": [("type_id", "=", self.id)],
            "context": {"default_type_id": self.id},
        }

    def action_view_doors(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": _("Doors it opens"), "res_model": "bf.key.door",
            "view_mode": "list,form", "domain": [("id", "in", self._doors_opened().ids)],
        }

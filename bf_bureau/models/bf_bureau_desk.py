from odoo import api, fields, models


LAYOUT_SLOTS = {
    "single": ("full",),
    "two_columns": ("left_full", "right_full"),
    "two_top_one_bottom": ("top_left", "top_right", "bottom_full"),
    "two_bottom_one_top": ("top_full", "bottom_left", "bottom_right"),
    "four_quadrant": ("top_left", "top_right", "bottom_left", "bottom_right"),
    "stacked_three": ("row_1", "row_2", "row_3"),
}

LAYOUT_LABELS = [
    ("single", "Un seul panneau"),
    ("two_columns", "Deux colonnes"),
    ("two_top_one_bottom", "Deux haut + un bas"),
    ("two_bottom_one_top", "Un haut + deux bas"),
    ("four_quadrant", "Quatre quadrants (2×2)"),
    ("stacked_three", "Trois rangées empilées"),
]

# Hour ranges (24h, end exclusive) keyed by active_when value.
# `always` matches anytime — used as fallback.
ACTIVE_HOURS = {
    "always": None,
    "morning": (5, 12),
    "afternoon": (12, 18),
    "evening": (18, 24),
    "night": (0, 5),
}


class BfBureauDesk(models.Model):
    _name = "bf.bureau.desk"
    _description = "BF Bureau — vue multi-panneaux configurable"
    _order = "sequence, id"

    name = fields.Char(required=True)
    user_id = fields.Many2one(
        "res.users",
        required=True,
        default=lambda self: self.env.user,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    is_default = fields.Boolean(
        default=False,
        help="Bureau ouvert par défaut quand l'utilisateur clique sur Mon bureau "
             "(sauf si un autre bureau a un créneau horaire actif).",
    )
    layout = fields.Selection(
        LAYOUT_LABELS,
        default="two_top_one_bottom",
        required=True,
    )
    pane_ids = fields.One2many("bf.bureau.pane", "desk_id")
    active = fields.Boolean(default=True)
    shortcut_key = fields.Char(
        string="Raccourci clavier",
        help="Combinaison déclenchant l'ouverture du bureau, ex. « alt+1 » "
             "ou « shift+g ». Vide = pas de raccourci.",
    )
    active_when = fields.Selection(
        [
            ("always", "En tout temps"),
            ("morning", "Matin (5h–12h)"),
            ("afternoon", "Après-midi (12h–18h)"),
            ("evening", "Soir (18h–minuit)"),
            ("night", "Nuit (minuit–5h)"),
        ],
        default="always",
        required=True,
        help="Plage horaire pendant laquelle ce bureau prend automatiquement "
             "le pas sur le bureau « par défaut ».",
    )

    _sql_constraints = [
        (
            "user_default_unique",
            "EXCLUDE (user_id WITH =) WHERE (is_default AND active)",
            "Un seul bureau par défaut par utilisateur.",
        ),
        (
            "user_shortcut_unique",
            "EXCLUDE (user_id WITH =, shortcut_key WITH =) "
            "WHERE (shortcut_key IS NOT NULL AND shortcut_key != '' AND active)",
            "Ce raccourci est déjà utilisé par un autre bureau.",
        ),
    ]

    @api.model
    def get_default_desk_id(self):
        """Return the desk that should open right now for this user.

        Priority: time-of-day matching desk > user-marked default > first.
        """
        user_id = self.env.uid
        hour = fields.Datetime.context_timestamp(
            self, fields.Datetime.now(),
        ).hour
        # Search for any time-bracketed desk whose hour range covers `hour`.
        candidates = self.search(
            [("user_id", "=", user_id), ("active_when", "!=", "always")],
            order="sequence, id",
        )
        for desk in candidates:
            rng = ACTIVE_HOURS.get(desk.active_when)
            if rng and rng[0] <= hour < rng[1]:
                return desk.id
        # Fall back to the user's marked default.
        desk = self.search(
            [("user_id", "=", user_id), ("is_default", "=", True)],
            limit=1,
        )
        if not desk:
            desk = self.search(
                [("user_id", "=", user_id)], limit=1, order="sequence, id",
            )
        return desk.id or False

    @api.model
    def list_user_desks(self):
        """Return desks the current user owns, for the sidebar / hotkey reg."""
        desks = self.search([("user_id", "=", self.env.uid)])
        return [
            {
                "id": d.id,
                "name": d.name,
                "is_default": d.is_default,
                "shortcut_key": d.shortcut_key or "",
                "active_when": d.active_when,
                "layout": d.layout,
            }
            for d in desks
        ]

    @api.model
    def read_desk_for_render(self, desk_id):
        """Return all data the JS needs to render a desk in one round-trip."""
        if not desk_id:
            desk_id = self.get_default_desk_id()
        if not desk_id:
            return {"desk": False, "panes": []}
        desk = self.browse(desk_id).filtered(lambda d: d.active)
        desk.check_access_rights("read")
        desk.check_access_rule("read")
        if not desk:
            return {"desk": False, "panes": []}
        panes = []
        for pane in desk.pane_ids:
            action = pane.action_id.sudo().read([
                "id", "name", "res_model", "domain", "context",
                "view_mode", "search_view_id",
            ])[0]
            panes.append({
                "id": pane.id,
                "slot": pane.slot,
                "view_type": pane.view_type,
                "name_override": pane.name_override or "",
                "weight": pane.weight,
                "domain_override": pane.domain_override or "",
                "context_override": pane.context_override or "",
                "action": action,
            })
        return {
            "desk": {
                "id": desk.id,
                "name": desk.name,
                "layout": desk.layout,
            },
            "panes": panes,
        }

    @api.model
    def get_layout_slots(self, layout):
        return list(LAYOUT_SLOTS.get(layout, ()))

    def action_set_default(self):
        for desk in self:
            self.search([
                ("user_id", "=", desk.user_id.id),
                ("is_default", "=", True),
                ("id", "!=", desk.id),
            ]).write({"is_default": False})
            desk.is_default = True

    def action_duplicate_for_me(self):
        """Clone this desk + panes for the current user.

        Useful for A/B-ing layouts before committing to one.
        """
        self.ensure_one()
        copy = self.copy({
            "name": f"{self.name} (copie)",
            "user_id": self.env.uid,
            "is_default": False,
            "shortcut_key": False,
        })
        for pane in self.pane_ids:
            pane.copy({"desk_id": copy.id})
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.bureau.desk",
            "res_id": copy.id,
            "views": [[False, "form"]],
            "target": "current",
        }

    def action_open(self):
        """Open this desk in the BF bureau client action."""
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "bf_bureau_desk",
            "params": {"desk_id": self.id},
            "target": "current",
        }

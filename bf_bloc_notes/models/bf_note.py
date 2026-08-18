import re
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError

# Modèles remontés en tête du sélecteur : ce sont ceux qu'on route au
# quotidien. Le reste de la liste suit par ordre alphabétique.
ALLOWED_QUICK_CREATE_KEYS = {
    "name",
    "body",
    "tag_ids",
    "pinned",
    "color",
    "deadline_date",
    "is_shared",
    "res_model",
    "res_id",
    "link_ids",
}

REMINDER_OFFSETS = {
    "today": 0,
    "tomorrow": 1,
    "2days": 2,
    "1week": 7,
}


class BfNote(models.Model):
    _name = "bf.note"
    _description = "Note rapide"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "pinned desc, write_date desc"

    name = fields.Char(string="Titre", compute="_compute_name", store=True, readonly=False)
    body = fields.Html(
        string="Contenu",
        sanitize=True,
        sanitize_style=True,
        strip_classes=True,
    )
    active = fields.Boolean(default=True)
    pinned = fields.Boolean(default=False)
    color = fields.Integer(default=0)
    is_shared = fields.Boolean(
        string="Partagée",
        default=False,
        help="Si cochée, les autres utilisateurs internes peuvent voir cette note.",
    )
    deadline_date = fields.Date(string="Échéance")
    tag_ids = fields.Many2many("bf.note.tag", string="Étiquettes")

    user_id = fields.Many2one(
        "res.users",
        string="Auteur",
        default=lambda self: self.env.user,
        required=True,
        index=True,
    )

    link_ids = fields.One2many("bf.note.link", "note_id", string="Liens")
    res_ref = fields.Reference(
        string="Lien primaire",
        selection="_selection_target_model",
        compute="_compute_res_ref",
        inverse="_inverse_res_ref",
    )
    res_model = fields.Char(compute="_compute_res_model_id", store=True, index=True)
    res_id = fields.Integer(compute="_compute_res_model_id", store=True, index=True)
    res_name = fields.Char(string="Lié à", compute="_compute_res_name", store=True)

    tracked_activity_ids = fields.Many2many(
        "mail.activity",
        "bf_note_tracked_activity_rel",
        "note_id",
        "activity_id",
        string="Activités créées",
    )
    tracked_activity_count = fields.Integer(compute="_compute_tracked_activity_count")

    tracked_task_ids = fields.Many2many(
        "project.task",
        "bf_note_tracked_task_rel",
        "note_id",
        "task_id",
        string="Tâches créées",
    )
    tracked_task_count = fields.Integer(compute="_compute_tracked_task_count")

    @api.model
    def _selection_target_model(self):
        """Modèles auxquels une note peut être rattachée.

        Délégué à `bf.chatter.target` depuis la 2.9.0 : la même liste sert
        désormais à tous les importateurs. Le paramètre système
        `bf_bloc_notes.reference_models` reste honoré par le socle, donc une
        base qui l'avait renseigné garde sa restriction sans rien ressaisir.
        """
        return self.env["bf.chatter.target"]._thread_model_selection()

    @api.depends("link_ids", "link_ids.res_model", "link_ids.res_id")
    def _compute_res_ref(self):
        # res_ref is a Reference field: its value is validated against the field
        # selection (_selection_target_model), NOT just the registry. A link may
        # point to a model that exists but is outside that selection (e.g.
        # bf.email, meeting.agenda); assigning it would raise ValueError and break
        # web_read for the whole note. Fall back to False in that case — the link
        # itself stays in link_ids and remains openable via action_open_record.
        allowed = {model for model, _name in self._selection_target_model()}
        for note in self:
            primary = note.link_ids[:1]
            if primary and primary.res_model and primary.res_id and primary.res_model in allowed:
                note.res_ref = f"{primary.res_model},{primary.res_id}"
            else:
                note.res_ref = False

    def _inverse_res_ref(self):
        Link = self.env["bf.note.link"]
        for note in self:
            if note.res_ref:
                model = note.res_ref._name
                rid = note.res_ref.id
                existing = note.link_ids.filtered(
                    lambda l, m=model, r=rid: l.res_model == m and l.res_id == r
                )
                if not existing:
                    Link.create({"note_id": note.id, "res_model": model, "res_id": rid})
            # ne pas supprimer les autres liens si l'utilisateur efface res_ref ; res_ref est juste le primaire

    @api.depends("link_ids.res_model", "link_ids.res_id")
    def _compute_res_model_id(self):
        for note in self:
            primary = note.link_ids[:1]
            note.res_model = primary.res_model or False
            note.res_id = primary.res_id or 0

    @api.depends("link_ids", "link_ids.res_name")
    def _compute_res_name(self):
        for note in self:
            names = [l.res_name for l in note.link_ids if l.res_name]
            if not names:
                note.res_name = False
            elif len(names) == 1:
                note.res_name = names[0]
            else:
                note.res_name = f"{names[0]} (+{len(names) - 1})"

    @api.depends("tracked_activity_ids")
    def _compute_tracked_activity_count(self):
        for note in self:
            note.tracked_activity_count = len(note.tracked_activity_ids)

    @api.depends("tracked_task_ids")
    def _compute_tracked_task_count(self):
        for note in self:
            note.tracked_task_count = len(note.tracked_task_ids)

    @api.depends("body")
    def _compute_name(self):
        for note in self:
            if note.name:
                continue
            if not note.body:
                note.name = "Note sans titre"
                continue
            text = self._html_to_text(note.body)
            note.name = (text[:80] or "Note sans titre").strip()

    @staticmethod
    def _html_to_text(html):
        text = re.sub(r"<[^>]+>", " ", html or "")
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def action_toggle_pinned(self):
        for note in self:
            note.pinned = not note.pinned
        return True

    def action_open_record(self):
        self.ensure_one()
        primary = self.link_ids[:1]
        if not (primary and primary.res_model and primary.res_id and primary.res_model in self.env):
            return False
        target = self.env[primary.res_model].browse(primary.res_id).exists()
        if not target:
            return False
        try:
            target.check_access("read")
        except AccessError:
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": primary.res_model,
            "res_id": primary.res_id,
            "views": [(False, "form")],
            "target": "current",
        }

    def action_open_activities(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Activités créées depuis la note",
            "res_model": "mail.activity",
            "view_mode": "list,form",
            "domain": [("id", "in", self.tracked_activity_ids.ids)],
        }

    def action_create_activity_quick(self):
        """Create one mail.activity per linked record. Offset days passed in context.

        Default activity_type = mail_activity_data_todo (« Tâche / À faire »).
        """
        offset = int(self.env.context.get("offset", 0))
        for note in self:
            note._create_activities_for_links(offset_days=offset)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Activité(s) créée(s)",
                "message": f"{sum(n.tracked_activity_count for n in self)} activité(s) au total.",
                "sticky": False,
                "type": "success",
            },
        }

    def action_open_activity_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Convertir en activité",
            "res_model": "bf.note.activity.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_note_id": self.id},
        }

    def action_open_task_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Convertir en tâche",
            "res_model": "bf.note.task.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_note_id": self.id},
        }

    def action_open_reroute_wizard(self):
        """Ouvre le wizard de re-routage (une note ou une sélection)."""
        return {
            "type": "ir.actions.act_window",
            "name": "Re-router la note" if len(self) == 1 else "Re-router les notes",
            "res_model": "bf.note.reroute",
            "view_mode": "form",
            "target": "new",
            "context": {"default_note_ids": [(6, 0, self.ids)]},
        }

    def action_open_tasks(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Tâches créées depuis la note",
            "res_model": "project.task",
            "view_mode": "list,form",
            "domain": [("id", "in", self.tracked_task_ids.ids)],
        }

    def _create_activities_for_links(self, offset_days=0, activity_type_id=None, custom_deadline=None, link_target_ref=None):
        """Internal: create activities on each linked target. If no link, fall back to link_target_ref."""
        self.ensure_one()
        Activity = self.env["mail.activity"]
        IrModel = self.env["ir.model"].sudo()

        if custom_deadline:
            deadline = fields.Date.to_date(custom_deadline)
        else:
            deadline = fields.Date.context_today(self) + timedelta(days=offset_days)

        if not activity_type_id:
            activity_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
            activity_type_id = activity_type.id if activity_type else False

        candidates = [
            (link.res_model, link.res_id)
            for link in self.link_ids
            if link.res_model and link.res_id and link.res_model in self.env
        ]
        # Une activité posée sur un modèle sans `mail.activity.mixin`
        # (calendar.event, discuss.channel, blog.post…) existe en base mais ne
        # s'affiche nulle part : on la renvoie sur la note elle-même. Le test
        # passe par `is_mail_activity` — la présence d'un champ `activity_ids`
        # ne prouve rien (calendar.event en a un, sans porter la mixin).
        targets = []
        if candidates:
            activity_models = set(IrModel.search([
                ("model", "in", list({model for model, _rid in candidates})),
                ("is_mail_activity", "=", True),
            ]).mapped("model"))
            targets = [
                (model, rid) for model, rid in candidates if model in activity_models
            ]

        if not targets and link_target_ref:
            try:
                targets.append((link_target_ref._name, link_target_ref.id))
            except Exception:
                targets = []

        if not targets:
            # Pas de lien : crée l'activité sur la note elle-même
            targets.append(("bf.note", self.id))

        new_activities = self.env["mail.activity"]
        for model_name, rec_id in targets:
            model_rec = IrModel.search([("model", "=", model_name)], limit=1)
            if not model_rec:
                continue
            vals = {
                "res_model_id": model_rec.id,
                "res_model": model_name,
                "res_id": rec_id,
                "summary": self.name or "Note rapide",
                "note": self.body or "",
                "user_id": self.env.user.id,
                "date_deadline": deadline,
            }
            if activity_type_id:
                vals["activity_type_id"] = activity_type_id
            new_activities |= Activity.create(vals)

        if new_activities:
            self.tracked_activity_ids = [(4, a.id) for a in new_activities]
        return new_activities

    def _create_activity_on_self(self, deadline, activity_type_id=None):
        """Create a mail.activity on the bf.note record itself (no linked target)."""
        self.ensure_one()
        if not activity_type_id:
            activity_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
            activity_type_id = activity_type.id if activity_type else False
        IrModel = self.env["ir.model"].sudo()
        model_rec = IrModel.search([("model", "=", "bf.note")], limit=1)
        vals = {
            "res_model_id": model_rec.id,
            "res_model": "bf.note",
            "res_id": self.id,
            "summary": self.name or "Note rapide",
            "note": self.body or "",
            "user_id": self.env.user.id,
            "date_deadline": deadline,
        }
        if activity_type_id:
            vals["activity_type_id"] = activity_type_id
        activity = self.env["mail.activity"].create(vals)
        self.tracked_activity_ids = [(4, activity.id)]
        return activity

    @api.model
    def quick_create_from_context(self, vals):
        """Called from the OWL quick-create dialog. Returns the created id.

        SECURITY: only whitelisted keys are accepted; user_id is always forced
        to the calling user. Prevents RPC-injection on auteur/create_uid/etc.

        Optional `reminder_key` ("today" / "tomorrow" / "2days" / "1week" /
        "custom" / "none") and `reminder_date` (ISO date, only used when
        reminder_key == "custom") trigger activity creation on linked records,
        or set `deadline_date` on the note if there is no link yet.
        """
        clean = {k: v for k, v in (vals or {}).items() if k in ALLOWED_QUICK_CREATE_KEYS}
        clean["user_id"] = self.env.user.id

        legacy_model = clean.pop("res_model", None)
        legacy_id = clean.pop("res_id", None)
        if legacy_model and legacy_id:
            clean.setdefault("link_ids", [])
            clean["link_ids"].append((0, 0, {"res_model": legacy_model, "res_id": int(legacy_id)}))

        reminder_key = (vals or {}).get("reminder_key") or "none"
        reminder_date = (vals or {}).get("reminder_date")

        deadline = None
        if reminder_key == "custom" and reminder_date:
            deadline = fields.Date.to_date(reminder_date)
        elif reminder_key in REMINDER_OFFSETS:
            deadline = fields.Date.context_today(self) + timedelta(days=REMINDER_OFFSETS[reminder_key])

        if deadline and "deadline_date" not in clean:
            clean["deadline_date"] = deadline

        record = self.create(clean)

        if deadline:
            if record.link_ids:
                record._create_activities_for_links(custom_deadline=deadline)
            else:
                record._create_activity_on_self(deadline)

        return {
            "id": record.id,
            "name": record.name,
            "display_name": record.display_name,
        }

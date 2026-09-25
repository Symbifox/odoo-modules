import base64
import json
import logging

from odoo import SUPERUSER_ID, api, models
from odoo.exceptions import UserError
from odoo.tools.translate import LazyTranslate

from ..lib import render

_logger = logging.getLogger(__name__)
_lt = LazyTranslate(__name__)

PARAM_STYLE = "bf_avatar.style"
PARAM_PALETTE = "bf_avatar.palette"
PARAM_CONTACTS = "bf_avatar.contacts"
DEFAULT_STYLE = "initials"

SLOT_LABELS = {
    "head": _lt("Hair"),
    "hair": _lt("Hair"),
    "face": _lt("Expression"),
    "facialHair": _lt("Facial hair"),
    "beard": _lt("Beard"),
    "accessories": _lt("Glasses"),
    "glasses": _lt("Glasses"),
    "mask": _lt("Mask"),
    "body": _lt("Outfit"),
    "lips": _lt("Mouth"),
    "nose": _lt("Nose"),
    "eyes": _lt("Eyes"),
    "brows": _lt("Eyebrows"),
    "gesture": _lt("Gesture"),
    "bodyIcon": _lt("Badge"),
}
COLOUR_LABELS = {
    "skin": _lt("Skin"),
    "clothing": _lt("Clothing"),
    "headContrast": _lt("Hair colour"),
}


class BfAvatar(models.AbstractModel):
    """Everything that decides what a generated avatar looks like."""

    _name = "bf.avatar"
    _description = "Generated avatars"

    # --- Settings -------------------------------------------------------------

    @api.model
    def _style(self):
        style = self.env["ir.config_parameter"].sudo().get_param(PARAM_STYLE, DEFAULT_STYLE)
        return style if style in dict(render.STYLES) else DEFAULT_STYLE

    @api.model
    def _contacts_enabled(self):
        # Stored as "True"/"False" by res.config.settings.
        return self.env["ir.config_parameter"].sudo().get_param(PARAM_CONTACTS, "True") == "True"

    @api.model
    def _base_colours(self):
        palette = render.parse_palette(
            self.env["ir.config_parameter"].sudo().get_param(PARAM_PALETTE, ""))
        if palette:
            return palette
        company = self.env.ref("base.main_company", raise_if_not_found=False) or self.env.company
        colours = [c for c in (company.sudo().primary_color, company.sudo().secondary_color) if c]
        # Odoo's untouched defaults say nothing about the house.
        colours = [c for c in render.parse_palette(" ".join(colours))
                   if c not in ("#714b67", "#212529", "#8595a2")]
        return colours or ["#1f84af"]

    # --- Who is behind a record -----------------------------------------------

    @api.model
    def _seed_of(self, record):
        name = record[record._avatar_name_field] or ""
        return " ".join(name.split()).casefold()

    @api.model
    def _user_of(self, record):
        if record._name == "res.users":
            return record
        if record._name == "res.partner":
            return record.sudo().user_ids.filtered(lambda u: not u.share)[:1]
        if "user_id" in record._fields and record._fields["user_id"].comodel_name == "res.users":
            return record.sudo().user_id
        return self.env["res.users"]

    @api.model
    def _stored_config(self, user):
        raw = user.sudo().bf_avatar_config if user else False
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = False
        return raw if isinstance(raw, dict) else None

    # --- Rendering -------------------------------------------------------------

    @api.model
    def _render_for(self, record, style=None, config=None):
        style = style or self._style()
        seed = self._seed_of(record)
        if style == "initials":
            colour = render.pick_colour(render.initials_palette(self._base_colours()), seed)
            return render.render_initials(record[record._avatar_name_field], colour)
        if config is None:
            config = self._stored_config(self._user_of(record))
            if config and config.get("style") != style:
                config = None
        colour = render.pick_colour(render.character_palette(self._base_colours()), seed)
        return render.render_character(style, render.clean_config(style, config, seed), colour)

    @api.model
    def _render_b64(self, record, **kwargs):
        return base64.b64encode(self._render_for(record, **kwargs).encode())

    # --- Pass over stored avatars ---------------------------------------------

    @api.model
    def _generated_targets(self, model=None, ids=None):
        """(model, ids) whose stored picture is a generated SVG, never an upload.

        With `model` and `ids`, only those records are read: saving one
        person's avatar must not read every picture in the database.
        """
        self.env.flush_all()
        query = """
            SELECT id, res_model, res_id FROM ir_attachment
             WHERE res_field = 'image_1920'
               AND res_model IN ('res.partner', 'hr.employee')
               AND mimetype LIKE 'image/svg%%'
        """
        params = []
        if model:
            if not ids:
                return {}
            query += " AND res_model = %s AND res_id IN %s"
            params = [model, tuple(ids)]
        self.env.cr.execute(query, params)
        rows = self.env.cr.fetchall()
        targets = {}
        attachments = self.env["ir.attachment"].sudo().browse([r[0] for r in rows])
        for attachment in attachments.with_context(bin_size=False):
            if attachment.res_model not in self.env:
                continue
            if render.is_generated_svg(attachment.raw):
                targets.setdefault(attachment.res_model, []).append(attachment.res_id)
        return targets

    @api.model
    def _regenerate_generated(self):
        """Redraw every generated avatar in the current style. Uploads are left alone."""
        count = 0
        for model, ids in self._generated_targets().items():
            records = self.env[model].with_user(SUPERUSER_ID).with_context(
                tracking_disable=True, mail_notrack=True, active_test=False).browse(ids).exists()
            for record in records:
                if not record[record._avatar_name_field]:
                    continue
                record.write({"image_1920": record._avatar_generate_svg()})
                count += 1
        _logger.info("bf_avatar: %d generated avatar(s) redrawn in style %s", count, self._style())
        return count

    # --- Parts a person may not use yet (the gamification bridge fills this) --

    @api.model
    def _locked_parts(self, user, style):
        """{(slot, variant): {"reward_id", "name", "cost"}} for parts not owned yet."""
        return {}

    @api.model
    def _composer_extra(self, user, style):
        return {}

    # --- Composer ---------------------------------------------------------------

    @api.model
    def _check_character_style(self):
        if not self.env.user._is_internal():
            raise UserError(self.env._("The avatar composer is for internal users."))
        style = self._style()
        if style not in render.CHARACTER_STYLES:
            raise UserError(self.env._("Avatars in this database are not characters: there is nothing to compose."))
        return style

    @api.model
    def _slot_payload(self, person, style):
        data = render.load_style(style)
        locked = self._locked_parts(person, style)
        slots = []
        for slot in render.slot_order(style):
            variants = list(data["slots"][slot])
            if len(variants) < 2 and slot not in data["probabilities"]:
                continue
            slots.append({
                "name": slot,
                "label": self.env._(SLOT_LABELS[slot]) if slot in SLOT_LABELS else slot,
                "optional": slot in data["probabilities"],
                "variants": [{"key": v, "locked": locked.get((slot, v)) or False} for v in variants],
            })
        colours = [{
            "name": name,
            "label": self.env._(COLOUR_LABELS[name]) if name in COLOUR_LABELS else name,
            "values": values,
        } for name, values in sorted(data["colors"].items())]
        return slots, colours

    @api.model
    def _has_uploaded_photo(self, user):
        attachment = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "res.partner"), ("res_field", "=", "image_1920"),
            ("res_id", "=", user.partner_id.id)], limit=1)
        if not attachment:
            return False
        return not render.is_generated_svg(attachment.with_context(bin_size=False).raw)

    @api.model
    def _write_avatar(self, user, style, config):
        # Odoo stores an SVG written by someone who cannot edit views as plain
        # text, sudo or not (ir.attachment._check_contents asks the real user).
        # The SVG is ours, assembled from checked parts, so write it as the
        # superuser.
        # The partner is written, not the user: hr copies a user's new picture
        # over the employee's even when HR uploaded one there.
        svg = self._render_b64(user.partner_id, style=style, config=config)
        user = user.with_user(SUPERUSER_ID)
        user.partner_id.with_context(tracking_disable=True).write({"image_1920": svg})
        if "employee_ids" in user._fields:
            own = user.sudo().employee_ids
            generated = self._generated_targets("hr.employee", own.ids).get("hr.employee", [])
            employees = own.filtered(lambda e: e.id in generated)
            employees.with_context(tracking_disable=True).write({"image_1920": svg})

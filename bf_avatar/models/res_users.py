import json

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..lib import render


class ResUsers(models.Model):
    _inherit = "res.users"

    bf_avatar_config = fields.Json(
        string="Avatar composition", copy=False, groups="base.group_system",
        help="The parts a person picked in the avatar composer.")

    # The four methods below are the composer's whole API. None of them takes a
    # user: they only ever act on the person who calls them.

    @api.model
    def bf_avatar_composer(self):
        avatars = self.env["bf.avatar"]
        style = avatars._check_character_style()
        person = self.env.user
        seed = avatars._seed_of(person.partner_id)
        stored = avatars._stored_config(person)
        config = render.clean_config(style, stored if stored and stored.get("style") == style else None, seed)
        slots, colours = avatars._slot_payload(person, style)
        return dict(
            avatars._composer_extra(person, style),
            style=style,
            # The settings field carries the translated labels of the styles.
            style_label=dict(self.env["res.config.settings"]._fields["bf_avatar_style"]
                             ._description_selection(self.env))[style],
            slots=slots,
            colours=colours,
            config=config,
            preview=avatars._render_for(person.partner_id, style=style, config=config),
            has_photo=avatars._has_uploaded_photo(person),
            customised=bool(stored),
        )

    @api.model
    def bf_avatar_preview(self, config, slot=None):
        """The avatar with `config`; with `slot`, one thumbnail per variant of that slot."""
        avatars = self.env["bf.avatar"]
        style = avatars._check_character_style()
        person = self.env.user
        seed = avatars._seed_of(person.partner_id)
        config = render.clean_config(style, config, seed)
        if not slot:
            return {"preview": avatars._render_for(person.partner_id, style=style, config=config)}
        data = render.load_style(style)
        if slot not in data["slots"]:
            raise UserError(self.env._("Unknown part: %s", slot))
        thumbnails = {}
        for variant in data["slots"][slot]:
            thumbnails[variant] = avatars._render_for(
                person.partner_id, style=style, config=dict(config, **{slot: variant}))
        if slot in data["probabilities"]:
            thumbnails[""] = avatars._render_for(
                person.partner_id, style=style, config=dict(config, **{slot: None}))
        return {"thumbnails": thumbnails}

    @api.model
    def bf_avatar_save(self, config):
        avatars = self.env["bf.avatar"]
        style = avatars._check_character_style()
        person = self.env.user
        seed = avatars._seed_of(person.partner_id)
        config = render.clean_config(style, config, seed)
        locked = avatars._locked_parts(person, style)
        missing = sorted(part for part in render.parts_of(style, config) if part in locked)
        if missing:
            raise UserError(self.env._(
                "Some parts are not unlocked yet: %s", ", ".join("%s/%s" % p for p in missing)))
        person.sudo().bf_avatar_config = dict(config, style=style)
        avatars._write_avatar(person, style, config)
        return self.bf_avatar_composer()

    @api.model
    def bf_avatar_reset(self):
        """Forget the composition and go back to the avatar drawn from the name."""
        avatars = self.env["bf.avatar"]
        style = avatars._check_character_style()
        person = self.env.user
        person.sudo().bf_avatar_config = False
        avatars._write_avatar(person, style, render.clean_config(style, None, avatars._seed_of(person.partner_id)))
        return self.bf_avatar_composer()

# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
def post_init_hook(env):
    """La « Plage de rencontre » de bf_calendar_block ouvre la prise de rendez-vous,
    et les plages déjà posées deviennent « libres »."""
    plage = env.ref("bf_calendar_block.block_meeting", raise_if_not_found=False)
    if plage:
        plage.sudo().write({"bf_opens_booking": True, "bf_block_show_as": "free"})
        env["calendar.event"].sudo().with_context(skip_nc_sync=False).search(
            [("bf_block_type_id", "=", plage.id), ("show_as", "!=", "free")])._bf_forcer_libre()

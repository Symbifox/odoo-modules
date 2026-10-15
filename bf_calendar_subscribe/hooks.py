# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
def uninstall_hook(env):
    """Les événements importés partent avec le module (relecture adverse, M6).

    Par l'ORM, pour que leurs messages, abonnés et activités partent aussi
    (second tour) : le module est encore chargé quand ce crochet tourne.
    """
    env["calendar.event"].sudo().with_context(
        active_test=False, skip_nc_sync=True, no_mail_to_attendees=True,
    ).search([("bf_subscription_id", "!=", False)]).unlink()

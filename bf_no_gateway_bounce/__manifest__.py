{
    "name": "BF No Gateway Bounce",
    "version": "18.0.1.0.0",
    "category": "Tools",
    "summary": "Never auto-reply MAILER-DAEMON bounces to people who write to Symbifox",
    "description": """
        Suppresses the automatic "Dear Sender, the message below could not be
        accepted by the address ..." reply that Odoo's mail gateway sends back
        to whoever wrote in.

        Core builds that reply in four places, all funnelling through the single
        method mail.thread._routing_create_bounce_email:

          * mail_thread.message_route  - loop detection (too many messages)
          * mail_thread.message_route  - direct write to the catchall address
          * mail_thread.message_route  - catchall plus unroutable recipients
          * mail_alias._alias_bounce_incoming_email - alias misconfigured, or
            alias_contact rules rejected the sender

        None of it is configurable; it is hardcoded. Every address Symbifox
        publishes is live and monitored, so a sender who reaches us must never
        be told their message was refused. The senders that actually trip these
        paths are clients, suppliers and support desks - a supplier ticketing
        system acknowledging a request, an "Accepted: meeting" reply from
        Outlook, a client answering a notification - and the bounce reads as if
        Symbifox rejected them.

        This module overrides that one method to log and drop instead of
        sending. Nothing else changes: core still flags the alias as invalid
        before calling it, still logs the routing failure through
        _routing_warn, and inbound bounce *detection* (_routing_handle_bounce,
        which records real delivery failures against partners) is a different
        code path and is untouched.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["mail"],
    "auto_install": False,
    "installable": True,
    "application": False,
}

from odoo import models
from odoo.tools import formataddr


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def _bf_active_sender_email(self):
        """Return ``"Name <addr>"`` if the current user has a per-company
        sender address for the active company, else ``False``.

        This is the non-destructive replacement for ous_multi_company_email,
        which set the per-company From by overwriting the partner's email. Here
        we only influence the message's email_from at post time — the partner
        record is never touched.
        """
        user = self.env.user
        if user.share:
            return False
        company_email = user.with_company(self.env.company).bf_company_email
        if not company_email:
            return False
        return formataddr((user.name, company_email))

    def message_post(self, **kwargs):
        # Seulement pour un message dont l'usager courant est l'auteur : l'adresse
        # d'envoi de sa société ne doit pas signer le message de quelqu'un d'autre.
        own = self.env.user.partner_id
        if (not kwargs.get("email_from")
                and kwargs.get("author_id") in (None, False, own.id)):
            sender = self._bf_active_sender_email()
            if sender:
                kwargs["email_from"] = sender
                # Odoo cherche l'auteur par l'adresse quand on lui en donne une
                # sans auteur (`_message_compute_author`). L'adresse de la
                # société n'étant sur aucune fiche, le message partirait sans
                # auteur : sans signature, et anonyme au fil.
                kwargs["author_id"] = own.id
        return super().message_post(**kwargs)


    # ------------------------------------------------------------------
    # Per-company signature at notification time (18.0.2.0.0)
    # ------------------------------------------------------------------
    def _notify_by_email_prepare_rendering_context(self, message, msg_vals=False,
                                                   model_description=False,
                                                   force_email_company=False,
                                                   force_email_lang=False):
        """Sign with the company the record belongs to, not the author's home.

        ``res.users.signature`` is a single field: on a multi-company database
        it can only ever hold one company's signature, and Odoo hands that one
        to every outgoing notification. Here the layout has already resolved
        which company the message speaks for — that resolution is the one the
        recipient sees in the footer — so the signature follows it.

        Falls through untouched whenever there is nothing better to say: no
        author, signature not wanted, same company, or no rendering stored for
        that company yet.
        """
        values = super()._notify_by_email_prepare_rendering_context(
            message,
            msg_vals=msg_vals,
            model_description=model_description,
            force_email_company=force_email_company,
            force_email_lang=force_email_lang,
        )
        if not values.get("email_add_signature"):
            return values
        author_user = values.get("author_user")
        company = values.get("company")
        if not author_user or not company or author_user.share:
            return values
        if company == author_user.company_id:
            return values
        rendered = author_user.sudo().with_context(
            allowed_company_ids=[company.id],
        ).bf_signature_rendered
        if rendered:
            values["signature"] = rendered
        return values

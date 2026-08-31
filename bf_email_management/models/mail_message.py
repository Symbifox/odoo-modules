import base64
import logging

from odoo import _, fields, models

from .subject_utils import dedup_subject_prefix

_logger = logging.getLogger(__name__)


class MailMessage(models.Model):
    _inherit = "mail.message"

    # ------------------------------------------------------------------
    # Indicateur « traité / non traité » sur chaque message du chatter
    # ------------------------------------------------------------------
    def _to_store(self, store, /, **kwargs):
        """Ajoute ``bfEmailState`` à chaque message envoyé au client web.

        Les boutons « Traité » et « Remettre en boîte » vivaient côte à côte
        sur tous les messages, sans jamais dire lequel s'appliquait : rien
        n'indiquait si le courriel était déjà sorti de la boîte. On joint donc
        l'état du miroir bf.email de l'usager courant, et le chatter affiche
        une pastille.

        Valeurs : ``handled``, ``snoozed``, ``inbox`` ou ``False`` (aucun
        miroir). Posé uniquement quand ``for_current_user`` est vrai — c'est
        le seul chemin qui n'est pas diffusé à d'autres usagers, et cet état
        est strictement personnel.
        """
        res = super()._to_store(store, **kwargs)
        if not kwargs.get("for_current_user"):
            return res
        try:
            self._bf_email_state_to_store(store)
        except Exception:
            # Une pastille décorative ne doit jamais empêcher un chatter de
            # se rendre.
            _logger.warning(
                "bf.email: calcul de l'état chatter échoué", exc_info=True,
            )
        return res

    def _bf_email_state_to_store(self, store):
        """Une seule requête pour tout le lot de messages affichés."""
        candidates = self.filtered("message_id")
        if not candidates:
            return
        mids = list({m.message_id for m in candidates})
        # Env de l'usager (pas de sudo) : les règles d'enregistrement restent
        # l'autorité, et le domaine est de toute façon borné à ses lignes.
        rows = self.env["bf.email"].with_context(active_test=False).search_read(
            [
                ("message_id_header", "in", mids),
                ("user_id", "=", self.env.uid),
            ],
            ["message_id_header", "is_handled", "snoozed_until"],
        )
        now = fields.Datetime.now()
        state_by_mid = {}
        for row in rows:
            mid = row.get("message_id_header")
            if not mid:
                continue
            snoozed = row.get("snoozed_until")
            if snoozed and snoozed > now:
                state_by_mid[mid] = "snoozed"
            elif row.get("is_handled"):
                state_by_mid[mid] = "handled"
            else:
                state_by_mid[mid] = "inbox"
        for message in candidates:
            store.add(message, {
                "bfEmailState": state_by_mid.get(message.message_id, False),
            })

    def reply_message(self):
        """Collapse stacked Re: on the standard chatter quoted-reply button.

        ``mail_quoted_reply.reply_message`` sets ``default_subject`` to
        ``f"Re: {subject}"`` unconditionally, so replying to an already-"Re:"
        thread yields "Re: Re: …". Normalize it the same way bf.email's own
        reply flow does.
        """
        action = super().reply_message()
        ctx = action.get("context") or {}
        if ctx.get("default_subject"):
            ctx["default_subject"] = dedup_subject_prefix(
                ctx["default_subject"], force="Re:"
            )
            action["context"] = ctx
        return action

    def _prep_quoted_reply_body(self):
        """Same quote as ``mail_quoted_reply``, minus the signature.

        Le module tiers insère ``self.env.user.signature`` en clair au-dessus
        de la citation. Ici la signature est posée à l'envoi et nulle part
        ailleurs : la laisser dans le corps la ferait partir en double, une
        fois écrite ici et une fois ajoutée par le gabarit de notification.

        On retire le bloc du rendu plutôt que de recopier le gabarit du
        tiers : la signature y est insérée telle quelle, donc retrouvable
        telle quelle, et la mise en page de la citation reste la sienne — une
        copie divergerait à sa prochaine mise à jour.
        """
        body = super()._prep_quoted_reply_body()
        signature = self.env.user.signature or ""
        # ⚠️ Sans cette garde, ``replace("", ...)`` s'insère entre chaque
        # caractère du corps.
        if signature.strip() and signature in body:
            body = body.replace(signature, "", 1)
        return body

    def action_download_eml(self):
        """Stream this chatter message as an .eml download.

        Triggered from the message kebab menu (see static/src/js/
        bf_email_chatter_action.js). Reuses bf.email's RFC 2822 builder so
        rows mirrored from IMAP keep their original raw bytes (DKIM etc.).
        """
        self.ensure_one()
        self.check_access_rule("read")

        BfEmail = self.env["bf.email"].sudo()

        eml_bytes = None
        filename = None

        # Prefer a mirrored bf.email row with raw_rfc822 — that gives us
        # the exact bytes we received over IMAP. User-scoped so user A
        # can't download user B's raw bytes via a chatter message.
        if self.message_id:
            mirror = BfEmail.search([
                ("message_id_header", "=", self.message_id),
                ("user_id", "=", self.env.uid),
            ], limit=1)
            if mirror and mirror.raw_rfc822:
                try:
                    eml_bytes = base64.b64decode(mirror.raw_rfc822)
                    filename = mirror._eml_filename()
                except Exception:
                    _logger.warning(
                        "mail.message %s: bf.email mirror raw_rfc822 decode failed",
                        self.id,
                    )

        if eml_bytes is None:
            eml_bytes = BfEmail._build_eml_from_mail_message(self)
            filename = self._eml_filename_from_message()

        attachment = self.env["ir.attachment"].sudo().create({
            "name": filename,
            "datas": base64.b64encode(eml_bytes).decode("ascii"),
            "mimetype": "message/rfc822",
            "res_model": self._name,
            "res_id": self.id,
        })
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "self",
        }

    # ------------------------------------------------------------------
    # Chatter actions: manage the current user's bf.email mirror in place
    # (see static/src/js/bf_email_chatter_action.js)
    # ------------------------------------------------------------------
    def _bf_resolve_mirror(self, create_if_missing=True):
        """Return the CURRENT user's bf.email row for this chatter message.

        Same resolution as action_download_eml: match on Message-ID scoped
        to ``env.uid`` (no stored inverse field exists). When no row exists
        yet and the message qualifies for projection (a real email, or a
        chatter comment that produced email notifications), ingest it on
        the spot — mirror of imap_browser_mark_handled's ingest-then-act.
        Returns an empty recordset when nothing can be resolved.
        """
        self.ensure_one()
        self.check_access_rule("read")
        BfEmail = self.env["bf.email"]

        if self.message_id:
            mirror_id = BfEmail.sudo().search([
                ("message_id_header", "=", self.message_id),
                ("user_id", "=", self.env.uid),
            ], limit=1).id
            if mirror_id:
                # Re-enter the user's env: the row is theirs, no sudo needed
                # for the state change (and rules stay authoritative).
                return BfEmail.browse(mirror_id)

        if not create_if_missing:
            return BfEmail

        qualifies = self.message_type == "email" or (
            self.message_type == "comment"
            and any(
                n.notification_type == "email"
                for n in self.sudo().notification_ids
            )
        )
        if not qualifies:
            return BfEmail

        vals = BfEmail._prepare_email_vals(self.sudo())
        if not vals:
            return BfEmail
        try:
            with self.env.cr.savepoint():
                return BfEmail.with_context(
                    mail_create_nosubscribe=True,
                    tracking_disable=True,
                ).create(vals)
        except Exception:
            _logger.warning(
                "mail.message %s: bf.email mirror ingest failed",
                self.id, exc_info=True,
            )
            return BfEmail

    @staticmethod
    def _bf_chatter_notification(title, message, ntype="success"):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": ntype,
                "sticky": False,
            },
        }

    def action_bf_mark_handled(self):
        """Chatter button « Traité » — archive the mirror without leaving
        the record (is_handled + IMAP writeback + reminder activities)."""
        mirror = self._bf_resolve_mirror()
        if not mirror:
            return self._bf_chatter_notification(
                _("Aucun courriel lié"),
                _("Ce message n'a pas de courriel dans votre boîte."),
                "warning",
            )
        mirror.action_archive()
        return self._bf_chatter_notification(
            _("Traité"),
            _("« %(subject)s » est sorti de votre boîte de réception.",
              subject=mirror.subject or mirror.display_name),
        )

    def action_bf_snooze(self):
        """Chatter button « Reporter » — open the snooze wizard."""
        mirror = self._bf_resolve_mirror()
        if not mirror:
            return self._bf_chatter_notification(
                _("Aucun courriel lié"),
                _("Ce message n'a pas de courriel dans votre boîte."),
                "warning",
            )
        return mirror.action_snooze()

    def action_bf_unhandle(self):
        """Chatter button « Remettre en boîte » — undo Traité/snooze."""
        mirror = self._bf_resolve_mirror(create_if_missing=False)
        if not mirror:
            return self._bf_chatter_notification(
                _("Aucun courriel lié"),
                _("Ce message n'a pas de courriel dans votre boîte."),
                "warning",
            )
        mirror.action_unhandle()
        return self._bf_chatter_notification(
            _("Remis en boîte"),
            _("« %(subject)s » est de retour dans votre boîte de réception.",
              subject=mirror.subject or mirror.display_name),
        )

    def _eml_filename_from_message(self):
        """Filename for direct mail.message downloads (no bf.email mirror)."""
        self.ensure_one()
        BfEmail = self.env["bf.email"].sudo()
        date_part = self.date.strftime("%Y-%m-%d") if self.date else "undated"
        from email.utils import parseaddr
        _, bare = parseaddr(self.email_from or "")
        local = (bare.split("@", 1)[0] if bare else "") or "unknown"
        local = BfEmail._eml_slug(local)
        subject = BfEmail._eml_slug(self.subject or "")
        parts = [p for p in (date_part, local, subject) if p]
        stem = "_".join(parts) or f"message_{self.id}"
        return f"{stem[:120]}.eml"

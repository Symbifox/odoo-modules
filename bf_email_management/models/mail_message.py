import base64
import logging

from odoo import _, fields, models

from .bf_email import split_address_list
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
        """La citation du module tiers, sa signature marquée ou retirée.

        ``mail_quoted_reply`` insère ``self.env.user.signature`` en clair
        au-dessus de la citation. Selon le réglage « Où vit la signature » :

        - en mode **brouillon**, on la garde et on l'enveloppe du marqueur,
          pour que l'envoi sache que le corps en porte déjà une ;
        - en mode **envoi**, on la retire : elle sera posée au rendu, et la
          laisser ici la ferait partir en double.

        On agit sur le rendu du tiers plutôt que d'en recopier le gabarit :
        la signature y est insérée telle quelle, donc retrouvable telle
        quelle, et la mise en page de la citation reste la sienne — une copie
        divergerait à sa prochaine mise à jour.
        """
        body = super()._prep_quoted_reply_body()
        signature = self.env.user.signature or ""
        # ⚠️ Sans cette garde, ``replace("", ...)`` s'insère entre chaque
        # caractère du corps.
        if not signature.strip() or signature not in body:
            return body
        BfEmail = self.env["bf.email"]
        if BfEmail._signature_placement() != "brouillon":
            return body.replace(signature, "", 1)
        marque = f'<div class="{BfEmail.SIGNATURE_MARKER}">{signature}</div>'
        return body.replace(signature, marque, 1)

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

    # ------------------------------------------------------------------
    # « Répondre à tous » depuis le chatter
    # ------------------------------------------------------------------
    def _bf_reply_all_addresses(self):
        """Retourne ``(direction, email_from, to_addrs, cc_addrs)``.

        🔴 **Le chatter d'Odoo ne garde pas les en-têtes ``To:`` et ``Cc:``
        d'un courriel entrant.** ``_message_route_process`` ne conserve que
        les ``partner_ids`` que ``_mail_find_partner_from_emails`` a su
        reconnaître, et il écarte les adresses de l'instance : un
        courriel adressé au seul catchall repart avec une liste vide. Mesuré
        sur une base réelle : la grande majorité des messages de type
        ``email`` n'y portent ni ``To:`` ni ``Cc:``.

        Trois sources, et on les **additionne** au lieu d'en élire une :

        1. **le miroir ``bf.email``** : la projection de la passerelle garde
           les en-têtes tels quels, et tout courriel routé par la passerelle
           en a un ;
        2. **``mail.message.email_to`` / ``email_cc``** (ajoutés par
           ``mail_tracking``), remplis de façon irrégulière ;
        3. **``partner_ids`` et ``recipient_cc_ids``**, fidèles pour ce que
           NOUS avons envoyé, puisque c'est Odoo qui a composé la liste.

        ⚠️ Élire une seule source perd des gens, et le premier essai le
        faisait : un message sortant porte des partenaires en
        ``recipient_cc_ids`` que son miroir, projeté depuis le chatter, ne
        connaît pas, parce que ``_prepare_email_vals`` ne lit pas ce champ.
        Préférer le miroir « parce qu'il a des en-têtes » supprimait donc des
        destinataires sans rien dire. Le dédoublonnage se fait plus loin, par
        partenaire, ce qui rend l'union sans danger.

        Le miroir est cherché en ``sudo`` et sans filtre d'usager : ce qu'on y
        lit, ce sont les en-têtes d'un message que l'usager a déjà sous les
        yeux dans ce chatter, jamais l'état personnel d'une boîte.
        """
        self.ensure_one()
        BfEmail = self.env["bf.email"]

        mirror = BfEmail.browse()
        if self.message_id:
            mirror = BfEmail.sudo().with_context(active_test=False).search(
                [("message_id_header", "=", self.message_id)], order="id asc",
            ).filtered(lambda r: r.email_to or r.email_cc)[:1]

        # ⚠️ ``email_to`` / ``email_cc`` sur mail.message viennent de
        # ``mail_tracking`` (OCA), qui n'est PAS une dépendance de ce module :
        # ils existent sur certaines instances et manquent ailleurs. Y toucher
        # sans garde lève un AttributeError là où le module n'est pas installé.
        # C'est une base neuve, sans mail_tracking, qui l'a attrapé.
        to_addrs = (split_address_list(self.email_to)
                    if "email_to" in self._fields else [])
        cc_addrs = (split_address_list(self.email_cc)
                    if "email_cc" in self._fields else [])
        if mirror:
            to_addrs += split_address_list(mirror.email_to)
            cc_addrs += split_address_list(mirror.email_cc)
        to_addrs += [(p.name or "", p.email) for p in self.partner_ids if p.email]
        if "recipient_cc_ids" in self._fields:
            cc_addrs += [
                (p.name or "", p.email)
                for p in self.recipient_cc_ids if p.email
            ]

        direction = mirror.direction if mirror else BfEmail._detect_direction(self)
        email_from = self.email_from or (mirror.email_from if mirror else "") or ""
        return direction, email_from, to_addrs, cc_addrs

    def _bf_reply_all_recipients(self):
        """Retourne ``(to_ids, cc_ids)`` pour un « Répondre à tous ».

        Même partage que ``bf.email`` : « À » = qui a écrit (ou, sur un
        message sortant, qui on visait), « Cc » = tout le reste du fil, moins
        nos propres adresses et celles qui reviennent dans cet Odoo.
        """
        self.ensure_one()
        BfEmail = self.env["bf.email"]
        direction, email_from, to_addrs, cc_addrs = self._bf_reply_all_addresses()

        if direction == "in":
            to_source = split_address_list(email_from)
            rest = to_addrs + cc_addrs
        else:
            to_source = to_addrs
            rest = cc_addrs

        # 🔴 Deux ensembles, pas un. Celui du « À » n'écarte que nos propres
        # adresses et celles qui reviennent dans cet Odoo ; celui du « Cc » y
        # ajoute l'expéditeur, qu'on vient de mettre dans le « À ». Confondre
        # les deux vide la ligne « À » d'un entrant (l'expéditeur s'excluait
        # lui-même), et le repli sur l'auteur adressait alors OdooBot.
        exclude_to = BfEmail._bf_reply_exclusion_set()
        exclude_cc = set(exclude_to)
        for _display_name, bare in to_source:
            if bare:
                exclude_cc.add(bare.lower())

        to_ids = BfEmail._bf_partner_ids_from_addresses(to_source, exclude_to)
        # ⚠️ Le repli sur l'auteur ne vaut QUE pour un entrant. Sur un message
        # que nous avons envoyé, l'auteur c'est nous : le repli ouvrirait un
        # composeur adressé à soi-même, ce qui a l'air de marcher et ne
        # prévient personne.
        if not to_ids and direction == "in" and self.author_id.email:
            to_ids = [self.author_id.id]
        cc_ids = BfEmail._bf_partner_ids_from_addresses(
            rest, exclude_cc, skip_ids=to_ids,
        )
        return to_ids, cc_ids

    def action_bf_reply_all(self):
        """Bouton « Répondre à tous » du menu « … » du chatter.

        Le corps cité, l'objet dédoublonné et le traitement de la signature
        viennent de ``reply_message``, celui de ``mail_quoted_reply``, déjà
        rectifié plus haut dans ce fichier. Ce qui change ici, et seulement
        ça : la liste des destinataires.
        """
        self.ensure_one()
        self.check_access_rule("read")
        to_ids, cc_ids = self._bf_reply_all_recipients()
        # 🔴 Un composeur ouvert sans un seul destinataire part « à personne » :
        # le message naît bien dans le chatter, visible comme n'importe quel
        # envoi, sans un seul mail.notification. Le module a déjà payé ce
        # défaut une fois (voir _bf_retarget_to_chatter). On le dit tout haut
        # plutôt que d'ouvrir une fenêtre qui ment.
        if not to_ids and not cc_ids:
            return self._bf_chatter_notification(
                _("Personne à qui répondre"),
                _("Ce message ne porte aucun destinataire hors de l'instance. "
                  "Le bouton « Envoyer un message » du chatter écrit aux "
                  "abonnés de la fiche."),
                "warning",
            )
        action = self.reply_message()
        ctx = dict(action.get("context") or {})
        ctx["default_partner_ids"] = [(6, 0, to_ids)]
        ctx["default_partner_cc_ids"] = [(6, 0, cc_ids)]
        ctx["default_partner_bcc_ids"] = [(6, 0, [])]
        action["context"] = ctx
        return action

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

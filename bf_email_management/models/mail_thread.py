"""Rattraper au routage les réponses qui visent la boîte plutôt qu'un dossier.

``bf.email`` hérite de ``mail.thread`` : la passerelle sait donc déposer un
message sur une rangée de la boîte, et elle le fait dès qu'un correspondant
répond à un envoi parti de la boîte avant que la rangée soit classée — son
Message-ID porte alors ``openerp-<id>-bf.email``. Le fil quitte le dossier, et
il n'y revient pas tout seul : chaque réponse suivante cite le même en-tête.

``bf.email._composer_target`` empêche les nouveaux cas. Cette garde-ci sert les
fils DÉJÀ partis : le Message-ID est chez le correspondant, on ne peut pas le
reprendre, mais on peut lire où la rangée visée est classée et y rediriger le
message. Une réécriture de route, pas un second envoi.
"""

import logging

from markupsafe import Markup

from odoo import api, models

_logger = logging.getLogger(__name__)


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    # ------------------------------------------------------------------
    # Envois du téléphone : les destinataires saisis, et personne d'autre
    # ------------------------------------------------------------------
    def _get_notify_valid_parameters(self):
        """Déclarer la portée de la garde comme paramètre de notification.

        🔴 Le noyau refuse tout paramètre inconnu
        (``_raise_for_invalid_parameters``, appelé en tête de
        ``_notify_thread``) : sans cette déclaration, faire voyager la portée
        fait lever ``ValueError`` sur TOUT envoi gardé, y compris ceux qui
        marchaient avant. Mesuré au banc le 2026-09-15 : cinq essais en erreur.
        """
        return super()._get_notify_valid_parameters() | {
            "bf_notify_explicit_only", "bf_abonnes_retires"}

    def message_notify(self, **kwargs):
        """L'avis d'échec d'un envoi programmé ne va qu'à son auteur.

        🔴 Quand un programmé ne peut pas partir (fiche supprimée, accès
        retiré…), le noyau prévient son auteur par ``message_notify`` DEPUIS
        l'environnement du programmé. Celui-ci porte le contexte Cc/Cci que
        ``mail.scheduled.message._post_message`` repose pour l'envoi, et
        ``mail_composer_cc_bcc`` ajoutait alors la copie conforme et la copie
        cachée aux destinataires de l'avis — corps du message compris, le Cci
        en « À ». Mesuré au banc (relecture adverse du 2026-09-16).
        """
        if self.env.context.get("bf_copies_programmees"):
            vide = self.env["res.partner"]
            self = self.with_context(
                is_from_composer=False,
                partner_cc_ids=vide,
                partner_bcc_ids=vide,
                bf_copies_programmees=False,
            )
        return super(MailThread, self).message_notify(**kwargs)

    def _notify_thread(self, message, msg_vals=False, **kwargs):
        """Faire suivre la portée de la garde jusqu'au RENVOI différé.

        🔴 Quand l'envoi est différé — ``mail_post_defer`` le fait par défaut
        sur tout ce qui ne force pas l'envoi — Odoo JETTE la liste de
        destinataires qu'on vient de filtrer : il ne garde que
        ``notification_parameters``, et le cron rappelle ``_notify_thread``
        depuis SON environnement, sans notre contexte et avec
        ``msg_vals=False``. La garde ressortait donc sans rien filtrer, et les
        abonnés recevaient trente secondes plus tard ce que l'envoi immédiat
        leur avait refusé. Mesuré au banc le 2026-09-15 : deux abonnés notifiés
        après le cron, zéro avant.

        Les ``kwargs``, eux, sont sérialisés avec la programmation et rendus au
        cron : la portée voyage là, et ``_notify_get_recipients`` la relit des
        deux côtés. Les deux chemins du téléphone forcent l'envoi aujourd'hui
        (le composeur pose ``force_send`` en fiche unique), donc rien ne change
        pour eux — c'est la garantie qui devient inconditionnelle.
        """
        scope = self.env.context.get("bf_notify_explicit_only")
        if scope and "bf_notify_explicit_only" not in kwargs:
            kwargs["bf_notify_explicit_only"] = list(scope)
        # Même voyage pour le retrait d'abonnés du poste : sans lui,
        # `mail_post_defer` rendrait trente secondes plus tard ce que l'envoi
        # venait de retirer.
        retrait = self.env.context.get("bf_abonnes_retires")
        if retrait and "bf_abonnes_retires" not in kwargs:
            kwargs["bf_abonnes_retires"] = list(retrait)
        return super()._notify_thread(message, msg_vals=msg_vals, **kwargs)

    def _notify_get_recipients(self, message, msg_vals, **kwargs):
        """Sous ``bf_notify_explicit_only``, ne garder que les destinataires
        EXPLICITES du message : ses ``partner_ids`` (le « À ») et le Cc / Cci
        du composeur.

        🔴 Posé par ``bf.email._mobile_post`` et ``bf.email.mobile_draft_send``,
        et par eux seuls. Une réponse, un brouillon ou
        un nouveau courriel écrit au téléphone sur une fiche partait aussi à
        ses abonnés, portail compris : le client qui suit une tâche recevait
        la réponse destinée à un fournisseur, et le fournisseur voyait
        l'adresse du client dans le « À », puisque ``mail_composer_cc_bcc``
        regroupe tous les destinataires en un seul courriel. Le poste montre
        ces abonnés avant l'envoi ; le téléphone, non. Audit du 2026-09-08
        (S-M6).

        ⚠️ La portée nomme la fiche visée ``(modèle, id)`` : un autre
        message posté pendant l'envoi, ailleurs, garde ses destinataires.
        """
        recipients = super()._notify_get_recipients(message, msg_vals, **kwargs)
        explicit = self._bf_destinataires_explicites(message, msg_vals)
        scope = (self.env.context.get("bf_notify_explicit_only")
                 or kwargs.get("bf_notify_explicit_only"))
        if scope and self and len(self) == 1 \
                and (self._name, self.id) == tuple(scope):
            return [r for r in recipients if r.get("id") in explicit]
        # 🔴 Le retrait d'abonnés du poste vit DANS la même méthode, pas dans
        # une seconde surcharge : deux ``def`` du même nom dans une classe, et
        # Python garde la dernière sans un mot, la garde du téléphone
        # disparaîtrait, et rien ne le dirait.
        # Le ``- explicit`` n'est PAS la même garde que celle du composeur :
        # celui-ci a déjà purgé sa liste avant d'écrire la portée, si bien que
        # le retrait qui arrive ici ne contient jamais un destinataire saisi.
        # Mesuré par mutation le 2026-09-18 : retirer cette soustraction ne
        # fait tomber aucun essai passant par le composeur. Elle tient pour
        # tout AUTRE appelant, la portée est un simple contexte, n'importe
        # quel code peut la poser, et ``test_la_garde_posee_a_la_main_...``
        # est l'essai qui la mesure pour elle-même.
        retires = self._bf_abonnes_retires(**kwargs) - explicit
        if retires:
            return [r for r in recipients if r.get("id") not in retires]
        return recipients

    def _bf_destinataires_explicites(self, message, msg_vals):
        """Les destinataires SAISIS : le « À » du message, son Cc et son Cci."""
        if msg_vals and "partner_ids" in msg_vals:
            explicit = set(msg_vals.get("partner_ids") or [])
        else:
            explicit = set(message.sudo().partner_ids.ids)
        Partner = self.env["res.partner"]
        for key in ("partner_cc_ids", "partner_bcc_ids"):
            explicit |= set(self.env.context.get(key, Partner).ids)
        return explicit

    def _bf_abonnes_retires(self, **kwargs):
        """Les abonnés que le composeur du poste a retirés pour CETTE fiche.

        La garde voyage en trois morceaux, modèle, identifiant, abonnés, et
        elle passe par le JSON de ``notification_parameters`` quand l'envoi est
        programmé : le tuple en ressort en liste, d'où la normalisation.

        ⚠️ La fiche est nommée pour que le retrait ne déborde pas. Sans elle,
        un message posté ailleurs pendant l'envoi, un journal d'activité, une
        note automatique, perdrait les mêmes destinataires.
        """
        brut = (self.env.context.get("bf_abonnes_retires")
                or kwargs.get("bf_abonnes_retires"))
        if not brut or len(brut) != 3 or not self or len(self) != 1:
            return set()
        modele, res_id, ids = brut
        try:
            if (self._name, self.id) != (modele, int(res_id)):
                return set()
        except (TypeError, ValueError):
            return set()
        return {int(i) for i in (ids or [])}



    # ------------------------------------------------------------------
    # Signature : posée ici, jamais dans le corps
    # ------------------------------------------------------------------
    def _notify_by_email_prepare_rendering_context(
            self, message, msg_vals=False, model_description=False,
            force_email_company=False, force_email_lang=False):
        """Signer de l'identité qui expédie, quand elle a sa propre signature.

        Odoo signe de ``res.users.signature`` : une personne, une signature.
        Ce module lui donne plusieurs adresses d'envoi vérifiées, chacune
        pouvant porter la sienne. L'identité est résolue par l'adresse du
        « De » du message — le composeur, transitoire, n'existe plus à
        l'instant du rendu, et le « De » est de toute façon ce que le
        destinataire lit.

        ⚠️ C'est aussi ici que se décide s'il faut en poser une : quand le
        corps en porte DÉJÀ une — mode « brouillon », le défaut — le marqueur
        ``data-bf-signature`` le dit, et le gabarit n'en ajoute pas une
        seconde. C'est la seule garde qui tienne quel que soit le chemin
        d'envoi ; sans elle le destinataire en reçoit deux.

        On ne touche à rien quand l'identité n'a pas de signature propre : la
        valeur calculée plus haut reste, y compris celle que
        ``bf_multi_company_email`` substitue quand le message parle pour une
        autre société que celle de son auteur.
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
        # Le corps porte déjà sa signature : ne pas en ajouter une deuxième.
        corps = (msg_vals or {}).get("body") or message.body or ""
        if self.env["bf.email"].SIGNATURE_MARKER in corps:
            values["email_add_signature"] = False
            return values
        author_user = values.get("author_user")
        if not author_user or author_user.share:
            return values
        email_from = (msg_vals or {}).get("email_from") or message.email_from
        identity = self.env["bf.email.identity"].sudo()._for_sender(
            email_from, author_user)
        if not identity:
            return values
        # Même cascade que le brouillon et que l'aperçu du composeur : la
        # signature de l'identité, puis celle de la société de son COMPTE,
        # puis rien (Odoo pose alors `res.users.signature`). Sans le deuxième
        # temps, une identité d'une autre société sans signature propre
        # repartait signée de la société principale de la personne.
        signature = self.env["bf.email"].with_user(
            author_user)._signature_for_identity(identity)
        if (signature or "").strip():
            values["signature"] = signature
        return values

    # Ce qui ouvre une citation, dans les trois chemins du module : la réponse
    # depuis la boîte et le bouton Répondre du chatter posent un
    # ``<blockquote>``, le transfert pose son entête « Forwarded message ».
    # ⚠️ Pas ``data-o-mail-quote`` : cet attribut décore aussi les signatures
    # elles-mêmes, l'ancre tomberait n'importe où.
    _BF_QUOTE_MARKERS = ("<blockquote", "---------- forwarded message")

    def _bf_quote_offset(self, body):
        """Où commence la citation dans ce corps, -1 s'il n'y en a pas.

        La PREMIÈRE occurrence, donc la citation la plus externe : dans un fil
        qui s'empile, la signature doit passer au-dessus de tout le bloc cité,
        pas se glisser entre deux niveaux.
        """
        minuscule = (body or "").lower()
        positions = [p for p in (minuscule.find(m) for m in self._BF_QUOTE_MARKERS) if p >= 0]
        return min(positions) if positions else -1

    def _notify_by_email_render_layout(self, message, recipients_group,
                                       msg_vals=False, render_values=None):
        """Poser la signature au-dessus de la citation, pas sous elle.

        Le gabarit d'Odoo rend ``message.body`` d'un bloc puis ajoute la
        signature dessous : dans une réponse, elle se retrouve **après** tout
        le fil cité. Avant que la signature quitte le corps, elle tombait au
        bon endroit parce qu'elle était écrite là — au prix du doublon que la
        18.0.11.9.0 a supprimé.

        On garde donc l'unicité et on récupère l'ordre de lecture : le gabarit
        n'ajoute rien, et la signature est insérée dans le rendu juste avant
        l'ouverture de la citation.

        ⚠️ Trois cas retombent volontairement sur le comportement d'Odoo, la
        signature en fin de courriel : pas de citation (un courriel neuf n'a
        rien au-dessus de quoi passer), une citation qui commence au tout
        début du corps (personne n'a écrit au-dessus), et un corps qui ne
        ressort pas tel quel du rendu. Ce dernier repli compte : perdre la
        signature parce qu'on n'a pas retrouvé son ancre serait pire que la
        poser trop bas.
        """
        valeurs = dict(render_values or {})
        signature = valeurs.get("signature") or ""
        corps = (msg_vals or {}).get("body") or message.body or ""
        depart = self._bf_quote_offset(corps)
        if not (valeurs.get("email_add_signature") and signature.strip() and depart > 0):
            return super()._notify_by_email_render_layout(
                message, recipients_group, msg_vals=msg_vals,
                render_values=render_values)

        valeurs["email_add_signature"] = False
        rendu = super()._notify_by_email_render_layout(
            message, recipients_group, msg_vals=msg_vals, render_values=valeurs)
        rendu = Markup(rendu if isinstance(rendu, str) else (rendu or b"").decode())
        # ⚠️ ``Markup.replace`` échappe tout argument qui n'est pas déjà du
        # Markup : un bloc en ``str`` ressortait en clair dans le courriel,
        # `&lt;div style=…` au lieu de la signature. Les deux côtés doivent
        # donc être déclarés sûrs — ils le sont, c'est le HTML que le gabarit
        # aurait rendu tel quel.
        ancre = Markup(corps[depart:depart + 120])
        if ancre not in rendu:
            _logger.info(
                "bf_email_management: ancre de citation introuvable dans le "
                "rendu du message %s, signature laissée en fin de courriel",
                message.id)
            return super()._notify_by_email_render_layout(
                message, recipients_group, msg_vals=msg_vals,
                render_values=render_values)
        bloc = Markup('<div style="font-size: 13px;">%s</div>') % Markup(signature)
        return rendu.replace(ancre, bloc + ancre, 1)

    @api.model
    def message_route(self, message, message_dict, model=None,
                      thread_id=None, custom_values=None):
        routes = super().message_route(
            message, message_dict, model=model, thread_id=thread_id,
            custom_values=custom_values,
        )
        return [self._bf_email_redirect_route(route) for route in routes]

    @api.model
    def _bf_email_redirect_route(self, route):
        """Réécrire une route qui vise la boîte vers le dossier du fil.

        ``route`` est le 5-uplet d'Odoo ``(modèle, id, valeurs, usager,
        alias)``. Seuls les deux premiers changent : l'alias et les valeurs
        par défaut que la passerelle a choisis restent ceux qu'elle a choisis.
        """
        if not isinstance(route, (list, tuple)) or len(route) < 2:
            return route
        if route[0] != "bf.email" or not route[1]:
            return route
        row = self.env["bf.email"].sudo().browse(route[1]).exists()
        if not row:
            return route
        target = row._filing_target()
        if not target:
            return route
        _logger.info(
            "bf.email : message routé sur la rangée #%s redirigé vers %s/%s",
            row.id, target[0], target[1],
        )
        return (target[0], target[1]) + tuple(route[2:])

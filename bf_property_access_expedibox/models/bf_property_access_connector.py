"""Connecteur Expedibox : le casier reçoit les résidents que Symbifox lui dit.

Un ordre d'ajout inscrit le résident au casier (``POST /recipients/``), un
ordre de retrait l'en retire (``DELETE /recipients/{id}/``). Le rapprochement
se fait par ``custom_identifier``, propre à cette base, à la personne et au
point : le module ne touche jamais un destinataire qu'il n'a pas inscrit.

🔴 Le casier n'attend rien d'Odoo pour s'ouvrir : il valide seul ses
destinataires. Une panne retarde une inscription ou un retrait, elle ne bloque
jamais un colis.

🔴 Le module ne conclut JAMAIS qu'il n'y a personne à retirer. Un ordre de
retrait n'existe que pour un droit qui a pu être posé (voir ``_bf_revoke`` du
socle) : si l'inscription n'est pas repérable au casier, c'est qu'elle y a été
faite à la main, ou qu'elle s'est perdue — pas que la personne n'y est pas.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from . import expedibox_api as api_expedibox

_logger = logging.getLogger(__name__)

# Là où un numéro s'arrête : ce qui suit est un poste, pas le numéro.
EXTENSION_MARKS = ("poste", "ext", "#", ",", ";", "x")
# Les états d'un droit dont le destinataire doit être au casier (un retrait à
# faire en est un : il y est encore).
HELD_STATES = ("awaiting_consent", "to_issue", "issued", "to_remove")


class BfPropertyAccessConnector(models.Model):
    _inherit = "bf.property.access.connector"

    kind = fields.Selection(
        selection_add=[("expedibox", "Expedibox : boîte à colis")],
        ondelete={"expedibox": "set default"},
    )
    # ⚠️ Le jeton est celui de l'ORGANISATION cliente, pas celui de Blue Fox :
    # chaque organisation qui installe l'application Symbifox sur son casier
    # reçoit le sien (portail développeur d'Expedibox).
    # Réservé aux gestionnaires : la Consultation lit les connecteurs (le
    # concierge y voit le nom du casier), elle n'a rien à faire du jeton.
    expedibox_app_token = fields.Char(
        string="Jeton d'application (X-App-Token)",
        groups="bf_property_core.group_bf_property_manager",
        copy=False,
        help="Portail développeur d'Expedibox : l'application, le casier, "
             "« Installation details ». Un jeton par organisation.",
    )
    expedibox_organization_id = fields.Char(
        string="Identifiant d'organisation Expedibox",
        groups="bf_property_core.group_bf_property_manager",
        copy=False,
        help="Ce qu'Expedibox envoie dans l'en-tête X-Expedibox-Organization-Id "
             "de ses avis. Noté ici pour rapprocher un avis de dépôt de cette "
             "organisation ; le connecteur lui-même ne s'en sert pas.",
    )
    expedibox_contact = fields.Selection(
        [("email_first", "Courriel ; le mobile seulement s'il n'y a pas de courriel"),
         ("both", "Courriel et numéro de mobile")],
        string="Ce que le casier reçoit pour joindre le résident",
        default="email_first",
        help="Expedibox exige un moyen de joindre le résident pour l'aviser "
             "d'un colis. Par défaut, le logiciel n'en communique qu'un "
             "(art. 5 de la Loi sur la protection des renseignements "
             "personnels dans le secteur privé : le minimum nécessaire).",
    )

    @api.constrains("kind")
    def _check_expedibox_points(self):
        """🔴 Un connecteur manuel qu'on bascule vers Expedibox.

        La contrainte du point d'accès ne se rejoue pas : elle surveille les
        champs DU POINT, et c'est le connecteur qui a changé. Sans celle-ci,
        trois portiers restaient reliés à un connecteur de casier, sans
        identifiant de casier, et leurs ordres partaient quand même.
        """
        for connector in self:
            if connector.kind != "expedibox":
                continue
            points = self.env["bf.property.access.point"].sudo().with_context(
                active_test=False).search([("connector_id", "=", connector.id)])
            wrong = points.filtered(
                lambda p: p.point_type != "parcel" or not p.expedibox_locker_id)
            if wrong:
                raise ValidationError(_(
                    "Ces points d'accès sont reliés à « %(connector)s » et ne "
                    "peuvent pas l'être à un casier Expedibox (il leur faut le "
                    "type « Boîte à colis » et l'identifiant de leur casier) : "
                    "%(points)s.")
                    % {"connector": connector.name,
                       "points": ", ".join(wrong.mapped("display_name"))})

    # ── Envoi ────────────────────────────────────────────────────────────

    def _push_expedibox(self, order):
        self.ensure_one()
        reason = self._expedibox_blocking_reason(order)
        if reason:
            return self._expedibox_hand_over(order, reason)
        token = self.sudo().expedibox_app_token
        locker = order.point_id.expedibox_locker_id
        identifier = order.grant_id._expedibox_identifier()
        try:
            if order.action == "add":
                return self._expedibox_add(order, token, locker, identifier)
            return self._expedibox_remove(order, token, locker, identifier)
        except api_expedibox.ExpediboxError as exc:
            message = str(exc)
            if exc.permanent:
                # Jamais le corps de la réponse au journal : il peut nommer le
                # résident ou citer son courriel (il reste dans le message de
                # l'ordre, que la personne qui doit agir lit).
                _logger.warning("Ordre d'accès %s non envoyé : refus définitif "
                                "(%s %s)", order.id, order.action,
                                order.point_id.display_name)
                return self._expedibox_hand_over(order, message)
            # Panne, délai, 5xx : l'ordre se rejoue au prochain passage.
            return False, message, False

    def _expedibox_hand_over(self, order, message):
        """Rend l'ordre au responsable tout de suite : le rejouer ne changerait rien.

        Sauf quand l'essai en cours est le dernier : le socle le remet alors
        lui-même, et deux activités pour un seul ordre en laisseraient une
        orpheline, que personne ne fermerait.
        """
        if order.attempts + 1 < max(self.max_attempts, 1):
            order._bf_hand_over(message)
        return False, message, False

    def _expedibox_blocking_reason(self, order):
        """Ce qui empêche l'appel : configuration ou fiche de la personne."""
        self.ensure_one()
        if not self.sudo().expedibox_app_token:
            return _("Le connecteur « %s » n'a pas de jeton d'application "
                     "Expedibox : le casier ne peut pas être joint.") % self.name
        if not order.point_id.expedibox_locker_id:
            return _("Le point d'accès « %s » n'a pas d'identifiant de casier "
                     "Expedibox (bouton « Casiers du jeton » sur le connecteur).") \
                % order.point_id.display_name
        if order.grant_id.pass_id:
            # Un accès temporaire n'est pas un résident : le casier n'a pas de
            # plage de validité, et un visiteur inscrit resterait joignable
            # pour des colis. L'avis de vie privée ne couvre que les résidents.
            return _("Un accès temporaire ne s'inscrit pas au casier à colis : "
                     "faites-le à la main au casier si l'organisation le veut, "
                     "puis cochez cet ordre.")
        if order.action == "add":
            first, last = self._expedibox_names(order.grant_id)
            if not first or not last:
                return _("Expedibox exige un prénom et un nom : la fiche de "
                         "« %s » n'en donne pas.") % (order.grant_id.holder_label or "")
            if not self._expedibox_contact_values(order.grant_id):
                # Le bouton « Renvoyer à l'appareil » est réservé aux
                # gestionnaires : ne pas demander au concierge d'y toucher.
                return _("Expedibox exige un courriel ou un numéro de mobile pour "
                         "aviser %s d'un colis : la fiche n'en a aucun. Ajoutez-en "
                         "un à la fiche, puis demandez à un gestionnaire de "
                         "renvoyer l'ordre à l'appareil.") \
                    % (order.grant_id.holder_label or "")
        return ""

    def _expedibox_add(self, order, token, locker, identifier):
        # Rejouable : si le destinataire de ce droit est déjà au casier, ne
        # pas en créer un deuxième (ordre renvoyé, transaction rejetée après
        # l'appel, montée).
        payload = self._expedibox_payload(order.grant_id, locker, identifier)
        existing = api_expedibox.find_recipient(token, locker, identifier)
        if existing:
            ref = str(existing.get("id") or "")
            stale = {k: v for k, v in payload.items()
                     if k in ("first_name", "last_name", "unit")
                     and (existing.get(k) or "") != v}
            if stale:
                # Le nom ou le logement a changé depuis l'inscription : le
                # casier affiche le nom au livreur, il doit être juste.
                api_expedibox.patch_recipient(token, ref, stale)
                message = _("Déjà inscrit au casier (destinataire %(ref)s), "
                            "corrigé : %(champs)s.") % {
                                "ref": ref, "champs": ", ".join(sorted(stale))}
                return True, message, ref
            return True, _("Déjà inscrit au casier (destinataire %s).") % ref, ref
        ref = api_expedibox.create_recipient(token, payload)
        return True, _("Inscrit au casier (destinataire %s).") % ref, ref

    def _expedibox_remove(self, order, token, locker, identifier):
        grant = order.grant_id
        ref = (grant.external_ref or "").strip()
        if not ref:
            found = api_expedibox.find_recipient(token, locker, identifier)
            if not found:
                # 🔴 Ne jamais conclure « il n'y a personne ». Un ordre de
                # retrait n'existe que pour un droit qui a pu être posé : une
                # inscription faite à la main par le concierge ne porte pas
                # notre identifiant, et la clore ici laisserait la personne au
                # casier avec un journal qui dit le contraire.
                raise api_expedibox.ExpediboxError(
                    _("Aucune inscription de ce droit repérable au casier : elle "
                      "a pu y être faite à la main. Vérifiez au casier et "
                      "retirez-y la personne s'il y est inscrite."),
                    permanent=True,
                )
            ref = str(found.get("id") or "")
        recipient = api_expedibox.read_recipient(token, ref)
        if recipient is None:
            return True, _("Déjà retiré du casier (le destinataire %s n'y est "
                           "plus).") % ref, False
        # 🔴 Garde : la référence d'un droit s'écrit à la main. Supprimer un
        # destinataire qui n'est pas le nôtre retirerait l'accès de quelqu'un
        # d'autre au casier, et le journal dirait que le retrait est fait.
        if str(recipient.get("locker_id") or "") != str(locker):
            raise api_expedibox.ExpediboxError(
                _("Le destinataire %(ref)s est inscrit au casier %(autre)s, pas "
                  "au casier %(attendu)s de ce point : le module n'y touche pas. "
                  "Retrait à faire à la main sur le bon casier.")
                % {"ref": ref, "autre": recipient.get("locker_id") or "?",
                   "attendu": locker},
                permanent=True,
            )
        if (recipient.get("custom_identifier") or "") != identifier:
            raise api_expedibox.ExpediboxError(
                _("La référence %(ref)s ne désigne pas l'inscription de ce droit "
                  "(identifiant attendu : %(id)s) : le module n'y touche pas. "
                  "Retrait à faire à la main au casier.")
                % {"ref": ref, "id": identifier},
                permanent=True,
            )
        status = api_expedibox.delete_recipient(token, ref)
        if status in (404, 410):
            return True, _("Déjà retiré du casier (le destinataire %s n'y est "
                           "plus).") % ref, False
        return True, _("Retiré du casier (destinataire %s).") % ref, False

    # ── Ce qu'on envoie ──────────────────────────────────────────────────

    def _expedibox_payload(self, grant, locker, identifier):
        """Le destinataire, tel qu'Expedibox l'attend.

        Rien d'autre que le nécessaire : nom, logement, un moyen de joindre la
        personne, et notre identifiant de rapprochement. Aucun numéro fixe
        (un avis de colis sur une ligne fixe n'avise personne), aucune note,
        aucune date de bail.
        """
        first, last = self._expedibox_names(grant)
        payload = {
            "first_name": first,
            "last_name": last,
            # Expedibox exige un logement ; une chambre sans fraction inscrite
            # au dossier n'en a pas (maison de chambres).
            "unit": (grant.unit_id.name or "-")[:50],
            "locker_id": locker,
            "custom_identifier": identifier,
        }
        payload.update(self._expedibox_contact_values(grant))
        return payload

    def _expedibox_names(self, grant):
        partner = grant.partner_id
        name = " ".join((partner.name or "").split())
        parts = name.split(" ")
        # Expedibox refuse un nom vide (400). Une personne morale et un nom
        # d'un seul mot remplissent les deux champs : l'écran du casier montre
        # au livreur ce qui est écrit au dossier.
        if len(parts) >= 2 and not partner.is_company:
            return parts[0][:150], " ".join(parts[1:])[:150]
        return name[:150], name[:150]

    def _expedibox_contact_values(self, grant):
        """Le moyen de joindre la personne, et rien d'autre.

        🔴 Jamais `phone` : c'est le numéro général, donc la ligne fixe dans le
        cas courant, et un avis de colis par message texte sur une ligne fixe
        n'avise personne. L'avis remis au résident ne nomme que le courriel et
        le mobile : ce qui part doit être ce qui est déclaré.
        """
        self.ensure_one()
        partner = grant.partner_id
        email = (partner.email or "").strip()
        if "@" not in email or len(email) > 100:
            email = ""
        mobile = self._expedibox_number(
            partner.mobile if "mobile" in partner._fields else "")
        values = {}
        if email:
            values["email"] = email
        if mobile and (not email or self.expedibox_contact == "both"):
            values["mobile_number"] = mobile
        return values

    @api.model
    def _expedibox_number(self, value):
        """Le numéro en chiffres, ou rien s'il ne tient pas dans 15 caractères."""
        raw = (value or "").strip().lower()
        for mark in EXTENSION_MARKS:
            # Le poste ne fait pas partie du numéro : « 514 555-0142 poste 2 »
            # envoyé tel quel donnerait un numéro à onze chiffres.
            cut = raw.find(mark)
            if cut > 0:
                raw = raw[:cut]
        digits = "".join(c for c in raw if c.isdigit())
        number = ("+" + digits) if raw.lstrip().startswith("+") else digits
        if not 10 <= len(number) <= 15:
            return ""
        return number

    # ── Passage de nuit : le casier suivi, les orphelins signalés ────────

    @api.model
    def _expedibox_reconcile(self):
        """Confronte chaque casier à ce que la base dit, après la synchro.

        Deux écarts, deux gestes :

        * un nom ou un logement qui a changé depuis l'inscription est corrigé
          au casier (art. 28 LPRPSP : une correction suit le renseignement
          communiqué ; le casier affiche le nom au livreur) ;
        * un destinataire inscrit par CETTE base mais qu'aucun droit ne réclame
          est signalé au responsable du point. Jamais supprimé d'office : le
          module ne retire personne d'un casier sans qu'une personne l'ait vu.
        """
        Point = self.env["bf.property.access.point"].sudo()
        points = Point.search([("connector_id.kind", "=", "expedibox"),
                               ("connector_id.active", "=", True)])
        for locker, group in self._expedibox_by_locker(points).items():
            connector = group.connector_id[:1]
            token = connector.sudo().expedibox_app_token
            if not token or not locker:
                continue
            grants = self.env["bf.property.access.grant"].sudo().search(
                [("point_id", "in", group.ids), ("state", "in", HELD_STATES),
                 ("resident_id", "!=", False)])
            try:
                prefix = grants[:1]._expedibox_prefix() if grants \
                    else self.env["bf.property.access.grant"]._expedibox_prefix()
                ours = api_expedibox.find_ours(token, locker, prefix)
            except (api_expedibox.ExpediboxError, UserError) as exc:
                _logger.warning("Expedibox : casier %s non relu (%s)", locker, exc)
                continue
            expected = {g._expedibox_identifier(): g for g in grants}
            orphans = []
            for recipient in ours:
                grant = expected.get(recipient.get("custom_identifier") or "")
                if not grant:
                    orphans.append(str(recipient.get("id")))
                    continue
                self._expedibox_fix_drift(connector, token, locker, grant, recipient)
            if orphans:
                self._expedibox_report_orphans(group, locker, orphans)

    @api.model
    def _expedibox_by_locker(self, points):
        by_locker = {}
        for point in points:
            key = point.expedibox_locker_id or ""
            by_locker[key] = by_locker.get(key, point.browse()) | point
        return by_locker

    def _expedibox_fix_drift(self, connector, token, locker, grant, recipient):
        payload = connector._expedibox_payload(
            grant, locker, grant._expedibox_identifier())
        stale = {k: v for k, v in payload.items()
                 if k in ("first_name", "last_name", "unit")
                 and (recipient.get(k) or "") != v}
        if not stale:
            return
        try:
            api_expedibox.patch_recipient(token, str(recipient.get("id")), stale)
        except api_expedibox.ExpediboxError as exc:
            _logger.warning("Expedibox : destinataire %s non corrigé (%s)",
                            recipient.get("id"), exc)
            return
        grant.point_id.sudo().message_post(
            body=_("Casier %(locker)s : l'inscription de %(who)s a été corrigée "
                   "(%(champs)s).")
            % {"locker": locker, "who": grant.holder_label or "",
               "champs": ", ".join(sorted(stale))},
            subtype_xmlid="mail.mt_note",
        )

    @api.model
    def _expedibox_report_orphans(self, points, locker, orphans):
        """Un destinataire de cette base qu'aucun droit ne réclame.

        Arrive quand la transaction qui créait le droit a été rejetée après
        l'appel, ou quand le connecteur a été débranché du point. Il reste au
        casier : quelqu'un doit aller voir.
        """
        point = points[:1]
        Activity = self.env["mail.activity"].sudo()
        model = self.env["ir.model"]._get(point._name)
        # Le résumé sert aussi de repère : une seule activité ouverte à la fois
        # pour ce point, sinon le passage de nuit en empilerait une par nuit.
        # La recherche et la création le calculent dans le même appel, donc dans
        # la même langue.
        summary = _("Expedibox : destinataires sans droit")
        already = Activity.search_count([
            ("res_model_id", "=", model.id), ("res_id", "=", point.id),
            ("summary", "=", summary)])
        body = _("Casier %(locker)s : %(n)s destinataire(s) inscrit(s) par ce "
                 "logiciel ne correspondent à aucun droit en vigueur "
                 "(%(refs)s). Vérifiez au casier : s'il s'agit de personnes "
                 "parties, retirez-les. Le logiciel n'y touche pas de lui-même.") \
            % {"locker": locker, "n": len(orphans), "refs": ", ".join(orphans[:20])}
        point.sudo().message_post(body=body, subtype_xmlid="mail.mt_note")
        if already or not point.user_id:
            return
        Activity.create({
            "res_model_id": model.id,
            "res_id": point.id,
            "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
            "user_id": point.user_id.id,
            "date_deadline": fields.Date.context_today(point),
            "summary": summary,
            "note": body,
        })

    # ── Bouton ───────────────────────────────────────────────────────────

    def action_expedibox_lockers(self):
        """Les casiers que le jeton voit, avec leur identifiant à recopier au point."""
        self.ensure_one()
        if not self.env.user.has_group("bf_property_core.group_bf_property_manager"):
            raise AccessError(_("Réservé aux gestionnaires."))
        if self.kind != "expedibox":
            raise UserError(_("Ce connecteur n'est pas un connecteur Expedibox."))
        token = self.sudo().expedibox_app_token
        if not token:
            raise UserError(_("Ce connecteur n'a pas de jeton d'application."))
        try:
            lockers = api_expedibox.list_lockers(token)
        except api_expedibox.ExpediboxError as exc:
            raise UserError(_("Expedibox n'a pas répondu : %s") % exc) from exc
        if not lockers:
            message = _("Le jeton ne voit aucun casier.")
        else:
            # `_()` hors de la génératrice : dans son cadre, Odoo ne retrouve
            # pas la langue de l'environnement et laisse le terme en source.
            essai = _(" (casier d'essai)")
            message = "\n".join(
                "%s — %s%s" % (locker.get("id"), locker.get("name") or "",
                               essai if locker.get("test") else "")
                for locker in lockers)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": _("Casiers Expedibox"), "message": message,
                       "type": "info", "sticky": True},
        }

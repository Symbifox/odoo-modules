# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Plages retenues pour déplacer une rencontre.

Une plage retenue est un ``calendar.event`` ordinaire qui pointe vers la
rencontre qu'il remplacerait. Rien de plus : c'est ce qui la fait passer
partout où passe un événement (grille, Nextcloud, disponibilités de la prise
de rendez-vous) sans rien réécrire de ces chemins.
"""

import logging
from datetime import timedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Le glyphe qui dit « cette plage renvoie à une rencontre » dans un titre :
# la grille ne trace aucun trait entre deux blocs, le titre le porte.
FLECHE = "↪"


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    bf_hold_for_id = fields.Many2one(
        "calendar.event",
        string="Held for",
        index=True,
        copy=False,
        # Filet de sécurité seulement : unlink() libère les plages par l'ORM
        # avant, pour que leur suppression parte aussi vers Nextcloud.
        ondelete="cascade",
        help="The meeting this slot is held for. Choosing the slot moves "
             "that meeting here and releases its other held slots.",
    )
    bf_hold_ids = fields.One2many(
        "calendar.event", "bf_hold_for_id", string="Held slots",
        help="Candidate slots held to move this meeting.",
    )
    bf_hold_count = fields.Integer(
        string="Held slot count", compute="_compute_bf_hold_count")
    # Stocké, booléen : c'est la forme qu'un critère de règle bf_color sait
    # lire. Le Many2one lui-même donnerait une couleur par rencontre.
    bf_is_hold = fields.Boolean(
        string="Held slot", compute="_compute_bf_is_hold", store=True, index=True)

    @api.depends("bf_hold_ids")
    def _compute_bf_hold_count(self):
        for event in self:
            event.bf_hold_count = len(event.bf_hold_ids)

    @api.depends("bf_hold_for_id")
    def _compute_bf_is_hold(self):
        for event in self:
            event.bf_is_hold = bool(event.bf_hold_for_id)

    @api.constrains("bf_hold_for_id")
    def _check_bf_hold_for_id(self):
        for event in self.filtered("bf_hold_for_id"):
            if event.bf_hold_for_id == event:
                raise ValidationError(_("A slot cannot be held for itself."))
            if event.bf_hold_for_id.bf_hold_for_id:
                raise ValidationError(_("A slot cannot be held for another held slot."))
            if event.bf_hold_ids:
                raise ValidationError(_("A meeting with held slots cannot itself be a held slot."))
            if event.recurrency and not event.recurrence_id:
                raise ValidationError(_("A held slot cannot repeat."))

    # ------------------------------------------------------------------
    # Création : ce qui fait une plage, quoi que le formulaire envoie
    # ------------------------------------------------------------------

    @api.model
    def default_get(self, fields_list):
        """Les valeurs d'une plage, quand la grille est ouverte en mode « retenir ».

        ⚠️ Le mode tient en UNE clé, ``default_bf_hold_for_id``, et non en
        ``default_name``, ``default_user_id``… : ceux-ci s'appliquent à TOUT
        modèle créé dans l'action, et donneraient leur nom ou leur vendeur au
        contact qu'on créerait depuis la liste des participants. Le reste se
        calcule ici. Le préfixe ``default_`` est voulu : le client web retire
        ces clés des actions que rendent les boutons, et le mode ne suit plus
        l'usager hors de la grille.
        """
        res = super().default_get(fields_list)
        meeting_id = self.env.context.get("default_bf_hold_for_id")
        if not meeting_id:
            return res
        meeting = self.browse(meeting_id).exists()
        if not meeting:
            return res
        organizer = meeting.user_id or self.env.user
        valeurs = {
            "bf_hold_for_id": meeting.id,
            "name": "%s %s" % (FLECHE, meeting.name or ""),
            "user_id": organizer.id,
            "partner_ids": [Command.set(organizer.partner_id.ids)],
            "show_as": "busy",
            "bf_event_status": "tentative",
            "alarm_ids": [Command.clear()],
            "privacy": meeting.privacy,
        }
        if "x_nc_calendar_id" in self._fields and meeting.x_nc_calendar_id:
            valeurs["x_nc_calendar_id"] = meeting.x_nc_calendar_id.id
        res.update({k: v for k, v in valeurs.items() if k in fields_list})
        return res

    @api.model_create_multi
    def create(self, vals_list):
        plages = [bool(vals.get("bf_hold_for_id")) for vals in vals_list]
        if not any(plages):
            # 🔴 La clé du mode « retenir » ne vaut que pour une plage EXPLICITE.
            # Sans ce retrait, `default_get` la servait à toute création sous ce
            # contexte : une rencontre dupliquée en mode « retenir », invités
            # compris, devenait une plage de la rencontre visée, puis était
            # supprimée au « Choisir » suivant.
            return super(CalendarEvent, self.with_context(
                default_bf_hold_for_id=False)).create(vals_list)
        if all(plages):
            for vals in vals_list:
                self._bf_hold_prepare(vals)
            # Une plage n'invite personne : seul l'organisateur y figure, et le
            # cœur n'écrit jamais à l'usager courant. Le contexte couvre le cas
            # d'une assistante qui retient des plages pour la rencontre d'autrui.
            return super(CalendarEvent, self.with_context(no_mail_to_attendees=True)).create(vals_list)
        # Lot mêlé : un par un, dans l'ordre, chacun selon ce qu'il est.
        records = self.browse()
        for vals in vals_list:
            records |= self.create([vals])
        return records

    @api.model
    def _bf_hold_prepare(self, vals):
        """Ce qui fait une plage, imposé à la création."""
        meeting = self.browse(vals["bf_hold_for_id"]).exists()
        if not meeting:
            return
        # Retenir des plages pour une rencontre, c'est préparer son déplacement :
        # le geste de qui peut la modifier.
        meeting.check_access("write")
        organizer = meeting.user_id or self.env.user
        vals["user_id"] = organizer.id
        vals["partner_ids"] = [Command.set(organizer.partner_id.ids)]
        vals["alarm_ids"] = [Command.clear()]
        vals.setdefault("show_as", "busy")
        vals["bf_event_status"] = "tentative"
        vals["recurrency"] = False
        # 🔴 La plage porte le titre de la rencontre : elle en prend aussi la
        # confidentialité. Une rencontre privée lue « Occupé(e) » par un collègue
        # se lisait en clair sur sa plage, et partait dans Nextcloud sans CLASS.
        vals["privacy"] = meeting.privacy
        if not vals.get("name"):
            vals["name"] = "%s %s" % (FLECHE, meeting.name or "")
        for champ in ("bf_skip_agenda", "bf_skip_dashboard"):
            # bf_meeting, s'il est là : une plage n'est pas une rencontre à
            # préparer et n'a rien à faire au tableau de bord.
            if champ in self._fields:
                vals[champ] = True
        if "x_nc_calendar_id" in self._fields and meeting.x_nc_calendar_id \
                and not vals.get("x_nc_calendar_id"):
            vals["x_nc_calendar_id"] = meeting.x_nc_calendar_id.id
        # La plage a la durée de la rencontre : c'est exactement ce que
        # « Choisir » réservera. Un clic dans la grille, sans glisser, rendrait
        # sinon une case de trente minutes pour une rencontre d'une heure.
        if not vals.get("allday") and vals.get("start") and not meeting.allday:
            debut = fields.Datetime.to_datetime(vals["start"])
            vals["stop"] = debut + (meeting.stop - meeting.start)

    # ------------------------------------------------------------------
    # Retenir, choisir, libérer
    # ------------------------------------------------------------------

    def action_bf_hold_slots(self):
        """Ouvre la grille sur la semaine de la rencontre, en mode « retenir »."""
        self.ensure_one()
        if self.bf_hold_for_id:
            raise UserError(_("Slots are held for a meeting, not for another held slot."))
        self.check_access("write")
        action = self.env["ir.actions.act_window"]._for_xml_id("calendar.action_calendar_event")
        # Un contexte neuf, pas celui de l'appelant : des `default_*` qui y
        # traîneraient vaudraient pour chaque plage tracée.
        contexte = {
            "default_bf_hold_for_id": self.id,
            "initial_date": fields.Date.to_string(
                fields.Datetime.to_datetime(self.start).date()),
        }
        nom = _("Hold slots: %s", self.name)
        # Le fil d'Ariane d'Odoo 18 lit `display_name` : `name` seul laissait
        # « Réunions », et rien ne disait qu'on était en mode « retenir ».
        action.update({
            "name": nom,
            "display_name": nom,
            "context": contexte,
            "target": "current",
        })
        return action

    def action_bf_hold_list(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Held slots: %s", self.name),
            "res_model": "calendar.event",
            "view_mode": "list,form",
            "domain": [("bf_hold_for_id", "=", self.id)],
            "context": {"default_bf_hold_for_id": self.id},
        }

    def action_bf_hold_choose(self):
        """Déplace la rencontre sur cette plage et libère toutes ses plages."""
        self.ensure_one()
        meeting = self.bf_hold_for_id
        if not meeting:
            raise UserError(_("This event is not a held slot."))
        meeting.check_access("write")
        Event = self.env["calendar.event"]
        vals = meeting._bf_hold_move_vals(self)
        plages = meeting.bf_hold_ids
        if not Event._push_via_caldav_enabled():
            # Transport par webhook (repli) : l'ordre d'avant.
            meeting.with_context(bf_hold_choosing=True).write(vals)
            plages.exists()._bf_hold_release()
        else:
            # 🔴 Les plages quittent Odoo AVANT le déplacement : la prise de
            # rendez-vous voyait sinon la plage choisie elle-même, occupée par le
            # même organisateur, et refusait le déplacement d'une rencontre née
            # d'une réservation. Nextcloud n'est touché
            # qu'APRÈS : si le déplacement lève, tout s'annule dans Odoo et rien
            # n'est parti.
            #
            # Même détour pour un agenda Google : `skip_nc_sync` y coupe aussi
            # la suppression, qui part donc après le déplacement, à part
            # (supprimées dans Google AVANT, elles y étaient perdues quand le
            # déplacement échouait).
            cibles = plages._bf_hold_cibles_nextcloud()
            cibles_google = plages._bf_hold_cibles_google()
            plages.with_context(skip_nc_sync=True, skip_google_sync=True).unlink()
            meeting.with_context(bf_hold_choosing=True).write(vals)
            Event._bf_hold_supprimer_dans_nextcloud(cibles)
            Event._bf_hold_supprimer_dans_google(cibles_google)
        return {
            "type": "ir.actions.act_window",
            "res_model": "calendar.event",
            "res_id": meeting.id,
            "views": [[False, "form"]],
            "target": "current",
        }

    def action_bf_hold_release(self):
        holds = self.filtered("bf_hold_for_id")
        holds.check_access("unlink")
        holds._bf_hold_release()
        return True

    def _bf_hold_cibles_nextcloud(self):
        """``[(config, url, titre)]`` des plages à retirer de Nextcloud plus tard."""
        backend = self.env["calendar.caldav.backend"]
        cibles = []
        for plage in self:
            config = plage._bf_nc_config_pousse()
            if not config or not (plage.x_caldav_href or plage.x_nc_uid):
                continue
            url, _href = backend._resource_url(config, plage)
            if url:
                cibles.append((config, url, plage.name or ""))
            else:
                # Adresse refusée par la garde de calendar_nextcloud_sync : la
                # plage quitte Odoo mais reste dans Nextcloud, et l'usager le sait.
                self._bf_nc_avertir(plage.name or "")
        return cibles

    def _bf_hold_cibles_google(self):
        """``[(config, id Google)]`` des plages à retirer de Google plus tard."""
        cibles = []
        for plage in self:
            config = plage.x_nc_calendar_id.sudo()
            if config.backend_type == "google" and config.sync_direction != "nc_to_odoo" \
                    and plage.x_nc_uid:
                cibles.append((config, plage.x_nc_uid))
        return cibles

    @api.model
    def _bf_hold_supprimer_dans_google(self, cibles):
        backend = self.env["calendar.google.backend"]
        for config, google_id in cibles:
            try:
                backend.push_delete(config, google_id)
                config.update_sync_status("success", "Google delete: held slot")
            except Exception as e:  # noqa: BLE001 (journalisé, comme les autres poussées Google)
                _logger.error("Plage %s non retirée de Google : %s", google_id, e)
                config.update_sync_status("error", f"Google delete: {e}")

    @api.model
    def _bf_hold_supprimer_dans_nextcloud(self, cibles):
        backend = self.env["calendar.caldav.backend"]
        for config, url, titre in cibles:
            try:
                backend.push_delete(config, url=url)
            except Exception as e:  # noqa: BLE001 (dit à l'usager)
                _logger.error("Plage %s non retirée de Nextcloud : %s", url, e)
                config.update_sync_status("error", f"CalDAV delete: {e}")
                self._bf_nc_avertir(titre)

    def _bf_hold_move_vals(self, plage):
        """Les valeurs qui posent cette rencontre sur ``plage``, sa durée gardée."""
        self.ensure_one()
        if self.allday:
            jours = (self.stop_date - self.start_date).days
            debut = plage.start_date or fields.Datetime.to_datetime(plage.start).date()
            vals = {"start_date": debut, "stop_date": debut + timedelta(days=jours)}
        else:
            vals = {"start": plage.start, "stop": plage.start + (self.stop - self.start)}
        if self.recurrence_id:
            # Cette occurrence seulement : la série reste où elle est, et
            # calendar_nextcloud_sync en fait une exception RECURRENCE-ID.
            vals["recurrence_update"] = "self_only"
        return vals

    def _bf_hold_release(self):
        """Supprime des plages, en passant par l'ORM.

        ``skip_nc_sync`` est levé exprès : une libération peut arriver pendant
        une lecture Nextcloud qui recrée une série (contexte muet), et la
        plage, elle, doit quand même disparaître de Nextcloud.
        """
        holds = self.filtered("bf_hold_for_id").exists()
        if holds:
            holds.with_context(skip_nc_sync=False, skip_google_sync=False).unlink()

    def write(self, vals):
        if vals.get("bf_hold_for_id"):
            # La même garde qu'à la création : rattacher un événement à une
            # rencontre, c'est préparer son déplacement (seul
            # create() la vérifiait).
            self.browse(vals["bf_hold_for_id"]).exists().check_access("write")
        res = super().write(vals)
        if vals.get("bf_hold_for_id") and "privacy" not in vals:
            # Rattachée par écriture (série recréée par Nextcloud, événement
            # existant) : elle prend la confidentialité de sa rencontre, comme
            # à la création.
            for plage in self.sudo().filtered("bf_hold_for_id"):
                if plage.privacy != plage.bf_hold_for_id.privacy:
                    plage.with_context(skip_nc_sync=False).write(
                        {"privacy": plage.bf_hold_for_id.privacy})
        if "privacy" in vals and not self.env.context.get("bf_hold_choosing"):
            # La plage porte le titre de sa rencontre : elle en suit la
            # confidentialité même quand elle change après coup (CLASS relu de
            # Nextcloud, fiche modifiée), comme à la création. En sudo : les
            # plages sont à l'organisateur, une rencontre devenue privée les
            # masque (champ non public) à qui l'a rendue privée sans y être
            # invité, et seule la valeur que l'usager vient d'écrire y est recopiée.
            for rencontre in self.sudo().filtered("bf_hold_ids"):
                plages = rencontre.bf_hold_ids.filtered(
                    lambda h, r=rencontre: h.privacy != r.privacy)
                if plages:
                    # Le CLASS part vers Nextcloud même depuis une lecture
                    # (contexte muet), comme une libération.
                    plages.with_context(skip_nc_sync=False).write(
                        {"privacy": rencontre.privacy})
        if self.env.context.get("bf_hold_choosing"):
            return res
        rencontres = self.filtered("bf_hold_ids")
        if not rencontres:
            return res
        if vals.get("bf_event_status") == "cancelled" or vals.get("active") is False:
            # Rencontre annulée ou archivée : ses plages ne servent plus à rien.
            rencontres.filtered(
                lambda e: not e.active or e.bf_event_status == "cancelled"
            ).bf_hold_ids._bf_hold_release()
        elif {"start", "stop", "start_date", "stop_date"} & set(vals):
            rencontres._bf_hold_release_si_posee_dessus()
        return res

    def _bf_hold_release_si_posee_dessus(self):
        """Rencontre posée sur une de ses plages : c'est un choix, elles se libèrent.

        Glissée dans la grille, ou déplacée dans Nextcloud.
        """
        for rencontre in self:
            if any(abs((h.start - rencontre.start).total_seconds()) < 60
                   for h in rencontre.bf_hold_ids):
                rencontre.bf_hold_ids._bf_hold_release()

    def unlink(self):
        plages = self.bf_hold_ids - self
        if plages:
            plages._bf_hold_release()
        return super().unlink()

    # @api.model gardé : le relevé n8n de la synchro l'appelle en JSON-RPC sous la
    # forme « modèle » (`[données, config]`, sans ids), toutes les cinq minutes.
    @api.model
    def create_from_nextcloud(self, data, config_id):
        """Les plages d'une série restent rattachées quand la lecture la recrée.

        Une série changée dans Nextcloud est rasée puis recréée : ids neufs.
        Les plages de ses occurrences partaient avec elles, dans Odoo et dans
        Nextcloud, sans que rien ne le dise. Elles sont
        détachées avant, puis rattachées à l'occurrence qui a la même heure
        d'origine ; celles dont l'occurrence n'existe plus sont libérées.
        """
        gardees = {}
        if data.get("rrule") and data.get("uid"):
            porteur = self.search([
                ("x_nc_uid", "=", data["uid"]), ("x_nc_calendar_id", "=", config_id),
            ], limit=1)
            if porteur.recurrence_id:
                for occurrence in porteur.recurrence_id.with_context(
                        active_test=False).calendar_event_ids.filtered("bf_hold_ids"):
                    origine = occurrence._bf_nc_origine()
                    if origine:
                        gardees[origine] = occurrence.bf_hold_ids
                if gardees:
                    detachees = self.browse().union(*gardees.values())
                    detachees.with_context(skip_nc_sync=True).write({"bf_hold_for_id": False})
        res = super().create_from_nextcloud(data, config_id)
        if gardees:
            porteur = self.search([
                ("x_nc_uid", "=", data["uid"]), ("x_nc_calendar_id", "=", config_id),
            ], limit=1)
            occurrences = porteur.recurrence_id.calendar_event_ids
            rattachees = self.browse()
            for origine, plages in gardees.items():
                plages = plages.exists()
                cible = occurrences.filtered(
                    lambda e, o=origine: e._bf_nc_correspond(o, e.allday))[:1]
                if cible:
                    plages.with_context(skip_nc_sync=True).write({"bf_hold_for_id": cible.id})
                    rattachees |= cible
                else:
                    plages._bf_hold_release_detachees()
            # 🔴 Détachées pendant la lecture, les plages échappaient à la
            # libération de write() : une occurrence déplacée dans Nextcloud sur
            # sa plage gardait la plage par-dessus elle. C'est exactement le
            # geste du 2026-10-10.
            rattachees._bf_hold_release_si_posee_dessus()
        return res

    def _bf_hold_release_detachees(self):
        """Libère des plages déjà détachées de leur rencontre (série recréée sans elle)."""
        if self:
            self.with_context(skip_nc_sync=False, skip_google_sync=False).unlink()

    @api.model
    def _cron_bf_hold_cleanup(self):
        """Libère les plages dont l'heure est passée sans avoir été choisies.

        Pas celles d'une rencontre passée : retenir des plages pour reprendre
        une rencontre interrompue est l'un des usages relevés
        (« Réservé pour reprise de rencontre »).
        """
        echues = self.search([
            ("bf_hold_for_id", "!=", False),
            ("stop", "<", fields.Datetime.now()),
        ])
        echues._bf_hold_release()

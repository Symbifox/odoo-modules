"""Résident : une personne qui a droit d'entrer dans un immeuble.

🔴 **Le départ ne se déduit jamais d'une date.** Le bail se reconduit de
plein droit (art. 1941 C.c.Q.), la vente de l'immeuble ne le termine pas
(art. 1937), seul le tribunal expulse qui reste dans les lieux (art. 1889),
et couper l'accès pour pousser quelqu'un dehors peut être du harcèlement
(art. 1902). Quand le dossier cesse de désigner quelqu'un comme occupant,
le résident passe « départ à confirmer » avec une activité ; ses accès ne
sont retirés qu'une fois la date des clés remises saisie par une personne.

Le résident n'est pas le bail. Un conjoint, un enfant majeur ou un parent
qui n'a rien signé a droit au maintien dans les lieux (art. 1938) : on le
déclare à la main, et le départ du signataire ne touche pas ses accès.

Sources automatiques (`_bf_access_sources`) : le socle fournit les
copropriétaires qui occupent leur fraction et l'occupant d'une fraction
louée ; le pont locatif ajoute les signataires de bail. Une personne tirée
de plusieurs sources n'a qu'un résident par (personne, immeuble, fraction).
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import format_date

ORIGINS = [
    ("owner", "Copropriétaire occupant"),
    ("occupant", "Occupant de la fraction"),
    ("declared", "Occupant déclaré"),
    ("subtenant", "Sous-locataire"),
    ("other", "Autre"),
]

STATES = [
    ("upcoming", "À venir"),
    ("active", "Actif"),
    ("departure_pending", "Départ à confirmer"),
    ("left", "Parti"),
    ("cancelled", "Annulé (jamais arrivé)"),
]

_logger = logging.getLogger(__name__)

LIVE_STATES = ("upcoming", "active", "departure_pending")
# Champs qui ne s'écrivent que par le code du module (sudo) : l'état et ce qui
# le justifie. Une garde par clé de contexte se contourne par RPC (le client
# fixe le contexte) ; `env.su` ne se fixe pas depuis l'extérieur.
SOURCE_META = ("origin", "date_arrival", "date_known")
PROTECTED = {"state", "date_departure", "departure_signal",
             "departure_signal_date", "stale_source_noted"}
PRESENT_STATES = ("active", "departure_pending")


class BfPropertyAccessResident(models.Model):
    _name = "bf.property.access.resident"
    _description = "Résident (accès)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "building_id, unit_id, partner_id"
    _rec_names_search = ["partner_id.name", "unit_id.name", "building_id.name"]

    partner_id = fields.Many2one(
        "res.partner", string="Personne", required=True, index=True, tracking=True
    )
    building_id = fields.Many2one(
        "bf.property.building",
        string="Immeuble",
        required=True,
        ondelete="cascade",
        index=True,
        tracking=True,
    )
    unit_id = fields.Many2one(
        "bf.property.unit",
        string="Fraction",
        domain="[('building_id', '=', building_id)]",
        index=True,
        tracking=True,
        help="Vide pour une chambre ou un bail sans fraction inscrite : la "
             "personne n'a alors que les points communs de l'immeuble.",
    )
    organisation_id = fields.Many2one(
        related="building_id.organisation_id", store=True, string="Organisation"
    )
    company_id = fields.Many2one(
        related="building_id.company_id", store=True, string="Société"
    )
    origin = fields.Selection(
        ORIGINS, string="Origine", required=True, default="declared", tracking=True,
        help="D'où vient le droit. Les origines tirées du dossier (propriété, "
             "fraction, bail) sont suivies chaque jour ; un occupant déclaré "
             "ne l'est pas : son départ se déclare à la main.",
    )
    state = fields.Selection(
        STATES, string="État", required=True, default="upcoming",
        tracking=True, index=True, copy=False,
    )
    date_arrival = fields.Date(
        string="Arrivée", required=True, default=fields.Date.context_today,
        tracking=True,
    )
    date_departure = fields.Date(
        string="Clés remises le", tracking=True, copy=False,
        help="Date du départ confirmée par une personne. C'est elle, et non "
             "la fin du bail, qui retire les accès.",
    )
    departure_signal = fields.Text(
        string="Signal de départ", copy=False,
        help="Ce que le dossier dit, sans rien décider.",
    )
    departure_signal_date = fields.Date(string="Signalé le", copy=False)
    stale_source_noted = fields.Boolean(
        string="Dossier à mettre à jour", copy=False,
        help="Le départ est confirmé, mais le dossier désigne encore la "
             "personne comme occupante (p. ex. cotitulaire resté au bail). Les "
             "accès ne sont pas réémis : un retour se déclare à la main.",
    )
    grant_ids = fields.One2many(
        "bf.property.access.grant", "resident_id", string="Droits"
    )
    live_grant_count = fields.Integer(
        string="Droits en vigueur", compute="_compute_live_grant_count"
    )
    note = fields.Text(string="Note")

    def init(self):
        # Un seul résident vivant par (personne, immeuble, fraction). Index
        # partiel : les partis et les annulés restent au journal sans bloquer
        # un retour dans le même logement.
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS bf_property_access_resident_live_uniq
            ON bf_property_access_resident
               (partner_id, building_id, COALESCE(unit_id, 0))
            WHERE state IN ('upcoming', 'active', 'departure_pending')
            """
        )

    # ── Calculs et contraintes ───────────────────────────────────────────

    def _compute_live_grant_count(self):
        for res in self:
            res.live_grant_count = len(
                res.grant_ids.filtered(lambda g: g.state in ("to_issue", "issued", "awaiting_consent"))
            )

    @api.constrains("unit_id", "building_id")
    def _check_unit(self):
        for res in self:
            if res.unit_id and res.unit_id.building_id != res.building_id:
                raise ValidationError(
                    _("La fraction %(unit)s n'est pas dans l'immeuble %(building)s.")
                    % {"unit": res.unit_id.display_name,
                       "building": res.building_id.display_name}
                )

    @api.constrains("date_arrival", "date_departure")
    def _check_dates(self):
        for res in self:
            if res.date_departure and res.date_departure < res.date_arrival:
                raise ValidationError(
                    _("Les clés ne peuvent pas être remises avant l'arrivée.")
                )

    @api.depends("partner_id.name", "unit_id", "building_id.name")
    def _compute_display_name(self):
        for res in self:
            where = res.unit_id.display_name or res.building_id.name or ""
            res.display_name = f"{res.partner_id.name or ''} ({where})"

    # ── Création et écriture ─────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        today = fields.Date.context_today(self)
        for vals in vals_list:
            live = self.sudo().search_count([
                ("partner_id", "=", vals.get("partner_id")),
                ("building_id", "=", vals.get("building_id")),
                ("unit_id", "=", vals.get("unit_id") or False),
                ("state", "in", LIVE_STATES),
            ])
            if live:
                raise UserError(_("Cette personne est déjà résidente de ce logement."))
            if not self.env.su:
                for name in PROTECTED:
                    vals.pop(name, None)
                # À la main, on ne déclare qu'un occupant : les origines tirées
                # du dossier sont l'affaire de la synchronisation.
                if vals.get("origin") in self._bf_auto_origins():
                    vals["origin"] = "declared"
            if "state" not in vals:
                arrival = fields.Date.to_date(vals.get("date_arrival")) or today
                vals["state"] = "active" if arrival <= today else "upcoming"
        residents = super().create(vals_list)
        residents._bf_ensure_grants()
        return residents

    def write(self, vals):
        # L'état ne se change que par les gestes prévus : un write qui
        # passerait « parti » sans date retirerait des droits sans trace.
        if not self.env.su and (PROTECTED | {"origin"}) & set(vals):
            raise UserError(
                _("L'état d'un résident change par les boutons « Arrivée "
                  "maintenant », « Confirmer le départ » et « Il ne part pas ».")
            )
        if "partner_id" in vals or "building_id" in vals or "unit_id" in vals:
            if any(r.grant_ids for r in self):
                raise UserError(
                    _("Un résident qui a déjà des droits ne change ni de "
                      "personne ni de logement : confirmez son départ et "
                      "créez-en un autre.")
                )
        return super().write(vals)

    def _bf_set_state(self, vals):
        return self.sudo().write(vals)

    # ── Clés et sources ──────────────────────────────────────────────────

    def _bf_key(self):
        self.ensure_one()
        return (self.partner_id.id, self.building_id.id, self.unit_id.id or False)

    @api.model
    def _bf_auto_origins(self):
        """Origines tirées du dossier, suivies chaque jour."""
        return ("owner", "occupant")

    @api.model
    def _bf_access_sources(self, buildings=None):
        """Qui le dossier désigne comme occupant aujourd'hui (ou bientôt).

        Rend {(partner_id, building_id, unit_id ou False): {"origin": …,
        "date_arrival": date, …autres valeurs du résident}}. Les modules qui
        ajoutent une source appellent super() et complètent le dict par
        `_bf_add_source`, qui garde la date d'arrivée la plus tôt.
        """
        today = fields.Date.context_today(self)
        sources = {}
        unit_domain = [("unit_id.active", "=", True),
                       ("unit_id.unit_type", "=", "residential")]
        if buildings is not None:
            unit_domain.append(("unit_id.building_id", "in", buildings.ids))
        # Copropriétaire qui occupe sa fraction : une fraction non louée.
        ownerships = self.env["bf.property.ownership"].search(
            unit_domain + [
                ("unit_id.is_rented", "=", False),
                "|", ("date_end", "=", False), ("date_end", ">=", today),
            ]
        )
        for own in ownerships:
            unit = own.unit_id
            self._bf_add_source(
                sources, (own.partner_id.id, unit.building_id.id, unit.id),
                {"origin": "owner", "date_arrival": own.date_start or today,
                 "date_known": bool(own.date_start)},
            )
        # Occupant d'une fraction louée : aucune date au dossier.
        unit_dom = [("active", "=", True), ("unit_type", "=", "residential"),
                    ("is_rented", "=", True), ("occupant_id", "!=", False)]
        if buildings is not None:
            unit_dom.append(("building_id", "in", buildings.ids))
        for unit in self.env["bf.property.unit"].search(unit_dom):
            self._bf_add_source(
                sources, (unit.occupant_id.id, unit.building_id.id, unit.id),
                {"origin": "occupant", "date_arrival": today, "date_known": False},
            )
        return sources

    @api.model
    def _bf_add_source(self, sources, key, vals):
        """Ajoute une source ; la première origine gagne, la date la plus tôt aussi.

        Les autres clés (p. ex. `lease_id` du pont locatif) sont des
        identifiants de Many2one, recopiés sur le résident à chaque passage.
        """
        vals = dict(vals)
        vals.setdefault("date_known", True)
        known = sources.get(key)
        if known is None:
            sources[key] = vals
            return
        if vals["date_arrival"] < known["date_arrival"]:
            known["date_arrival"] = vals["date_arrival"]
        known["date_known"] = known["date_known"] or vals["date_known"]
        for name, value in vals.items():
            known.setdefault(name, value)

    def _bf_departure_reason(self):
        """Pourquoi le dossier ne désigne plus cette personne. Texte seul."""
        self.ensure_one()
        if self.origin == "owner":
            return _("La personne n'est plus copropriétaire de la fraction, ou "
                     "la fraction est maintenant louée.")
        if self.origin == "occupant":
            return _("La personne n'est plus l'occupant inscrit à la fraction, "
                     "ou la fraction n'est plus marquée louée.")
        return _("La personne n'apparaît plus au dossier comme occupante.")

    # ── Synchronisation ──────────────────────────────────────────────────

    @api.model
    def _cron_sync(self):
        """Un immeuble à la fois : une donnée incohérente n'arrête pas les autres."""
        Building = self.env["bf.property.building"].sudo()
        for building in Building.search([]):
            try:
                with self.env.cr.savepoint():
                    self.sudo()._bf_sync(buildings=building)
            except Exception:  # noqa: BLE001 — journaliser et passer au suivant
                _logger.exception("Accès : synchronisation de l'immeuble %s en échec",
                                  building.id)
        self.env["bf.property.access.order"].sudo()._cron_send()

    @api.model
    def _bf_sync(self, buildings=None):
        """Met les résidents d'accord avec le dossier, sans jamais retirer.

        Les signaux et les messages sont rédigés dans la langue de la société de
        l'immeuble : lancé par le cron, le texte prendrait sinon la langue du
        compte système, et ses dates s'afficheraient au format ISO.
        """
        company = buildings[:1].company_id if buildings else self.env.company
        lang = company.partner_id.lang or self.env.lang
        if lang and lang != self.env.lang:
            self = self.with_context(lang=lang)
        today = fields.Date.context_today(self)
        sources = self._bf_access_sources(buildings)
        domain = [("state", "in", LIVE_STATES)]
        if buildings is not None:
            domain.append(("building_id", "in", buildings.ids))
        live = self.search(domain)
        by_key = {res._bf_key(): res for res in live}
        created = self.browse()
        gone = {}
        for res in self.search(
            [("state", "=", "left")] + domain[1:], order="date_departure desc, id desc"
        ):
            gone.setdefault(res._bf_key(), res)

        for key, src in sources.items():
            res = by_key.get(key)
            if res:
                if res.state == "upcoming" and src["date_arrival"] != res.date_arrival:
                    res.date_arrival = src["date_arrival"]
                extra = {name: value for name, value in src.items()
                         if name not in SOURCE_META and res[name].id != value}
                if extra:
                    res.write(extra)
                if res.state == "departure_pending" and res.origin in self._bf_auto_origins():
                    res._bf_clear_signal(_("Le dossier désigne de nouveau cette "
                                           "personne comme occupante."))
                continue
            former = gone.get(key)
            # Une source sans date réelle (registre, propriété sans date)
            # ne prouve aucun retour : l'ancien départ confirmé suffit.
            if former and (not src["date_known"]
                           or former.date_departure >= src["date_arrival"]):
                # Départ confirmé par une personne, que le dossier n'a pas
                # suivi (cotitulaire resté au bail…) : ne rien réémettre.
                former._bf_note_stale_source()
                continue
            partner_id, building_id, unit_id = key
            vals = {name: value for name, value in src.items() if name != "date_known"}
            vals.update(partner_id=partner_id, building_id=building_id, unit_id=unit_id)
            created |= self.create(vals)

        auto = self._bf_auto_origins()
        for res in live:
            if res.origin not in auto or res._bf_key() in sources:
                continue
            if res.state == "upcoming":
                res._bf_set_state({"state": "cancelled"})
                res.message_post(body=_("Annulé avant l'arrivée : %s")
                                 % res._bf_departure_reason())
            elif res.state == "active":
                res._bf_flag_departure(res._bf_departure_reason())

        due = self.search(domain + [("state", "=", "upcoming"),
                                    ("date_arrival", "<=", today)])
        due._bf_arrive()
        # Un point ajouté pendant que personne ne regardait : rattraper.
        self.search(domain + [("state", "in", PRESENT_STATES)])._bf_ensure_grants()
        return created

    def _bf_note_stale_source(self):
        """Une seule fois : activité « mettre le dossier à jour »."""
        for res in self.filtered(lambda r: not r.stale_source_noted):
            res._bf_set_state({"stale_source_noted": True})
            why = _("Le départ de %(who)s est confirmé (clés remises le %(date)s), "
                    "mais le dossier la désigne encore comme occupante. Ses accès "
                    "ne seront pas réémis. Mettez le dossier à jour (bail, "
                    "registre), ou déclarez un retour à la main.") % {
                "who": res.partner_id.name,
                "date": format_date(self.env, res.date_departure)}
            res.message_post(body=why)
            user = res._bf_departure_user()
            if user:
                res.with_context(mail_activity_quick_update=True).activity_schedule(
                    "mail.mail_activity_data_todo", user_id=user.id,
                    summary=_("Mettre le dossier à jour : %s") % res.partner_id.name,
                    note=why,
                )

    def _bf_arrive(self):
        for res in self.filtered(lambda r: r.state == "upcoming"):
            res._bf_set_state({"state": "active"})
            res.message_post(body=_("Arrivée : accès à émettre."))
        self._bf_ensure_grants()

    def _bf_ensure_grants(self, points=None):
        # Les droits naissent du code, pas de la main de l'usager : le geste
        # qui les déclenche (création d'un résident ou d'un point, arrivée,
        # cron) a déjà passé ses propres contrôles.
        Grant = self.env["bf.property.access.grant"].sudo()
        present = self.filtered(lambda r: r.state in PRESENT_STATES)
        if points is None:
            all_points = self.env["bf.property.access.point"].sudo().search(
                [("building_id", "in", present.building_id.ids)]
            )
        for res in present:
            candidates = points if points is not None else all_points.filtered(
                lambda p, r=res: p.building_id == r.building_id
            )
            serving = candidates.filtered(lambda p, r=res: p._bf_serves(r))
            held = res.grant_ids.filtered(
                lambda g: g.state in ("awaiting_consent", "to_issue", "issued")
            ).point_id
            for point in serving - held:
                Grant.create({"resident_id": res.id, "point_id": point.id})

    def _bf_flag_departure(self, reason):
        today = fields.Date.context_today(self)
        for res in self:
            res._bf_set_state({
                "state": "departure_pending",
                "departure_signal": reason,
                "departure_signal_date": today,
            })
            res.message_post(body=_(
                "Départ à confirmer : %s Les accès restent en place tant que "
                "la date des clés remises n'est pas saisie."
            ) % reason)
            user = res._bf_departure_user()
            if user:
                res.with_context(mail_activity_quick_update=True).activity_schedule(
                    "bf_property_access.mail_activity_departure",
                    user_id=user.id,
                    summary=_("Confirmer le départ de %s") % res.partner_id.name,
                    note=reason,
                )

    def _bf_departure_user(self):
        """Quelqu'un qui peut confirmer : le responsable des accès de l'immeuble,
        sinon le premier responsable de point qui est gestionnaire. Personne
        d'autre : un concierge qui fermerait l'activité effacerait le seul rappel."""
        self.ensure_one()
        if self.building_id.access_user_id:
            return self.building_id.access_user_id
        points = self.env["bf.property.access.point"].sudo().search(
            [("building_id", "=", self.building_id.id)]
        )
        return points.user_id.filtered(
            lambda u: u.has_group("bf_property_core.group_bf_property_manager")
        )[:1]

    def _bf_clear_signal(self, why):
        for res in self:
            res._bf_set_state({
                "state": "active",
                "departure_signal": False,
                "departure_signal_date": False,
            })
            res.activity_unlink(["bf_property_access.mail_activity_departure"])
            res.message_post(body=_("Signal de départ levé : %s") % why)

    def _bf_confirm_departure(self, date_keys):
        today = fields.Date.context_today(self)
        if date_keys > today:
            raise UserError(_("La date des clés remises ne peut pas être future : "
                              "on confirme un départ, on ne le prévoit pas."))
        for res in self:
            if res.state not in PRESENT_STATES:
                raise UserError(_("%s n'est pas présent : rien à confirmer.")
                                % res.display_name)
            res._bf_set_state({"state": "left", "date_departure": date_keys})
            res.grant_ids._bf_revoke("departure")
            res.activity_unlink(["bf_property_access.mail_activity_departure"])
            res.message_post(body=_("Départ confirmé, clés remises le %s : "
                                    "accès à retirer.")
                             % format_date(self.env, date_keys))

    # ── Boutons ──────────────────────────────────────────────────────────

    def _bf_check_manager(self):
        if not self.env.user.has_group("bf_property_core.group_bf_property_manager"):
            raise AccessError(_("Réservé aux gestionnaires."))

    def action_cancel_arrival(self):
        """Une arrivée prévue qui n'aura pas lieu (occupant déclaré à l'avance)."""
        self._bf_check_manager()
        for res in self:
            if res.state != "upcoming":
                raise UserError(_("%s n'est pas à venir.") % res.display_name)
            if res.origin in self._bf_auto_origins():
                raise UserError(_("%s vient du dossier (bail, propriété) : c'est le "
                                  "dossier qu'il faut corriger, la synchronisation "
                                  "annulera l'arrivée.") % res.display_name)
            res._bf_set_state({"state": "cancelled"})
            res.message_post(body=_("Arrivée annulée par %s.") % self.env.user.name)

    def action_arrive_now(self):
        self._bf_check_manager()
        today = fields.Date.context_today(self)
        for res in self:
            if res.state != "upcoming":
                raise UserError(_("%s est déjà arrivé.") % res.display_name)
            res.date_arrival = today
        self._bf_arrive()

    def action_confirm_departure(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("Confirmer le départ"),
            "res_model": "bf.property.access.departure",
            "view_mode": "form",
            "target": "new",
            "context": {"default_resident_ids": [(6, 0, self.ids)]},
        }

    def action_dismiss_departure(self):
        """Il ne part pas : le dossier se trompe, une personne s'en porte garante.

        Le résident devient « occupant déclaré » : sans cela, le cron du
        lendemain relèverait le même signal et l'activité reviendrait chaque
        jour (p. ex. un copropriétaire qui a vendu et reste comme locataire).
        """
        self._bf_check_manager()
        for res in self:
            if res.state != "departure_pending":
                raise UserError(_("%s n'a pas de départ à confirmer.")
                                % res.display_name)
            res.sudo().origin = "declared"
            res._bf_clear_signal(_(
                "%s confirme que la personne habite toujours les lieux. Elle "
                "devient occupante déclarée : son départ se déclarera à la main."
            ) % self.env.user.name)

    def action_view_grants(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Droits : %s") % self.display_name,
            "res_model": "bf.property.access.grant",
            "view_mode": "list,form",
            "domain": [("resident_id", "=", self.id)],
        }

    @api.model
    def action_sync_now(self):
        """Bouton « Synchroniser » : seulement les immeubles que l'usager voit."""
        self.check_access("write")
        buildings = self.env["bf.property.building"].search([])
        self.sudo()._bf_sync(buildings=buildings.sudo())
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "message": _("Résidents mis à jour d'après le dossier."),
                "type": "success",
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    # ── Purge (Loi 25, art. 23) ──────────────────────────────────────────

    @api.model
    def _cron_purge(self):
        """Supprime le journal échu, par l'ORM (messages et activités compris).

        Jamais ce qui reste à faire : un résident parti dont un droit n'est
        pas encore retiré, ou dont un ordre est ouvert, attend son retrait,
        quelle que soit la date des clés (qui peut être antidatée).
        """
        today = fields.Date.context_today(self)
        Order = self.env["bf.property.access.order"].sudo()
        Grant = self.env["bf.property.access.grant"].sudo()
        closed_grant = ("removed", "cancelled")
        closed_order = ("done", "cancelled")
        for org in self.env["bf.property.organisation"].sudo().search(
            [("access_log_retention_days", ">", 0)]
        ):
            limit = fields.Date.subtract(today, days=org.access_log_retention_days)
            limit_dt = fields.Datetime.to_datetime(limit)
            old = self.sudo().search([
                ("organisation_id", "=", org.id),
                ("state", "in", ("left", "cancelled")),
                "|", ("date_departure", "<", limit),
                "&", ("date_departure", "=", False), ("write_date", "<", limit_dt),
            ]).filtered(lambda r: all(g.state in closed_grant for g in r.grant_ids)
                        and all(o.state in closed_order for o in r.grant_ids.order_ids))
            # Un départ confirmé que le dossier n'a pas suivi : sa fiche est la
            # garde qui empêche la réémission. On la garde tant que le dossier
            # désigne encore la personne.
            guarded = old.filtered("stale_source_noted")
            if guarded:
                still = set(self.sudo()._bf_access_sources(
                    buildings=guarded.building_id).keys())
                old -= guarded.filtered(lambda r: r._bf_key() in still)
            old.grant_ids.order_ids.unlink()
            old.grant_ids.unlink()
            old.unlink()
            passes = self.env["bf.property.access.pass"].sudo().search([
                ("organisation_id", "=", org.id),
                ("state", "in", ("draft", "ended", "cancelled")),
                ("date_to", "<", limit_dt),
            ]).filtered(lambda t: all(g.state in closed_grant for g in t.grant_ids)
                        and all(o.state in closed_order for o in t.grant_ids.order_ids))
            passes.grant_ids.order_ids.unlink()
            passes.grant_ids.unlink()
            passes.unlink()
            grants = Grant.search([
                ("organisation_id", "=", org.id),
                ("state", "in", closed_grant),
                ("write_date", "<", limit_dt),
            ]).filtered(lambda g: all(o.state in closed_order for o in g.order_ids))
            grants.order_ids.unlink()
            grants.unlink()
            Order.search([
                ("grant_id.organisation_id", "=", org.id),
                ("state", "in", closed_order),
                ("write_date", "<", limit_dt),
            ]).unlink()

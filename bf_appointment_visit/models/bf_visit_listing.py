"""L'inscription à visiter, et les objets de réservation qu'elle fabrique.

Une inscription est une adresse qu'on fait visiter pendant un moment de sa vie :
une maison à vendre, un logement à relouer. Elle porte ce qu'un humain sait
dire (qui vend, qui montre, quand c'est possible, qui habite là) et fabrique ce
que le moteur de rendez-vous demande (un type, un calendrier, une ressource, des
combinaisons).

Le partage des rôles, qui est le cœur du module :

* le **calendrier de la ressource** porte les plages que le vendeur offre;
* le **calendrier du type** porte ce que l'agence ou la loi impose, et rien
  d'autre. 🔴 Il doit être posé EXPLICITEMENT : un type de rendez-vous naît avec
  le calendrier de l'entreprise, du lundi au vendredi, et des plages de fin de
  semaine rendent alors zéro créneau, sans un mot. Mesuré au banc le 2026-09-11;
* chaque **courtier autorisé** entre dans sa propre combinaison, avec la
  propriété. Une combinaison par courtier, jamais une combinaison à trois avec
  un plafond : le K-de-N accepte n'importe quel sous-ensemble, donc {courtier A,
  courtier B} passerait SANS la propriété, et les plages du vendeur seraient
  ignorées. Mesuré au banc le même jour.
"""

import logging
from datetime import date, datetime, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.base.models.res_partner import _tz_get

_logger = logging.getLogger(__name__)

# Bornes de l'art. 1931 du Code civil : un logement occupé se visite entre 9 h
# et 21 h, sur préavis de 24 heures. C'est d'ordre public, donc ni le locateur
# ni le locataire ne peuvent y renoncer d'avance.
OCCUPIED_HOUR_FROM = 9.0
OCCUPIED_HOUR_TO = 21.0
OCCUPIED_NOTICE_HOURS = 24.0


class BfVisitListing(models.Model):
    _name = "bf.visit.listing"
    _description = "Inscription à visiter"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "state, name"

    name = fields.Char(
        string="Adresse", required=True, tracking=True, index=True,
        help="Ce que le visiteur lira en tête de la page de réservation.",
    )
    reference = fields.Char(
        string="Numéro d'inscription",
        help="Le numéro de la fiche au système de votre chambre, s'il y en a un.",
    )
    active = fields.Boolean(default=True)
    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("published", "En ligne"),
            ("suspended", "Suspendue"),
            ("closed", "Terminée"),
        ],
        default="draft", required=True, tracking=True, string="État",
    )
    listing_kind = fields.Selection(
        [("sale", "À vendre"), ("rental", "À louer")],
        default="sale", required=True, string="Nature", tracking=True,
    )
    occupancy = fields.Selection(
        [
            ("vacant", "Inoccupée"),
            ("owner", "Occupée par le propriétaire"),
            ("tenant", "Occupée par un locataire"),
        ],
        default="owner", required=True, string="Occupation", tracking=True,
        help="« Occupée par un locataire » impose le préavis de 24 heures et "
             "les heures de 9 h à 21 h de l'article 1931 du Code civil.",
    )
    company_id = fields.Many2one(
        "res.company", string="Société", required=True,
        default=lambda self: self.env.company,
    )

    # --- Les gens -----------------------------------------------------------
    seller_ids = fields.Many2many(
        "res.partner", "bf_visit_listing_seller_rel", "listing_id", "partner_id",
        string="Vendeurs",
        help="Qui reçoit les demandes de visite quand l'inscription est en "
             "mode « sur approbation », et le compte rendu des visites.",
    )
    broker_ids = fields.Many2many(
        "res.users", "bf_visit_listing_broker_rel", "listing_id", "user_id",
        string="Courtiers autorisés",
        help="Les personnes qui peuvent faire visiter. Un créneau n'est offert "
             "que si l'une d'elles est libre en même temps que la propriété.",
    )
    tenant_id = fields.Many2one(
        "res.partner", string="Locataire",
        help="Qui reçoit l'avis de visite de 24 heures.",
    )
    tenant_wants_presence = fields.Boolean(
        string="Le locataire demande la présence du locateur",
        help="L'article 1931 lui en donne le droit. Coché, la mention part "
             "avec chaque avis de visite.",
    )

    # --- L'adresse ----------------------------------------------------------
    street = fields.Char(string="Rue")
    street2 = fields.Char(string="Complément")
    city = fields.Char(string="Ville")
    state_id = fields.Many2one("res.country.state", string="Province")
    zip = fields.Char(string="Code postal")
    country_id = fields.Many2one(
        "res.country", string="Pays",
        default=lambda self: self.env.company.country_id,
    )
    address_display = fields.Char(
        string="Adresse complète", compute="_compute_address_display", store=True,
    )

    # --- Les règles de visite ----------------------------------------------
    visit_duration = fields.Float(
        string="Durée d'une visite (heures)", default=0.5, required=True,
    )
    slot_duration = fields.Float(
        string="Intervalle entre les créneaux (heures)", default=0.5, required=True,
    )
    lead_time_hours = fields.Float(
        string="Préavis minimum (heures)", default=2.0,
        help="Délai entre la réservation et la visite. Porté à 24 heures d'office "
             "pour un logement occupé par un locataire.",
    )
    approval_mode = fields.Selection(
        [
            ("auto", "Confirmation automatique"),
            ("seller", "Sur approbation du vendeur"),
            ("broker", "Sur approbation du courtier"),
        ],
        default="seller", required=True, string="Mode de confirmation",
        tracking=True,
        help="La confirmation automatique convient à une propriété inoccupée. "
             "Dès qu'il y a quelqu'un derrière la porte, l'approbation est la "
             "règle du métier.",
    )
    open_house = fields.Boolean(
        string="Visite libre", tracking=True,
        help="Plusieurs personnes sur le même créneau, avec des arrivées "
             "échelonnées. ⚠️ Un courtier doit être présent pendant toute la "
             "visite libre, ou s'y faire remplacer.",
    )
    slot_capacity = fields.Integer(
        string="Personnes par créneau", default=1,
        help="N'a d'effet qu'en visite libre.",
    )
    require_identity = fields.Boolean(
        string="Vérifier l'identité des visiteurs", default=True,
        help="L'OACIQ le demande pour la sécurité et la protection de la "
             "propriété. On consigne le type de pièce vue, jamais une copie.",
    )
    require_representation = fields.Boolean(
        string="Demander si la personne est représentée", default=True,
        help="Le courtier du vendeur doit poser la question à la première "
             "occasion. La réponse et son heure restent au registre : c'est la "
             "pièce qui compte dans un litige sur la cause efficiente.",
    )
    showing_instructions = fields.Html(
        string="Consignes de visite",
        help="Ce que le visiteur lit avant de venir : chaussures, animaux, "
             "stationnement, ce qu'il ne faut pas ouvrir.",
    )
    access_instructions = fields.Html(
        string="Accès à la propriété",
        help="⚠️ Livré SEULEMENT quand la visite est confirmée, et jamais sur "
             "la page publique. Code de boîte à clés, porte à utiliser, "
             "système d'alarme.",
    )

    # --- Ce que le module fabrique -----------------------------------------
    booking_type_id = fields.Many2one(
        "resource.booking.type", string="Type de rendez-vous",
        readonly=True, copy=False, ondelete="set null",
    )
    resource_id = fields.Many2one(
        "resource.resource", string="Ressource « propriété »",
        readonly=True, copy=False, ondelete="set null",
    )
    calendar_id = fields.Many2one(
        "resource.calendar", string="Calendrier des plages offertes",
        readonly=True, copy=False, ondelete="set null",
    )
    window_ids = fields.One2many(
        "bf.visit.window", "listing_id", string="Plages offertes",
    )
    visit_ids = fields.One2many("bf.visit", "listing_id", string="Visites")
    visit_count = fields.Integer(
        compute="_compute_visit_count", string="Nombre de visites",
    )
    public_url = fields.Char(
        related="booking_type_id.public_url", string="Page de réservation",
        readonly=True,
    )
    walkin_token = fields.Char(
        string="Jeton de la feuille d'inscription", copy=False, readonly=True,
        groups="bf_appointment_visit.group_bf_visit_user",
    )
    tz = fields.Selection(
        _tz_get, string="Fuseau horaire", required=True,
        default=lambda self: self.env.user.tz or "America/Toronto",
        help="Le fuseau dans lequel se lisent les plages offertes. 🔴 Il ne se "
             "déduit PAS du calendrier de la société : sur une base neuve, "
             "celui-ci est en UTC, et « samedi 13 h » devenait alors 9 h du "
             "matin pour le visiteur. Mesuré au banc le 2026-09-11.",
    )
    tz_name = fields.Char(
        string="Fuseau appliqué", compute="_compute_tz_name",
        help="Ce que le module utilise vraiment, repli compris.",
    )
    note = fields.Html(string="Notes internes")

    _sql_constraints = [
        (
            "visit_duration_positive",
            "CHECK(visit_duration > 0)",
            "La durée d'une visite doit être supérieure à zéro.",
        ),
    ]

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------
    @api.depends("name", "street", "street2", "city", "state_id", "zip")
    def _compute_address_display(self):
        for rec in self:
            morceaux = [rec.street, rec.street2, rec.city]
            if rec.state_id:
                morceaux.append(rec.state_id.name)
            morceaux.append(rec.zip)
            adresse = ", ".join(m.strip() for m in morceaux if m and m.strip())
            rec.address_display = adresse or rec.name or ""

    @api.depends("tz")
    def _compute_tz_name(self):
        for rec in self:
            rec.tz_name = rec.tz or self.env.user.tz or "America/Toronto"

    @api.depends("visit_ids")
    def _compute_visit_count(self):
        data = dict(
            self.env["bf.visit"]._read_group(
                [("listing_id", "in", self.ids)], ["listing_id"], ["__count"]
            )
        )
        for rec in self:
            rec.visit_count = data.get(rec, 0)

    # ------------------------------------------------------------------
    # Contraintes
    # ------------------------------------------------------------------
    @api.constrains("occupancy", "tenant_id", "state")
    def _check_tenant(self):
        for rec in self:
            if rec.state == "published" and rec.occupancy == "tenant" and not rec.tenant_id:
                raise ValidationError(
                    _(
                        "« %s » est occupée par un locataire : il faut dire QUI, "
                        "sinon l'avis de 24 heures que la loi exige ne part "
                        "nulle part."
                    )
                    % rec.name
                )

    @api.constrains("slot_capacity", "open_house")
    def _check_capacity(self):
        for rec in self:
            if rec.open_house and rec.slot_capacity < 2:
                raise ValidationError(
                    _("Une visite libre accueille au moins 2 personnes par créneau.")
                )

    @api.onchange("occupancy")
    def _onchange_occupancy(self):
        """Le préavis de 24 heures se propose tout seul, et ne se descend pas."""
        if self.occupancy == "tenant" and self.lead_time_hours < OCCUPIED_NOTICE_HOURS:
            self.lead_time_hours = OCCUPIED_NOTICE_HOURS

    @api.onchange("open_house")
    def _onchange_open_house(self):
        if self.open_house and self.slot_capacity < 2:
            self.slot_capacity = 5
        elif not self.open_house:
            self.slot_capacity = 1

    # ------------------------------------------------------------------
    # Le préavis réellement appliqué
    # ------------------------------------------------------------------
    def _effective_lead_time(self):
        """Le préavis retenu, plancher légal compris.

        Un logement occupé par un locataire ne se visite pas sur moins de 24
        heures d'avis. Le champ reste modifiable vers le HAUT, jamais vers le
        bas : c'est d'ordre public.
        """
        self.ensure_one()
        if self.occupancy == "tenant":
            return max(self.lead_time_hours or 0.0, OCCUPIED_NOTICE_HOURS)
        return self.lead_time_hours or 0.0

    # ------------------------------------------------------------------
    # Fabrication des objets de réservation
    # ------------------------------------------------------------------
    def _frame_calendar(self):
        """Le calendrier du TYPE : ce que l'agence et la loi imposent.

        🔴 Ne JAMAIS laisser le défaut. Un type naît avec le calendrier de
        l'entreprise (lundi au vendredi, heures de bureau) et avale alors
        silencieusement toutes les plages de fin de semaine, qui sont la moitié
        du métier. Mesuré au banc le 2026-09-11 : zéro créneau rendu.
        """
        self.ensure_one()
        occupe = self.occupancy == "tenant"
        tz = self.tz_name
        # Le fuseau entre dans le NOM : deux inscriptions dans deux fuseaux ne
        # peuvent pas partager un calendrier, et un calendrier partagé qu'on
        # réécrirait au passage déplacerait les plages de l'autre.
        nom = "%s [%s]" % (
            _("Visites — logement occupé (9 h à 21 h)")
            if occupe
            else _("Visites — sans restriction d'heure"),
            tz,
        )
        Calendar = self.env["resource.calendar"].sudo()
        cal = Calendar.search(
            [("name", "=", nom), ("company_id", "=", self.company_id.id)], limit=1
        )
        if cal:
            return cal
        depart = OCCUPIED_HOUR_FROM if occupe else 0.0
        fin = OCCUPIED_HOUR_TO if occupe else 23.98
        lignes = []
        for jour in range(7):
            lignes.append(
                (0, 0, {
                    "name": nom,
                    "dayofweek": str(jour),
                    "hour_from": depart,
                    "hour_to": fin,
                    "day_period": "morning" if depart < 12 else "afternoon",
                })
            )
        return Calendar.create({
            "name": nom,
            "company_id": self.company_id.id,
            "tz": tz,
            "two_weeks_calendar": False,
            "attendance_ids": [(5, 0, 0)] + lignes,
        })

    def _resource_for_user(self, user):
        """La ressource « personne » d'un courtier, trouvée ou fabriquée.

        Odoo ne crée pas de `resource.resource` pour un utilisateur tant que
        rien ne le demande. On réutilise celle qui existe (le module RH en pose
        une par employé) plutôt que d'en empiler une deuxième : deux ressources
        pour la même personne, ce sont deux agendas qui ne se voient pas.
        """
        self.ensure_one()
        Resource = self.env["resource.resource"].sudo()
        existante = Resource.search(
            [
                ("user_id", "=", user.id),
                ("resource_type", "=", "user"),
                ("company_id", "in", [self.company_id.id, False]),
            ],
            limit=1,
        )
        if existante:
            return existante
        return Resource.create({
            "name": user.name,
            "resource_type": "user",
            "user_id": user.id,
            "company_id": self.company_id.id,
            "tz": user.tz or self.env.user.tz or "America/Toronto",
        })

    def _property_calendar_values(self):
        """Le calendrier de la propriété : uniquement les plages offertes."""
        self.ensure_one()
        return {
            "name": _("Plages offertes — %s") % (self.name or ""),
            "company_id": self.company_id.id,
            "tz": self.tz_name,
            "two_weeks_calendar": False,
        }

    def _sync_windows(self):
        """Reporte les plages offertes dans le calendrier de la propriété.

        Une plage ponctuelle s'écrit comme une ligne de présence bornée par
        `date_from` et `date_to` sur la même date : le noyau écarte la ligne
        pour tout jour hors de ses bornes. Rien à calculer nous-mêmes.
        """
        self.ensure_one()
        if not self.calendar_id:
            return
        lignes = []
        for fenetre in self.window_ids:
            vals = {
                "name": fenetre.display_name,
                "dayofweek": fenetre._dayofweek(),
                "hour_from": fenetre.hour_from,
                "hour_to": fenetre.hour_to,
                "day_period": "morning" if fenetre.hour_from < 12 else "afternoon",
            }
            if fenetre.kind == "once":
                vals["date_from"] = fenetre.date
                vals["date_to"] = fenetre.date
            lignes.append((0, 0, vals))
        self._assert_confirmed_visits_fit(lignes)
        self.calendar_id.sudo().write({"attendance_ids": [(5, 0, 0)] + lignes})

    def _assert_confirmed_visits_fit(self, lignes):
        """Refuser la coupe d'une plage où une visite tient encore debout.

        🔴 Sans ce contrôle, c'est Odoo qui refuse, et il le dit mal : la
        contrainte d'OCA sur `resource.calendar` lève « Cannot schedule these
        bookings… », qui ne nomme ni la plage retirée ni ce qu'il faut faire.
        Mesuré au banc le 2026-09-11. On préfère nommer les visites, et laisser
        la personne décider de les déplacer.
        """
        self.ensure_one()
        futures = self.visit_ids.filtered(
            lambda v: v.state in ("requested", "approved")
            and v.start
            and v.start > fields.Datetime.now()
        )
        if not futures:
            return
        creneaux = self._windows_as_local_intervals(lignes)
        orphelines = self.env["bf.visit"]
        for visite in futures:
            if not self._interval_covered(visite, creneaux):
                orphelines |= visite
        if orphelines:
            raise UserError(
                _(
                    "Ces visites tombent hors des plages que vous gardez :\n\n%s\n\n"
                    "Déplacez-les ou annulez-les d'abord. Retirer la plage sous "
                    "leurs pieds ne les annule pas, ça les laisse debout sans "
                    "disponibilité."
                )
                % "\n".join(
                    "- %s, le %s"
                    % (v.display_name, fields.Datetime.context_timestamp(v, v.start))
                    for v in orphelines
                )
            )

    def _windows_as_local_intervals(self, lignes):
        """Les plages, sous une forme comparable à l'heure d'une visite."""
        creneaux = []
        for ligne in lignes:
            vals = ligne[2]
            creneaux.append({
                "dayofweek": int(vals["dayofweek"]),
                "hour_from": vals["hour_from"],
                "hour_to": vals["hour_to"],
                "date_from": vals.get("date_from"),
                "date_to": vals.get("date_to"),
            })
        return creneaux

    def _interval_covered(self, visite, creneaux):
        """La visite tient-elle dans au moins une des plages gardées?"""
        self.ensure_one()
        debut = fields.Datetime.context_timestamp(
            self.with_context(tz=self.calendar_id.tz or self.env.user.tz), visite.start
        )
        fin = debut + timedelta(hours=visite.duration or self.visit_duration)
        h_debut = debut.hour + debut.minute / 60.0
        h_fin = fin.hour + fin.minute / 60.0
        if fin.date() != debut.date():
            return False
        for creneau in creneaux:
            if creneau["dayofweek"] != debut.weekday():
                continue
            borne_a = creneau.get("date_from")
            borne_b = creneau.get("date_to")
            if borne_a and _as_date(borne_a) > debut.date():
                continue
            if borne_b and _as_date(borne_b) < debut.date():
                continue
            if creneau["hour_from"] <= h_debut and h_fin <= creneau["hour_to"]:
                return True
        return False

    def _sync_booking_assets(self):
        """Monter (ou remettre à jour) type, calendrier, ressource, combinaisons."""
        for rec in self:
            Calendar = self.env["resource.calendar"].sudo()
            Resource = self.env["resource.resource"].sudo()
            Type = self.env["resource.booking.type"].sudo()
            Combination = self.env["resource.booking.combination"].sudo()

            # ⚠️ Toutes les écritures qui suivent passent en `sudo()`, y compris
            # celles de mise à jour. 🔴 Un courtier n'a aucun droit sur
            # `resource.calendar`, `resource.resource`, `resource.booking.type`
            # ni sur les combinaisons : ce sont des objets techniques que le
            # module fabrique POUR lui, pas des données qu'il saisit. Sans ça,
            # sa DEUXIÈME publication échoue (la première crée, la seconde met à
            # jour), ce qu'aucun test joué en administrateur ne peut voir.
            # Trouvé en jouant le parcours comme un courtier sur la production
            # du 2026-09-11. Les valeurs écrites sont calculées ici, jamais
            # reçues de l'utilisateur.
            if not rec.calendar_id:
                rec.calendar_id = Calendar.create(
                    dict(rec._property_calendar_values(), attendance_ids=[(5, 0, 0)])
                )
            else:
                rec.calendar_id.sudo().write(rec._property_calendar_values())

            if not rec.resource_id:
                rec.resource_id = Resource.create({
                    "name": rec.name,
                    "resource_type": "material",
                    "calendar_id": rec.calendar_id.id,
                    "company_id": rec.company_id.id,
                    "tz": rec.calendar_id.tz,
                })
            else:
                rec.resource_id.sudo().write({
                    "name": rec.name,
                    "calendar_id": rec.calendar_id.id,
                    "tz": rec.calendar_id.tz,
                })

            # Une combinaison par courtier. Jamais un K-de-N à trois : il
            # accepterait deux courtiers SANS la propriété.
            voulues = {}
            for courtier in rec.broker_ids:
                ressource = rec._resource_for_user(courtier)
                voulues[courtier.id] = ressource
            existantes = Combination.search(
                [("resource_ids", "in", rec.resource_id.ids)]
            )
            gardees = Combination.browse()
            for courtier_id, ressource in voulues.items():
                trouvee = existantes.filtered(
                    lambda c: set(c.resource_ids.ids)
                    == {rec.resource_id.id, ressource.id}
                )
                if trouvee:
                    gardees |= trouvee[0]
                else:
                    gardees |= Combination.create({
                        "resource_ids": [(6, 0, [rec.resource_id.id, ressource.id])],
                    })
            perimees = existantes - gardees
            if perimees:
                perimees.sudo().write({"active": False})

            valeurs_type = {
                "name": _("Visite — %s") % rec.name,
                "duration": rec.visit_duration,
                "slot_duration": rec.slot_duration,
                "modifications_deadline": rec._effective_lead_time(),
                "resource_calendar_id": rec._frame_calendar().id,
                "location": rec.address_display or rec.name,
                "is_in_person": True,
                # 🔴 Trouvé au parcours réel du 2026-09-11, invisible aux tests
                # Python : le type naît avec une salle de visioconférence et le
                # consentement d'enregistrement EXIGÉ, alors la page publique
                # refusait toute réservation avec « l'enregistrement fait partie
                # du service ». Une visite de maison se fait sur place, personne
                # ne transcrit rien.
                "video_provider": "none",
                "requires_recording_consent": False,
                "is_public": rec.state == "published",
                "listed_on_landing": False,
                "slot_capacity": rec.slot_capacity if rec.open_house else 1,
                "requester_advice": rec._advice_text(),
                "company_id": rec.company_id.id,
                "combination_rel_ids": [(5, 0, 0)]
                + [(0, 0, {"combination_id": c.id}) for c in gardees],
                "visit_listing_id": rec.id,
            }
            if not rec.booking_type_id:
                rec.booking_type_id = Type.create(valeurs_type)
            else:
                rec.booking_type_id.sudo().write(valeurs_type)
            rec._sync_intake_fields()
            rec._sync_windows()

    def _advice_text(self):
        self.ensure_one()
        morceaux = []
        if self.approval_mode != "auto":
            morceaux.append(
                _("Votre demande est transmise pour approbation. Vous recevrez "
                  "une réponse par courriel.")
            )
        if self.occupancy == "tenant":
            morceaux.append(
                _("Le logement est occupé : la visite est annoncée au locataire "
                  "au moins 24 heures d'avance, entre 9 h et 21 h.")
            )
        return "\n".join(morceaux)

    def _sync_intake_fields(self):
        """La question de la représentation, posée sur le formulaire public.

        Elle n'est pas décorative : le courtier du vendeur doit la poser à la
        première occasion, et la réponse pèse dans un litige sur la cause
        efficiente de la vente. On la pose donc AVANT la visite, par écrit, avec
        une heure.
        """
        self.ensure_one()
        Field = self.env["appointment.intake.field"].sudo()
        if not self.booking_type_id:
            return
        libelle = _("Êtes-vous représenté par un courtier immobilier?")
        libelle_nom = _("Si oui, le nom de votre courtier et de son agence")
        champs = Field.search([("type_id", "=", self.booking_type_id.id)])
        porte_question = champs.filtered(
            lambda f: f.bf_visit_role == "representation"
        )
        porte_nom = champs.filtered(lambda f: f.bf_visit_role == "broker_name")
        # ⚠️ Reprise des questions posées AVANT que le rôle existe. Sans elle,
        # une inscription déjà en ligne se retrouve avec deux fois la même
        # question : l'ancienne, que personne ne lit plus, et la neuve. Le
        # critère est le libellé exact de ce qu'on aurait écrit, donc on ne
        # s'empare jamais d'une question ajoutée à la main.
        if not porte_question:
            orpheline = champs.filtered(
                lambda f: not f.bf_visit_role and f.name == libelle
            )
            if orpheline:
                orpheline.bf_visit_role = "representation"
                porte_question = orpheline
        if not porte_nom:
            orpheline = champs.filtered(
                lambda f: not f.bf_visit_role and f.name == libelle_nom
            )
            if orpheline:
                orpheline.bf_visit_role = "broker_name"
                porte_nom = orpheline
        if not self.require_representation:
            (porte_question | porte_nom).unlink()
            return
        if not porte_question:
            Field.create({
                "type_id": self.booking_type_id.id,
                "name": libelle,
                "field_type": "select",
                "select_options": "%s\n%s" % (_("Oui"), _("Non")),
                "required": True,
                "sequence": 1,
                "bf_visit_role": "representation",
            })
        if not porte_nom:
            Field.create({
                "type_id": self.booking_type_id.id,
                "name": libelle_nom,
                "field_type": "text",
                "required": False,
                "sequence": 2,
                "bf_visit_role": "broker_name",
            })

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_publish(self):
        for rec in self:
            if not rec.broker_ids:
                raise UserError(
                    _(
                        "« %s » n'a aucun courtier autorisé. Sans personne pour "
                        "ouvrir la porte, aucun créneau n'existe."
                    )
                    % rec.name
                )
            if not rec.window_ids:
                raise UserError(
                    _(
                        "« %s » n'offre aucune plage. Le vendeur doit dire quand "
                        "il accepte de faire visiter."
                    )
                    % rec.name
                )
            rec.state = "published"
            rec._sync_booking_assets()
            if not rec.walkin_token:
                rec.walkin_token = rec._new_token()
        return True

    def action_suspend(self):
        for rec in self:
            rec.state = "suspended"
            if rec.booking_type_id:
                rec.booking_type_id.sudo().is_public = False
        return True

    def action_close(self):
        for rec in self:
            rec.state = "closed"
            if rec.booking_type_id:
                rec.booking_type_id.sudo().is_public = False
        return True

    def action_back_to_draft(self):
        self.write({"state": "draft"})
        return True

    def action_open_public_page(self):
        self.ensure_one()
        if not self.booking_type_id:
            raise UserError(_("Publiez l'inscription d'abord."))
        return {
            "type": "ir.actions.act_url",
            "url": "/appointment/%s" % self.booking_type_id.slug,
            "target": "new",
        }

    def action_view_visits(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Visites de %s") % self.name,
            "res_model": "bf.visit",
            "view_mode": "list,form",
            "domain": [("listing_id", "=", self.id)],
            "context": {"default_listing_id": self.id},
        }

    def action_open_walkin_sheet(self):
        """La feuille d'inscription sur place, celle qu'on met derrière un code QR."""
        self.ensure_one()
        if not self.walkin_token:
            self.walkin_token = self._new_token()
        return {
            "type": "ir.actions.act_url",
            "url": "/visite/accueil/%s" % self.walkin_token,
            "target": "new",
        }

    @api.model
    def _new_token(self):
        import secrets
        return secrets.token_urlsafe(24)

    # ------------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if not rec.walkin_token:
                rec.walkin_token = rec._new_token()
        return records

    def write(self, vals):
        result = super().write(vals)
        champs_structurants = {
            "name", "broker_ids", "visit_duration", "slot_duration",
            "lead_time_hours", "occupancy", "open_house", "slot_capacity", "tz",
            "state", "approval_mode", "require_representation", "street",
            "street2", "city", "state_id", "zip",
        }
        if champs_structurants & set(vals):
            self.filtered(
                lambda r: r.booking_type_id or r.state == "published"
            )._sync_booking_assets()
        return result

    def unlink(self):
        """Emporter ce que l'inscription a fabriqué, et rien d'autre.

        Le type de rendez-vous et la ressource n'ont pas de vie propre : ils
        sont nés de l'inscription. Les laisser derrière remplit la liste des
        types d'adresses vendues il y a trois ans.
        """
        types = self.mapped("booking_type_id")
        ressources = self.mapped("resource_id")
        calendriers = self.mapped("calendar_id")
        result = super().unlink()
        for lot in (types, ressources, calendriers):
            for enr in lot:
                try:
                    enr.sudo().write({"active": False})
                except Exception:  # noqa: BLE001
                    _logger.info("Rien à archiver sur %s", enr)
        return result


def _as_date(valeur):
    """Une date, qu'elle arrive en date, en datetime ou en chaîne."""
    if isinstance(valeur, datetime):
        return valeur.date()
    if isinstance(valeur, date):
        return valeur
    return fields.Date.to_date(valeur)

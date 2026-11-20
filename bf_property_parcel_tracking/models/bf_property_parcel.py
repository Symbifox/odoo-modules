"""Le colis qu'on attend, et ce que le transporteur en dit.

## Ce que ces API permettent, et ce qu'elles ne permettent pas

Les trois transporteurs répondent **sur un numéro de suivi**. Aucun ne dit
« voici ce qui a été livré au 123 rue Principale aujourd'hui ». Donc « relier
Purolator, UPS et DHL » ne peut pas vouloir dire « les colis se créent tout
seuls » : ça veut dire **suivre un numéro que quelqu'un a donné** — le résident
qui annonce son colis au portail, ou l'étiquette que le casier a lue — jusqu'à
ce que le transporteur dise « livré ».

## L'état « Annoncé »

Un colis annoncé n'est pas un colis reçu : personne ne l'a dans les mains. Il
entre donc dans le journal en **Annoncé**, et il ne devient **En dépôt** que
quand le transporteur l'a livré. C'est à ce moment-là, et pas avant, que l'avis
part au résident.

🔴 La liste des états est reconstruite ici **à partir de celle du journal**
(``PARCEL_STATES``), pas recopiée : si une version future du journal ajoute un
état, il apparaît ici tout seul, et un essai le vérifie.

## Vie privée

Un numéro de suivi et les étapes d'une livraison disent ce que quelqu'un
reçoit, et d'où. Le module garde **l'état courant, son libellé et sa date** —
pas l'itinéraire, pas la signature, pas la photo de livraison, qu'il ne demande
jamais (``returnSignature`` et ``returnPOD`` restent à « false » chez UPS). Le
numéro s'effacera avec la fiche, à la purge du journal des colis.
"""
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.tools import config
from odoo.exceptions import AccessError, UserError

from odoo.addons.bf_property_portal.models.bf_property_delivery import PARCEL_STATES

from . import carrier_api
from .bf_property_carrier_account import CARRIERS

_logger = logging.getLogger(__name__)

EXPECTED = ("expected", "Annoncé")
TRACKING_STATES = [
    ("pending", "À interroger"),
    ("pre_transit", "Étiquette créée"),
    ("in_transit", "En route"),
    ("out_for_delivery", "En livraison"),
    ("delivered", "Livré, selon le transporteur"),
    ("exception", "Problème signalé par le transporteur"),
    ("not_found", "Numéro inconnu du transporteur"),
    ("unknown", "Le transporteur ne se prononce pas"),
    ("blocked", "À suivre à la main"),
]
# Ce qu'on interroge encore.
OPEN_TRACKING = ("pending", "pre_transit", "in_transit", "out_for_delivery",
                 "unknown", "exception", "not_found")
# Combien de minutes avant la prochaine interrogation, selon ce qu'on sait.
# Un colis en livraison se regarde souvent ; une étiquette créée hier, non.
DELAYS = {
    "pending": 30,
    "pre_transit": 240,
    "in_transit": 120,
    "out_for_delivery": 30,
    "unknown": 180,
    "exception": 240,
    "not_found": 240,
}
# Un numéro que le transporteur ne connaît pas après six fois est un numéro mal
# saisi, pas un colis en retard.
MAX_NOT_FOUND = 6
# Un colis annoncé il y a un mois n'arrivera pas : on arrête d'interroger.
MAX_DAYS = 30
MAX_LABEL = 200


class BfPropertyParcel(models.Model):
    _inherit = "bf.property.parcel"

    # Ce qu'un résident peut déposer en annonçant SON colis, et rien d'autre.
    # La garde du socle (`_ensure_portal_create_scope`) refuse le reste, et
    # refuse aussi un `default_<champ>` forgé au contexte d'un appel RPC.
    _portal_creatable_fields = (
        "organisation_id", "building_id", "unit_id", "partner_id",
        "parcel_type", "state", "tracking_carrier", "tracking_number",
    )

    _sql_constraints = [
        # 🔴 Sans elle, un résident (ou un compte de portail compromis) posait
        # mille fois le même numéro, et chaque fiche prenait sa place dans la
        # file d'interrogation : le plafond horaire partait chez lui, et le
        # colis du voisin n'était jamais interrogé.
        ("uniq_tracking_number",
         "unique(company_id, tracking_carrier, tracking_number)",
         "Ce numéro de suivi est déjà annoncé pour ce transporteur."),
    ]

    # 🔴 Construite depuis la liste du journal : « Annoncé » d'abord (c'est le
    # début de la vie du colis), puis les états du journal dans leur ordre.
    state = fields.Selection(selection=[EXPECTED] + list(PARCEL_STATES))

    tracking_carrier = fields.Selection(
        CARRIERS + [("other", "Autre transporteur")],
        string="Transporteur suivi",
        help="Les trois transporteurs branchés s'interrogent tout seuls. "
             "« Autre » garde le numéro sans rien interroger.",
    )
    tracking_number = fields.Char(
        string="Numéro de suivi", copy=False, index=True,
        help="Le numéro que le transporteur a donné au colis. C'est le "
             "résident qui le connaît : personne n'a à le devenir.",
    )
    tracking_state = fields.Selection(
        TRACKING_STATES, string="Suivi", readonly=True, copy=False, index=True)
    tracking_label = fields.Char(
        string="Ce que dit le transporteur", readonly=True, copy=False,
        help="Le libellé rendu par le transporteur, dans ses mots. Il n'est "
             "pas traduit : c'est sa parole, pas la nôtre.",
    )
    tracking_raw_code = fields.Char(
        string="Code du transporteur", readonly=True, copy=False,
        groups="bf_property_core.group_bf_property_user",
        help="Le code brut, gardé pour qui doit appeler le transporteur.",
    )
    tracking_event_at = fields.Datetime(
        string="Dernier événement", readonly=True, copy=False)
    tracking_checked_at = fields.Datetime(
        string="Interrogé le", readonly=True, copy=False)
    tracking_next_check = fields.Datetime(
        string="Prochaine interrogation", readonly=True, copy=False, index=True)
    tracking_attempts = fields.Integer(
        string="Interrogations", readonly=True, copy=False, default=0)
    tracking_message = fields.Text(
        string="Dernier refus du transporteur", readonly=True, copy=False,
        # ⚠️ Réservé au bureau : ce champ porte des refus techniques, et il
        # était lisible par l'occupant sur sa propre fiche.
        groups="bf_property_core.group_bf_property_user")
    tracking_notified = fields.Boolean(
        string="Avis d'arrivée tenté", readonly=True, copy=False,
        groups="bf_property_core.group_bf_property_user",
        help="Posé AVANT l'avis au résident. Un texto parti ne se rappelle "
             "pas : sans cette marque, un retour en arrière après l'envoi "
             "faisait repartir l'avis au passage suivant.")
    tracking_since = fields.Datetime(
        string="Annoncé le", readonly=True, copy=False,
        groups="bf_property_core.group_bf_property_user",
        help="Le moment où ce numéro de suivi est entré en file. L'arrêt au "
             "bout de trente jours se compte depuis là, pas depuis la "
             "création de la fiche.")

    # ── Mise en file ─────────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        self._ensure_portal_create_scope(vals_list)
        outside = not (self.env.su or self.env.user._is_internal())
        if outside:
            self._bf_bound_announcement(vals_list)
        for vals in vals_list:
            if outside:
                # 🔴 La liste blanche dit QUELS champs un occupant peut
                # remplir, pas AVEC QUOI. Sans ces deux lignes, il annonçait un
                # colis au nom du voisin (la garde du journal ne vérifie que
                # l'appartenance au syndicat), ou le consignait d'emblée « en
                # dépôt » — une écriture au registre, pas une annonce.
                vals["partner_id"] = self.env.user.partner_id.id
                vals["state"] = "expected"
            if vals.get("tracking_number") and vals.get("tracking_carrier") in dict(CARRIERS):
                vals.setdefault("tracking_state", "pending")
                vals.setdefault("tracking_next_check", fields.Datetime.now())
                vals.setdefault("tracking_since", fields.Datetime.now())
        if vals_list and all(v.get("state") == "expected" for v in vals_list):
            # ⚠️ Le fil d'un colis annoncé disait « aucun avis par texto : le
            # colis n'est pas en dépôt », ce qui se lit comme un avis RETENU.
            # Rien n'est retenu : il n'est pas encore dû.
            self = self.with_context(bf_parcel_notice_elsewhere=_(
                "le colis est annoncé, l'avis partira à la livraison"))
        return super(BfPropertyParcel, self).create(vals_list)

    def write(self, vals):
        res = super().write(vals)
        if (("tracking_number" in vals or "tracking_carrier" in vals)
                and not self.env.context.get("bf_tracking_requeue")):
            for parcel in self:
                parcel._tracking_requeue()
        return res

    def _bf_bound_announcement(self, vals_list):
        """Une annonce ne vise que le logement de qui l'annonce.

        🔴 La garde du journal ne vérifie que l'appartenance au SYNDICAT
        (`_check_person_is_here` sans fraction), et aucune règle d'accès ne
        regarde la fraction : un occupant pouvait donc annoncer un colis sur
        l'immeuble d'un autre syndicat de la même société de gestion, et son
        nom paraissait au registre de ce syndicat-là, groupé par immeuble
        (relecture adverse du 2026-11-19). La route filtrait déjà, mais c'est
        le MODÈLE qui est ouvert à la création, pas seulement la route.
        """
        partner = self.env.user.partner_id
        units = self.env["bf.property.unit"].sudo()
        owned, occupied = units._portal_units_for(partner)
        mine = owned | occupied
        for vals in vals_list:
            unit = mine.filtered(lambda u: u.id == vals.get("unit_id"))
            # 🔴 Le refus AVANT le repli : avec le repli d'abord, une fraction
            # qui n'est pas la sienne était remplacée par la sienne en silence
            # au lieu d'être refusée — et sur un compte qui en a plusieurs,
            # rien n'aurait refusé du tout.
            if vals.get("unit_id") and not unit:
                raise AccessError(_(
                    "Vous ne pouvez annoncer un colis que pour un logement qui "
                    "est le vôtre."))
            if not unit and len(mine) == 1:
                # Le cas courant : une seule fraction, et le navigateur
                # n'envoie pas de sélecteur. Le serveur la connaît.
                unit = mine
            vals["unit_id"] = unit.id if unit else False
            vals["building_id"] = unit.building_id.id if unit else False
            organisation = (unit.organisation_id
                            or mine.mapped("organisation_id")[:1])
            if not organisation:
                raise AccessError(_(
                    "Votre compte n'est rattaché à aucun logement : il n'y a "
                    "pas de registre où annoncer ce colis."))
            vals["organisation_id"] = organisation.id

    def _tracking_requeue(self):
        """Un numéro neuf ou changé se remet en file, et oublie le passé."""
        self.ensure_one()
        known = self.tracking_carrier in dict(CARRIERS)
        if not (self.tracking_number and known):
            # Plus de numéro, ou un transporteur qu'on n'interroge pas : on ne
            # garde pas un état de suivi qui ne veut plus rien dire.
            if self.tracking_state:
                # Le drapeau de contexte coupe la récursion sans sauter les
                # surcharges d'écriture des modules chargés après celui-ci.
                self.with_context(bf_tracking_requeue=True).write({
                    "tracking_state": False, "tracking_next_check": False,
                    "tracking_label": False, "tracking_raw_code": False,
                    "tracking_message": False, "tracking_attempts": 0,
                    "tracking_since": False,
                })
            return False
        self.with_context(bf_tracking_requeue=True).write({
            "tracking_state": "pending",
            "tracking_next_check": fields.Datetime.now(),
            "tracking_since": fields.Datetime.now(),
            "tracking_attempts": 0,
            "tracking_label": False,
            "tracking_raw_code": False,
            "tracking_message": False,
        })
        return True

    # ── Interrogation ────────────────────────────────────────────────────

    @api.model
    def _cron_track(self, limit=200):
        """Interroge les transporteurs pour ce qui est dû.

        🔴 Un point de sauvegarde par colis : un refus n'emporte pas les
        suivants, et la trace du refus s'écrit APRÈS le retour en arrière.
        """
        now = fields.Datetime.now()
        parcels = self.sudo().search([
            ("state", "in", ("expected", "held")),
            ("tracking_state", "in", OPEN_TRACKING),
            ("tracking_next_check", "<=", now),
            ("tracking_number", "!=", False),
        ], order="tracking_next_check asc, id asc", limit=limit)
        checked = 0
        for parcel in parcels:
            failure = None
            try:
                with self.env.cr.savepoint():
                    parcel._track_once()
                checked += 1
            except carrier_api.CarrierError as exc:
                failure = (str(exc), exc.permanent)
                if exc.token_invalid:
                    # Après le retour en arrière, donc l'oubli tient : sans
                    # cela, les 199 colis suivants repartaient tous avec le
                    # jeton qu'UPS vient de refuser.
                    compte = self.env["bf.property.carrier.account"]._for(
                        parcel.tracking_carrier, parcel.company_id)
                    compte._ups_forget_token()
            except Exception as exc:  # noqa: BLE001 — consigné, jamais avalé
                _logger.error("Suivi du colis %s : échec inattendu (%s)",
                              parcel.id, type(exc).__name__)
                failure = (_("Échec inattendu du suivi (%s). Le détail est au "
                             "journal du serveur.") % type(exc).__name__, False)
            if failure is None:
                # 🔴 Hors du point de sauvegarde, et marqué avant l'envoi : un
                # texto parti ne se rappelle pas. Et hors du `except` du
                # suivi : un échec d'envoi n'a pas à s'écrire « échec du
                # suivi » sur le colis, ni à accuser le transporteur.
                try:
                    parcel._tracking_notify_arrival()
                except Exception:  # noqa: BLE001 — consigné, jamais avalé
                    _logger.exception("Avis d'arrivée du colis %s", parcel.id)
                # Un commit par colis : deux cents interrogations à trente
                # secondes d'attente tiendraient sinon une seule transaction
                # pendant cent minutes.
                if not (config["test_enable"] or self.env.registry.in_test_mode()):
                    self.env.cr.commit()
                continue
            message, permanent = failure
            parcel._tracking_failed(message, permanent)
            if not (config["test_enable"] or self.env.registry.in_test_mode()):
                self.env.cr.commit()
        return checked

    def _tracking_notify_arrival(self):
        """Avise le résident d'une arrivée, une seule fois, jamais deux.

        La marque est écrite et VALIDÉE avant l'envoi : elle survit à tout ce
        qui pourrait lever ensuite (archivage du texto, contrainte, arrêt du
        conteneur). Un avis manquant se voit ; un avis en double se paie.
        """
        self.ensure_one()
        if self.state != "held" or self.tracking_state != "delivered":
            return False
        if self.tracking_notified:
            return False
        self.sudo().write({"tracking_notified": True})
        if not (config["test_enable"] or self.env.registry.in_test_mode()):
            self.env.cr.commit()
        if hasattr(self, "_notify_arrival"):
            self._notify_arrival()
        return True

    def _track_once(self):
        """Une interrogation. Lève ``CarrierError`` si le transporteur refuse."""
        self.ensure_one()
        if self._tracking_too_old():
            return self._tracking_give_up(_(
                "Annoncé il y a plus de %s jours sans être livré : "
                "l'interrogation s'arrête. Le colis reste à suivre à la main.")
                % MAX_DAYS)
        account = self.env["bf.property.carrier.account"]._for(
            self.tracking_carrier, self.company_id)
        if not account:
            return self._tracking_give_up(_(
                "Aucun compte %s n'est configuré pour cette société : le suivi "
                "ne peut pas être interrogé.")
                % dict(CARRIERS).get(self.tracking_carrier, self.tracking_carrier))
        if not account._tracking_allowance():
            # Le plafond horaire du compte est atteint : on repousse, on ne
            # refuse pas. Rien n'est perdu.
            self.sudo().write({"tracking_next_check":
                               fields.Datetime.now() + timedelta(minutes=15)})
            return False
        state, label, instant, raw = account._bf_track(
            self.tracking_number, tz_name=self._bf_parcel_tz())
        return self._tracking_record(state, label, instant, raw)

    def _bf_parcel_tz(self):
        """Le fuseau du dossier, comme le reste de la famille le fait : celui
        de l'organisation, puis celui de la société, jamais celui de la
        personne connectée ni celui du compte système."""
        self.ensure_one()
        organisation = self.organisation_id
        return (organisation.partner_id.tz
                or organisation.company_id.partner_id.tz
                or "America/Toronto")

    def _tracking_record(self, state, label, instant, raw):
        """Consigne ce que le transporteur a dit, et agit si c'est « livré »."""
        self.ensure_one()
        now = fields.Datetime.now()
        attempts = self.tracking_attempts + 1
        values = {
            "tracking_state": state,
            "tracking_label": (label or "")[:MAX_LABEL] or False,
            "tracking_raw_code": (raw or "")[:64] or False,
            "tracking_checked_at": now,
            "tracking_attempts": attempts,
            "tracking_message": False,
        }
        if instant:
            values["tracking_event_at"] = instant
        if state == "not_found" and attempts >= MAX_NOT_FOUND:
            values.update({
                "tracking_state": "blocked",
                "tracking_next_check": False,
                "tracking_message": _(
                    "Le transporteur ne connaît pas ce numéro, après "
                    "%s interrogations : il est probablement mal saisi.")
                % attempts,
            })
            self.sudo().write(values)
            self._message_log(body=values["tracking_message"])
            return False
        if state == "delivered":
            values["tracking_next_check"] = False
            self.sudo().write(values)
            return self._tracking_delivered(instant)
        values["tracking_next_check"] = now + timedelta(
            minutes=DELAYS.get(state, 180))
        self.sudo().write(values)
        return True

    def _tracking_delivered(self, instant):
        """Le transporteur dit « livré ». Ce que ça change, et ce que non.

        🔴 Un colis **annoncé** devient **en dépôt**, et c'est là que l'avis
        part au résident : c'est le sens de tout ce module. Un colis déjà en
        dépôt (venu d'un casier, ou consigné par le concierge) n'est pas touché
        — il est déjà là, et son avis est déjà parti.
        """
        self.ensure_one()
        when = instant or fields.Datetime.now()
        if self.state != "expected":
            self._message_log(body=_(
                "Le transporteur déclare ce colis livré (%s).")
                % fields.Datetime.to_string(when))
            return True
        self.sudo().write({"state": "held", "date_received": when})
        self._message_log(body=_(
            "Le transporteur déclare ce colis livré : il passe en dépôt, et le "
            "destinataire sera avisé."))
        # ⚠️ L'avis NE PART PAS d'ici : il sort du point de sauvegarde, par
        # `_tracking_notify_arrival`, une fois cet état validé.
        return True

    def _tracking_failed(self, message, permanent):
        """Un refus : consigné, et repoussé — ou arrêté s'il est définitif."""
        self.ensure_one()
        now = fields.Datetime.now()
        values = {
            "tracking_message": message[:2000],
            "tracking_checked_at": now,
            "tracking_attempts": self.tracking_attempts + 1,
        }
        if permanent:
            # Rejouer ne changerait rien (clé refusée, numéro impossible,
            # transporteur non branché) : le colis se suit à la main, et la
            # raison est écrite noir sur blanc.
            values.update({"tracking_state": "blocked", "tracking_next_check": False})
            self.sudo().write(values)
            self._message_log(body=_("Suivi arrêté : %s") % message)
            return False
        values["tracking_next_check"] = now + timedelta(
            minutes=min(30 * max(self.tracking_attempts, 1), 24 * 60))
        self.sudo().write(values)
        return True

    def _tracking_give_up(self, message):
        self.ensure_one()
        self.sudo().write({
            "tracking_state": "blocked",
            "tracking_next_check": False,
            "tracking_message": message,
            "tracking_checked_at": fields.Datetime.now(),
        })
        self._message_log(body=message)
        return False

    def _tracking_too_old(self):
        """Trente jours depuis la mise en file, pas depuis la fiche.

        Un colis consigné il y a six semaines auquel le concierge ajoute un
        numéro aujourd'hui vient d'être annoncé : l'arrêter au premier passage
        avec « annoncé il y a plus de trente jours » serait faux.
        """
        self.ensure_one()
        born = self.tracking_since or self.create_date or fields.Datetime.now()
        return born < fields.Datetime.now() - timedelta(days=MAX_DAYS)

    # ── Purge des annonces ───────────────────────────────────────────────

    @api.model
    def _cron_purge_announcements(self):
        """Une annonce qui n'a rien donné ne reste pas au journal pour toujours.

        🔴 La purge du journal ne prend que les fiches CLOSES (remises ou
        retournées), sur leur date de fermeture. Une annonce que le suivi a
        abandonnée — numéro mal saisi, colis jamais livré, transporteur qu'on
        n'interroge pas — n'a pas de date de fermeture : elle échappait à toute
        purge, alors que la page du portail promet au résident que son numéro
        « disparaît avec la fiche du colis ».
        """
        now = fields.Datetime.now()
        purged = self.browse()
        organisations = self.env["bf.property.organisation"].sudo().search(
            [("log_retention_days", ">", 0)])
        for organisation in organisations:
            cutoff = now - timedelta(days=organisation.log_retention_days)
            purged |= self.sudo().search([
                ("organisation_id", "=", organisation.id),
                ("state", "=", "expected"),
                ("tracking_state", "in", (False, "blocked")),
                "|", ("tracking_since", "<", cutoff),
                ("create_date", "<", cutoff),
            ])
        count = len(purged)
        purged.unlink()
        return count

    def action_cancel_announcement(self):
        """Le résident retire l'annonce qu'il a faite. Lui seul, et seulement
        tant que le colis n'est pas arrivé."""
        for parcel in self:
            if parcel.state != "expected":
                raise UserError(_("« %s » n'est plus une simple annonce.")
                                % parcel.name)
        self.unlink()
        return True

    # ── Gestes à la main ─────────────────────────────────────────────────

    def action_track_now(self):
        """Interroge tout de suite. Le refus se voit, il ne se devine pas."""
        self._ensure_organisation_decides(_("Interroger un transporteur"))
        for parcel in self:
            if not (parcel.tracking_number
                    and parcel.tracking_carrier in dict(CARRIERS)):
                raise UserError(_(
                    "« %s » n'a pas de numéro de suivi chez un transporteur "
                    "branché.") % parcel.name)
            try:
                parcel._track_once()
            except carrier_api.CarrierError as exc:
                parcel._tracking_failed(str(exc), exc.permanent)
                raise UserError(_("%(carrier)s a refusé : %(why)s") % {
                    "carrier": dict(CARRIERS).get(parcel.tracking_carrier),
                    "why": exc,
                }) from exc
            # 🔴 Et l'avis part, comme au passage planifié. Sans cette ligne, le
            # bouton consignait le colis, écrivait « le destinataire est avisé »
            # au fil, retirait la fiche de la file (plus de prochaine
            # interrogation) — et personne n'était jamais avisé. C'est aussi le
            # seul chemin de reprise d'un suivi arrêté : il perdait l'avis.
            parcel._tracking_notify_arrival()
        return True

    def action_arrived(self):
        """Le concierge constate l'arrivée avant que le transporteur la dise.

        🔴 La marque d'avis se pose ICI aussi, et le suivi s'arrête. Sans elle,
        le colis restait dans la file : deux heures plus tard, UPS disait
        « livré », le passage planifié voyait un colis en dépôt, livré et non
        avisé, et un SECOND texto partait, facturé (relecture adverse, 2e tour).
        """
        self._ensure_organisation_decides(_("Consigner l'arrivée d'un colis"))
        for parcel in self:
            if parcel.state != "expected":
                raise UserError(_("« %s » n'est pas un colis annoncé.") % parcel.name)
            parcel.write({"state": "held",
                          "date_received": fields.Datetime.now()})
            parcel.sudo().write({"tracking_notified": True,
                                 "tracking_next_check": False})
            parcel._message_log(body=_("Colis arrivé, constaté au comptoir."))
            if hasattr(parcel, "_notify_arrival"):
                parcel._notify_arrival()
        return True

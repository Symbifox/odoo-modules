# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Plages de travail typées.

Le type d'une plage se lit dans ses étiquettes : c'est la première étiquette
« plage de travail » qu'elle porte. Le choisir pose l'étiquette, et une
étiquette posée ailleurs (Nextcloud, un téléphone en CalDAV) donne le type au
retour. Une seule vérité, celle qui voyage.
"""

from odoo import Command, api, fields, models

# Du plus ouvert au plus fermé : un type ne fait jamais descendre une
# confidentialité.
RANG_CONFIDENTIALITE = {"public": 0, "confidential": 1, "private": 2}


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    bf_block_type_id = fields.Many2one(
        "calendar.event.type",
        string="Work block",
        compute="_compute_bf_block_type_id",
        inverse="_inverse_bf_block_type_id",
        store=True,
        index=True,
        domain=[("bf_is_block", "=", True)],
        help="The kind of work this block of time is booked for. Stored as "
             "one of the event's tags, so that it reaches other calendars.")
    # Lu par la grille pour le titre. Hors des champs publics du cœur, comme
    # le type : sur l'événement privé d'un autre, les deux reviennent vides et
    # la bulle dit seulement « Occupé ». Pas en sudo : lu en sudo, le type
    # non masqué entrait dans le cache partagé.
    bf_block_glyph = fields.Char(related="bf_block_type_id.bf_block_glyph", related_sudo=False)

    @api.depends("categ_ids", "categ_ids.bf_is_block", "categ_ids.bf_block_sequence")
    def _compute_bf_block_type_id(self):
        for event in self:
            blocs = event.categ_ids.filtered("bf_is_block").sorted(
                lambda t: (t.bf_block_sequence, t.id))
            event.bf_block_type_id = blocs[:1]

    def _inverse_bf_block_type_id(self):
        # Filet seulement : write() et create() traduisent déjà le type en
        # commandes sur les étiquettes (voir `_bf_block_vers_etiquettes`).
        for event in self:
            blocs = event.categ_ids.filtered("bf_is_block")
            voulu = event.bf_block_type_id
            if set(blocs.ids) != set(voulu.ids):
                event.categ_ids = (event.categ_ids - blocs) | voulu

    @api.model
    def _bf_block_vers_etiquettes(self, vals):
        """Le type écrit, traduit en commandes sur `categ_ids`.

        Écrire le champ calculé laissait l'inverse poser les étiquettes à part,
        occurrence par occurrence : la synchro Nextcloud ne voyait pas
        `categ_ids` dans l'écriture. Un changement de type « pour tous les
        événements » d'une série marquait alors la série « à recréer », et la
        lecture suivante la rasait depuis Nextcloud, type compris. Traduit
        avant super(), le type part comme toute étiquette (CATEGORIES, sur
        chaque VEVENT de la série), en une seule écriture, et vaut aussi pour
        plusieurs fiches. Un type qui n'est pas une plage est ignoré.
        """
        if "bf_block_type_id" not in vals:
            return vals
        vals = dict(vals)
        voulu = vals.pop("bf_block_type_id") or False
        Type = self.env["calendar.event.type"].sudo()
        blocs = Type.search([("bf_is_block", "=", True)]).ids
        if voulu and voulu not in blocs:
            return vals
        commandes = [Command.unlink(b) for b in blocs if b != voulu]
        if voulu:
            commandes.append(Command.link(voulu))
        existantes = vals.get("categ_ids")
        if existantes is False:
            existantes = [Command.clear()]
        vals["categ_ids"] = list(existantes or []) + commandes
        return vals

    def _bf_block_confidentialite_plus_stricte(self, bloc, actuelle, organisateur=None):
        """La confidentialité du type, si elle est plus stricte que l'actuelle, sinon None.

        Sans confidentialité propre, c'est le défaut de l'ORGANISATEUR qui compte,
        comme dans le cœur.
        """
        voulue = bloc.bf_block_privacy
        if not voulue:
            return None
        organisateur = organisateur or self.env.user
        effective = actuelle or organisateur.sudo().calendar_default_privacy or "public"
        if RANG_CONFIDENTIALITE.get(voulue, 0) > RANG_CONFIDENTIALITE.get(effective, 0):
            return voulue
        return None

    @api.onchange("bf_block_type_id")
    def _onchange_bf_block_type_id(self):
        bloc = self.bf_block_type_id
        # Les étiquettes suivent le type DANS le formulaire :
        # sans cela, ajouter ensuite une étiquette recalculait le type depuis des
        # étiquettes où il n'était pas, et l'effaçait.
        blocs = self.categ_ids.filtered("bf_is_block")
        if blocs != bloc:
            self.categ_ids = (self.categ_ids - blocs) | bloc
        if not bloc:
            return
        # Le reste ne vaut que pour une fiche neuve (sur une
        # série existante, renommer rendait l'écriture mixte), jamais pour une
        # plage retenue (bf_calendar_hold), et jamais vers moins de
        # confidentialité.
        if self._origin.id or self.env.context.get("default_bf_hold_for_id"):
            return
        # Le titre suit le type tant qu'il n'a pas été écrit à la main : un
        # titre vide, ou celui d'un autre type choisi juste avant.
        noms_de_types = set(self.env["calendar.event.type"].search(
            [("bf_is_block", "=", True)]).mapped("name"))
        if not self.name or self.name in noms_de_types:
            self.name = bloc.name
        self.show_as = bloc.bf_block_show_as or "busy"
        stricte = self._bf_block_confidentialite_plus_stricte(bloc, self.privacy, self.user_id)
        if stricte:
            self.privacy = stricte

    @api.model_create_multi
    def create(self, vals_list):
        Type = self.env["calendar.event.type"]
        prets = []
        for vals in vals_list:
            bloc = Type.browse(vals.get("bf_block_type_id") or [])
            if bloc.exists() and bloc.bf_is_block:
                # Les défauts du type, quand l'appel ne dit rien : une plage
                # créée par RPC ou par un autre module se comporte comme celle
                # de la grille. Un `default_name` du contexte l'emporte.
                if not vals.get("name") and not self.env.context.get("default_name"):
                    vals["name"] = bloc.name
                if "show_as" not in vals and not self.env.context.get("default_show_as"):
                    vals["show_as"] = bloc.bf_block_show_as or "busy"
                if "privacy" not in vals:
                    organisateur = self.env["res.users"].browse(
                        vals.get("user_id") or self.env.context.get("default_user_id")
                        or self.env.uid)
                    stricte = self._bf_block_confidentialite_plus_stricte(bloc, False, organisateur)
                    if stricte:
                        vals["privacy"] = stricte
            pret = self._bf_block_vers_etiquettes(vals)
            # À la création, la clé reste : retirée, la valeur par défaut du type
            # (contexte de la création rapide) reviendrait par l'inverse et
            # remplacerait le type demandé. Les étiquettes étant déjà justes,
            # l'inverse n'écrit rien.
            if "bf_block_type_id" in vals and "categ_ids" in pret:
                bloc = Type.browse(vals["bf_block_type_id"] or [])
                pret["bf_block_type_id"] = bloc.id if bloc.exists() and bloc.bf_is_block else False
            prets.append(pret)
        return super().create(prets)

    def write(self, vals):
        return super().write(self._bf_block_vers_etiquettes(vals))

    def copy(self, default=None):
        # Une copie garde le type de l'original, jamais celui de la création
        # rapide encore présent dans le contexte du formulaire.
        return super(CalendarEvent, self.with_context(bf_creation_rapide_type_plage=False)).copy(default)

    @api.model
    def default_get(self, fields_list):
        """Le type choisi dans la création rapide, repris par « Plus d'options ».

        Une clé à nous et non `default_bf_block_type_id` : un `default_` suivrait le
        contexte vers une copie ou un « Nouveau » fait depuis ce formulaire
        (même raison que bf_meeting).
        """
        res = super().default_get(fields_list)
        type_id = self.env.context.get("bf_creation_rapide_type_plage")
        if type_id and "bf_block_type_id" in fields_list and not res.get("bf_block_type_id"):
            res["bf_block_type_id"] = type_id
        return res

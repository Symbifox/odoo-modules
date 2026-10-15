# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Ce qu'un collègue voit de mes rencontres, selon le niveau de partage (lot 4).

Le cœur d'Odoo 18 ne connaît que deux niveaux, réglés par l'auteur de chaque
événement : tout (sauf le privé), ou « Occupé » (privé). Ce module ajoute,
par agenda et par collègue, « disponibilités seulement » et « titres et
lieux » au-dessous, et « modifier » au-dessus.

⚠️ Un masquage d'AFFICHAGE, pas une frontière de sécurité (arbitrage
du 2026-10-14, après la relecture adverse) : il reprend le mécanisme du cœur
pour le privé (`_fetch_query` remplace les valeurs en cache), et couvre la
grille, la bulle, le formulaire, les noms affichés, les participants et le fil
de discussion. Restent hors d'atteinte, comme pour le privé du cœur : la
recherche par un champ masqué (un titre « existe ou non ») et les vues qui
lisent la base en SQL (tableau de bord des rencontres de bf_meeting).

Ne change rien à l'installation : le niveau par défaut est « tous les
détails », le comportement du cœur.
"""

import threading
from itertools import repeat

from odoo import SUPERUSER_ID, _, api, models
from odoo.exceptions import AccessError
from odoo.http import request
from odoo.osv.expression import AND

from .res_users import RANG

GROUPE_TOUT_VOIR = "bf_calendar_share.group_calendar_see_all"

# Ce que montre le niveau « titres et lieux », en plus des champs publics du
# cœur (heures, durée, récurrence, disponibilité). Ni participants, ni
# description, ni lien de visio, ni étiquettes (un nom de client y vit
# souvent) : seulement le type de plage (bf_calendar_block), par son nom.
CHAMPS_TITRES = {"name", "display_name", "location", "bf_block_type_id", "bf_block_glyph"}

# Ce qu'un collègue peut écrire sur la rencontre d'autrui sans la voir :
# les bascules de son propre tableau de bord (bf_meeting, « écarter »).
ECRITURES_SANS_VOIR = {"bf_skip_dashboard", "bf_skip_agenda", "bf_dashboard_skipped_steps"}


def _exempt(env):
    """Hors d'une requête web, ou pour un compte technique, rien n'est masqué.

    Un masquage d'AFFICHAGE (arbitrage du 2026-10-14) : il vaut pour ce qu'un usager
    voit à l'écran ou demande par RPC, pas pour les traitements du serveur. Des
    tâches planifiées tournent sous un vrai compte (rappels d'agenda sous
    l'uid 2 chez BF) : masquées, elles cessaient sans bruit de rappeler les
    rencontres d'un collègue restreint (relecture adverse, second tour, B-2).
    Les essais, eux, éprouvent le masque sans requête.

    Le superutilisateur et les comptes techniques voient tout.

    Plus de clé de contexte (relecture adverse, B1 : le contexte d'un appel RPC
    appartient à l'appelant, qui défaisait tout le masquage). Une lecture en
    sudo pour le compte d'un usager reste masquée pour lui : `env.uid` reste le
    sien (relecture adverse, M1 : noms affichés calculés en sudo).
    """
    if not request and not getattr(threading.current_thread(), "testing", False):
        return True
    return env.uid == SUPERUSER_ID or env.user._is_superuser() \
        or env.user.has_group(GROUPE_TOUT_VOIR)


def niveaux_sql(env, event_ids):
    """``{id d'événement: niveau}`` pour l'usager courant, lus en SQL.

    En SQL et non par l'ORM : aucune lecture préalable ne dépose de vraies
    valeurs dans le cache partagé de la requête. Seuls les événements d'autrui
    où l'usager n'est pas invité y figurent.
    """
    ids = [i for i in event_ids if isinstance(i, int)]
    if not ids:
        return {}
    lecteur = env.user
    env.cr.execute("""
        SELECT e.id, e.user_id FROM calendar_event e
         WHERE e.id = ANY(%s) AND e.user_id IS NOT NULL AND e.user_id != %s
           AND NOT EXISTS (SELECT 1 FROM calendar_event_res_partner_rel r
                            WHERE r.calendar_event_id = e.id AND r.res_partner_id = %s)
    """, [ids, lecteur.id, lecteur.partner_id.id])
    lignes = env.cr.fetchall()
    if not lignes:
        return {}
    proprietaires = env["res.users"].browse({u for _e, u in lignes})
    par_proprietaire = env["bf.calendar.share"]._bf_niveaux_pour(lecteur, proprietaires)
    return {e: par_proprietaire.get(u, "details") for e, u in lignes}


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    def _bf_share_niveaux(self):
        if _exempt(self.env):
            return {}
        return niveaux_sql(self.env, self.ids)

    def _fetch_query(self, query, fields):
        events = super()._fetch_query(query, fields)
        if self.env.su or _exempt(self.env):
            return events
        publics = self._get_public_fields()
        a_masquer = [f for f in fields if f.name not in publics]
        if not a_masquer:
            return events
        niveaux = niveaux_sql(self.env, events.ids)
        for niveau, permis in (("freebusy", publics), ("titles", publics | CHAMPS_TITRES)):
            cibles = events.browse([i for i, n in niveaux.items() if n == niveau])
            if not cibles:
                continue
            champs = [f for f in a_masquer if f.name not in permis]
            if "partner_ids" not in {f.name for f in champs}:
                champs.append(self._fields["partner_ids"])
            for field in champs:
                valeur = field.convert_to_cache(
                    _("Busy") if field.name == "name" else False, cibles)
                self.env.cache.update(cibles, field, repeat(valeur))
        return events

    @api.depends("privacy", "user_id")
    @api.depends_context("uid")
    def _compute_display_name(self):
        # « Occupé » d'abord, et le cœur seulement pour les autres : appelé sur
        # une rencontre masquée, il déposait le vrai titre dans le cache partagé
        # (relecture adverse, second tour, m-1).
        niveaux = self._bf_share_niveaux()
        masquees = self.filtered(lambda e: niveaux.get(e.id) == "freebusy")
        masquees.display_name = _("Busy")
        super(CalendarEvent, self - masquees)._compute_display_name()

    def _compute_user_can_edit(self):
        super()._compute_user_can_edit()
        niveaux = self._bf_share_niveaux()
        for event in self:
            if niveaux.get(event.id) == "edit" and not event._check_private_event_conditions():
                event.user_can_edit = True

    # ------------------------------------------------------------------
    # Écrire, et lire le fil de discussion
    # ------------------------------------------------------------------

    def _bf_share_restreints(self):
        niveaux = self._bf_share_niveaux()
        return self.filtered(lambda e: RANG.get(niveaux.get(e.id, "details"), 2) < RANG["details"])

    def write(self, vals):
        if not self.env.su:
            restreints = self._bf_share_restreints()
            if restreints:
                if set(vals) - ECRITURES_SANS_VOIR:
                    raise self.env["ir.rule"]._make_access_error("write", restreints[:1])
                # Les bascules du tableau de bord (bf_meeting, « écarter ») passent
                # en sudo, après la lecture vérifiée ; `check_access('write')`
                # les refusait sinon (relecture adverse, second tour, M-1).
                self.check_access("read")
                return super(CalendarEvent, self.sudo()).write(vals)
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            restreints = self._bf_share_restreints()
            if restreints:
                raise self.env["ir.rule"]._make_access_error("unlink", restreints[:1])
        return super().unlink()

    @api.model
    def _get_mail_message_access(self, res_ids, operation, model_name=None):
        """Le fil d'une rencontre restreinte se lit comme si on devait pouvoir l'écrire.

        Relecture adverse, B3 : le masquage porte sur les champs, et le fil de
        discussion (courriels, notes, valeurs suivies) restait lisible en entier.
        Exiger le droit d'écrire le referme au-dessous de « tous les détails ».
        """
        acces = super()._get_mail_message_access(res_ids, operation, model_name=model_name)
        if acces == "read" and (model_name or self._name) == "calendar.event" and not _exempt(self.env):
            niveaux = niveaux_sql(self.env, list(res_ids or []))
            if any(RANG[n] < RANG["details"] for n in niveaux.values()):
                return "write"
        return acces

    def _thread_to_store(self, store, /, *, fields=None, request_list=None):
        """Le fil d'une rencontre restreinte s'ouvre sans pièces jointes ni abonnés.

        Relecture adverse, second tour, B-1 : les messages étaient fermés, mais le
        panneau du fil servait encore les pièces jointes et les abonnés (nom et
        courriel des tiers).
        """
        if request_list and not _exempt(self.env):
            niveaux = niveaux_sql(self.env, self.ids)
            restreints = self.filtered(lambda e: RANG.get(niveaux.get(e.id, "details"), 2) < RANG["details"])
            if restreints:
                retires = {"attachments", "followers", "suggestedRecipients", "scheduledMessages"}
                super(CalendarEvent, restreints)._thread_to_store(
                    store, fields=fields, request_list=[r for r in request_list if r not in retires])
                autres = self - restreints
                if autres:
                    super(CalendarEvent, autres)._thread_to_store(
                        store, fields=fields, request_list=request_list)
                return
        return super()._thread_to_store(store, fields=fields, request_list=request_list)

    def _check_access(self, operation):
        """Le chatter passe par `check_access('write')` : on y refuse aussi le restreint."""
        res = super()._check_access(operation)
        if res or operation not in ("write", "unlink") or self.env.su:
            return res
        restreints = self._bf_share_restreints()
        if restreints:
            return restreints, lambda: self.env["ir.rule"]._make_access_error(operation, restreints[:1])
        return res

    # ------------------------------------------------------------------
    # Regrouper
    # ------------------------------------------------------------------

    @api.model
    def _bf_share_proprietaires_restreints(self):
        """Les usagers dont l'agenda est, pour moi, au-dessous de « tous les détails »."""
        lecteur = self.env.user
        Users = self.env["res.users"].sudo().with_context(active_test=False)
        partages = {s.owner_id.id: s.level for s in self.env["bf.calendar.share"].sudo().search(
            [("grantee_id", "=", lecteur.id)])}
        restreints = set(Users.search([
            ("bf_calendar_share_default", "in", ("freebusy", "titles")),
            ("id", "!=", lecteur.id)]).ids)
        for proprietaire, niveau in partages.items():
            if RANG[niveau] < RANG["details"]:
                restreints.add(proprietaire)
            else:
                restreints.discard(proprietaire)
        return list(restreints)

    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True):
        # Comme le cœur pour le privé : regrouper par un champ que je ne vois
        # pas (participants, étiquettes…) écarte les rencontres des agendas
        # qui me sont restreints, sauf celles où je suis invité.
        groupby_l = [groupby] if isinstance(groupby, str) else (groupby or [])
        champs = {g.split(":")[0].split(".")[0] for g in groupby_l}
        champs |= {f.split(":")[0] for f in (fields or []) if ":" in f}
        if not self.env.su and not _exempt(self.env) and champs - self._get_public_fields():
            restreints = self._bf_share_proprietaires_restreints()
            if restreints:
                domain = AND([domain or [], [
                    "|", ("user_id", "not in", restreints),
                    ("partner_ids", "in", self.env.user.partner_id.ids)]])
        return super().read_group(domain, fields, groupby, offset=offset, limit=limit,
                                  orderby=orderby, lazy=lazy)


class CalendarAttendee(models.Model):
    _inherit = "calendar.attendee"

    def _fetch_query(self, query, fields):
        """Les participants d'une rencontre restreinte ne se lisent pas par la bande.

        Relecture adverse, M1 : `calendar.attendee` est lisible par tous les
        internes (règle du cœur 1=1), et rendait nom et courriel des tiers d'une
        rencontre que le collègue ne voit qu'« Occupé ».
        """
        attendees = super()._fetch_query(query, fields)
        if self.env.su or _exempt(self.env):
            return attendees
        a_masquer = [f for f in fields if f.name in ("partner_id", "email", "common_name", "state",
                                                      "availability")]
        if not a_masquer:
            return attendees
        self.env.cr.execute("SELECT id, event_id FROM calendar_attendee WHERE id = ANY(%s)",
                            [attendees.ids])
        par_evenement = {}
        for att_id, ev_id in self.env.cr.fetchall():
            par_evenement.setdefault(ev_id, []).append(att_id)
        niveaux = niveaux_sql(self.env, list(par_evenement))
        cibles = attendees.browse([a for ev, ids in par_evenement.items()
                                   if RANG.get(niveaux.get(ev, "details"), 2) < RANG["details"]
                                   for a in ids])
        for field in a_masquer if cibles else []:
            self.env.cache.update(cibles, field, repeat(field.convert_to_cache(False, cibles)))
        return attendees


def _pieces_restreintes(env, lignes):
    """Parmi ``[(id de pièce, id de rencontre)]``, les pièces des rencontres restreintes."""
    niveaux = niveaux_sql(env, [r for _a, r in lignes])
    return {a for a, r in lignes if RANG.get(niveaux.get(r, "details"), 2) < RANG["details"]}


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def check(self, mode, values=None):
        """Les pièces jointes d'une rencontre restreinte ne se lisent pas (second tour, B-1).

        Le cœur ne demande que la lecture de la rencontre, qui reste permise
        (masquée) au-dessous de « tous les détails ».
        """
        res = super().check(mode, values=values)
        if mode == "read" and self and not self.env.su and not _exempt(self.env):
            self.env.cr.execute(
                "SELECT id, res_id FROM ir_attachment WHERE id = ANY(%s) AND res_model = 'calendar.event'",
                [list(self.ids)])
            if _pieces_restreintes(self.env, self.env.cr.fetchall()):
                raise AccessError(_("Sorry, you are not allowed to access this document."))
        return res

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None):
        query = super()._search(domain, offset=offset, limit=limit, order=order)
        if self.env.su or _exempt(self.env):
            return query
        ids = list(query.get_result_ids()) if hasattr(query, "get_result_ids") else list(query)
        if not ids:
            return query
        self.env.cr.execute(
            "SELECT id, res_id FROM ir_attachment WHERE id = ANY(%s) AND res_model = 'calendar.event'",
            [ids])
        lignes = self.env.cr.fetchall()
        bannies = _pieces_restreintes(self.env, lignes) if lignes else set()
        if not bannies:
            return query
        return self.browse([i for i in ids if i not in bannies])._as_query(ordered=True)


class MailFollowers(models.Model):
    _inherit = "mail.followers"

    def _fetch_query(self, query, fields):
        """Les abonnés d'une rencontre restreinte ne se lisent pas par la bande (second tour, B-1)."""
        abonnes = super()._fetch_query(query, fields)
        if self.env.su or _exempt(self.env):
            return abonnes
        a_masquer = [f for f in fields if f.name in ("partner_id", "name", "email", "display_name")]
        if not a_masquer or not abonnes:
            return abonnes
        self.env.cr.execute(
            "SELECT id, res_id FROM mail_followers WHERE id = ANY(%s) AND res_model = 'calendar.event'",
            [abonnes.ids])
        lignes = self.env.cr.fetchall()
        cibles = abonnes.browse(sorted(_pieces_restreintes(self.env, lignes))) if lignes else abonnes.browse()
        for field in a_masquer if cibles else []:
            self.env.cache.update(cibles, field, repeat(field.convert_to_cache(False, cibles)))
        return abonnes

# -*- coding: utf-8 -*-
"""Liens morts et propositions à l'équipe.

Liens morts : chaque semaine, les signets d'ENTREPRISE et d'ÉQUIPE sont visités (jamais
les perso : ils sont privés, et un lien mort dans son perso ne gêne que soi). Un lien
est « mort » après DEUX vérifications ratées d'affilée : domaine disparu, connexion
impossible, 404 ou 410. Un 401 ou un 403 n'est PAS mort : c'est souvent une page de
connexion, en particulier pour un intranet. Une adresse interne n'est pas visitée du
tout (la garde des icônes la refuse), donc jamais déclarée morte. Au passage à
« mort », une activité va au responsable de la section.

Propositions : un signet perso proposé à une section d'équipe ou d'entreprise va à son
responsable, qui l'accepte (le signet est créé avec SES droits) ou la refuse.
"""
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .bookmark_favicon import FetchRefused, NotCheckable, http_get

LINK_CHECK_DAYS = 7
LINK_BATCH = 100
LINK_FAILURES_DEAD = 2
PROBE_BYTES = 16 * 1024
DEAD_STATUSES = (404, 410)


class BookmarkFolder(models.Model):
    _inherit = "bf.bookmark.folder"

    def _responsible_user(self):
        """Qui décide pour cette section : gestionnaire du département, chargé du
        projet, ou responsable des signets de la société (le créateur s'il l'est
        encore, sinon le premier). Lu en sudo : c'est une question d'aiguillage."""
        self.ensure_one()
        folder = self.sudo()
        manager_group = self.env.ref("bf_bookmarks.group_bookmark_manager")
        managers = self.env["res.users"].sudo().search([
            ("groups_id", "in", manager_group.id), ("share", "=", False)]).filtered(
            lambda u: not folder.company_id or folder.company_id in u.company_ids)
        if folder.scope == "department" and folder.department_id.manager_id.user_id:
            return folder.department_id.manager_id.user_id
        if folder.scope == "project" and folder.project_id.user_id:
            return folder.project_id.user_id
        if folder.scope == "personal":
            return folder.owner_id
        if folder.create_uid in managers:
            return folder.create_uid
        return managers.sorted("id")[:1]


class Bookmark(models.Model):
    # Les activités (lien mort) se posent sur le signet lui-même. 🔴 En Odoo 18, créer une
    # activité abonne l'assigné (`message_subscribe`) : le mixin seul ne suffit pas, il
    # faut `mail.thread`. Sans abonnement ni journal à la création, sinon chaque signet
    # créé abonnerait son auteur (une ligne d'abonné par signet importé).
    _name = "bf.bookmark"
    _inherit = ["bf.bookmark", "mail.thread", "mail.activity.mixin"]

    @api.model_create_multi
    def create(self, vals_list):
        return super(Bookmark, self.with_context(
            mail_create_nosubscribe=True, mail_create_nolog=True, tracking_disable=True,
        )).create(vals_list)

    link_state = fields.Selection([("unknown", "Non vérifié"), ("ok", "Répond"), ("dead", "Mort")],
                                  string="Lien", default="unknown", index=True, copy=False, readonly=True)
    link_checked_at = fields.Datetime(string="Vérifié le", copy=False, readonly=True)
    link_failures = fields.Integer(copy=False, readonly=True)
    link_note = fields.Char(string="Dernière vérification", copy=False, readonly=True)

    @api.model
    def _probe(self, url):
        """(vivant, note). vivant = None quand on ne peut pas savoir (adresse interne)."""
        try:
            status, _h, _b, _u = http_get(url, PROBE_BYTES, truncate=True)
        except NotCheckable as exc:
            return None, _("non vérifiée (%s)", exc)
        except FetchRefused as exc:
            return False, str(exc)
        except Exception:
            # Sans le nom de l'exception : « refusée » contre « délai dépassé » dirait
            # quels ports d'une adresse sont ouverts, à qui lit la note.
            return False, _("injoignable")
        if status in DEAD_STATUSES:
            return False, _("statut %s", status)
        return True, _("statut %s", status)

    @api.model
    def _cron_check_links(self):
        now = fields.Datetime.now()
        due = self.sudo().search([
            ("scope", "!=", "personal"),
            "|", ("link_checked_at", "=", False),
            ("link_checked_at", "<", now - timedelta(days=LINK_CHECK_DAYS)),
        ], limit=LINK_BATCH, order="link_checked_at asc nulls first, id")
        for bookmark in due:
            alive, note = self._probe(bookmark.url)
            vals = {"link_checked_at": now, "link_note": note}
            if alive is None:
                vals.update(link_state="unknown", link_failures=0)
            elif alive:
                vals.update(link_state="ok", link_failures=0)
            else:
                failures = bookmark.link_failures + 1
                vals["link_failures"] = failures
                if failures >= LINK_FAILURES_DEAD and bookmark.link_state != "dead":
                    vals["link_state"] = "dead"
                    bookmark._notify_dead_link(note)
            bookmark.write(vals)
            if not self.env.context.get("bf_bookmarks_no_commit"):
                self.env.cr.commit()
        return len(due)

    def _notify_dead_link(self, note):
        self.ensure_one()
        root = self.folder_id
        while root.parent_id:
            root = root.parent_id
        user = root._responsible_user()
        if not user:
            return
        # Sans courriel d'assignation : une tâche planifiée n'envoie rien de son chef ;
        # l'activité apparaît dans l'icône des activités d'Odoo.
        self.sudo().with_context(mail_activity_quick_update=True).activity_schedule(
            "mail.mail_activity_data_todo", user_id=user.id,
            summary=_("Lien mort : %s", self.name),
            # 🔴 Échappé : le titre et l'adresse viennent de l'usager, la note est du HTML.
            note=Markup(_("« %(name)s » (%(url)s) ne répond plus : %(note)s. Corrigez "
                          "l'adresse, ou archivez le signet.")) % {
                "name": self.name, "url": self.url, "note": note})


class BookmarkProposal(models.Model):
    _name = "bf.bookmark.proposal"
    _description = "Signet proposé à une section"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"

    name = fields.Char(string="Titre", required=True)
    url = fields.Char(string="Adresse", required=True)
    note = fields.Text(string="Pourquoi")
    bookmark_id = fields.Many2one("bf.bookmark", string="Signet d'origine", ondelete="set null")
    target_folder_id = fields.Many2one(
        "bf.bookmark.folder", string="Proposé à", required=True, ondelete="cascade",
        domain="[('scope', '!=', 'personal')]")
    proposer_id = fields.Many2one("res.users", string="Proposé par", required=True, readonly=True,
                                  default=lambda self: self.env.uid)
    responsible_id = fields.Many2one("res.users", string="Décide", readonly=True)
    state = fields.Selection([("pending", "En attente"), ("accepted", "Acceptée"),
                              ("refused", "Refusée")], default="pending", required=True,
                             readonly=True, tracking=True)
    result_bookmark_id = fields.Many2one("bf.bookmark", string="Signet créé", readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        Folder = self.env["bf.bookmark.folder"]
        for vals in vals_list:
            # FIXÉS, pas seulement retirés : une clé absente serait comblée par une valeur
            # par défaut, et chacun peut se poser les siennes (`ir.default`) par RPC.
            vals["proposer_id"] = self.env.uid
            vals["state"] = "pending"
            # 🔴 Le signet créé n'existe qu'après acceptation : fourni à la création, son
            # nom se lisait par le champ relié, quel que soit le signet perso visé.
            vals["result_bookmark_id"] = False
            vals["bookmark_id"] = vals.get("bookmark_id") or False
            target = Folder.browse(vals.get("target_folder_id"))
            # On ne propose qu'à une section qu'on VOIT, et jamais à un perso.
            if not target.exists() or not target.has_access("read") or target.scope == "personal":
                raise UserError(_("On ne peut proposer qu'à une section d'équipe ou d'entreprise visible."))
            vals["url"] = self.env["bf.bookmark"]._clean_url(vals.get("url"))
            # Le signet d'origine : seulement un signet qu'on peut lire (sinon son nom
            # s'afficherait à qui ne le voit pas).
            if vals.get("bookmark_id"):
                source = self.env["bf.bookmark"].browse(vals["bookmark_id"])
                if not source.exists() or not source.has_access("read"):
                    raise AccessError(_("Signet introuvable."))
            root = target
            while root.parent_id:
                root = root.parent_id
            vals["responsible_id"] = root._responsible_user().id
        records = super().create(vals_list)
        for proposal in records:
            if proposal.responsible_id:
                proposal.sudo().with_context(mail_activity_quick_update=True).activity_schedule(
                    "mail.mail_activity_data_todo", user_id=proposal.responsible_id.id,
                    summary=_("Signet proposé : %s", proposal.name),
                    # 🔴 Échappé : titre et adresse viennent du proposant, la note est du HTML.
                    note=Markup(_("%(who)s propose « %(name)s » (%(url)s) pour %(where)s.")) % {
                        "who": proposal.proposer_id.name, "name": proposal.name,
                        "url": proposal.url, "where": proposal.target_folder_id.complete_name})
        return records

    # Ce que seul le module change (en sudo) : un appel RPC ne peut pas se déclarer
    # « acceptée » ni se choisir un décideur. Le titre, l'adresse et la note non plus :
    # changés après la relecture du responsable, ils passeraient sans qu'il les ait vus
    # (la note devient celle du signet accepté).
    _PROTECTED = {"state", "responsible_id", "proposer_id", "result_bookmark_id",
                  "target_folder_id", "bookmark_id", "name", "url", "note"}

    def write(self, vals):
        if not self.env.su and set(vals) & self._PROTECTED:
            raise AccessError(_("Ces champs ne se modifient pas à la main."))
        return super().write(vals)

    def _check_can_decide(self):
        for proposal in self:
            if proposal.state != "pending":
                raise UserError(_("Cette proposition est déjà traitée."))
            if not proposal.target_folder_id.has_access("write"):
                raise AccessError(_("Seul un responsable de cette section peut en décider."))

    def action_accept(self):
        self._check_can_decide()
        for proposal in self:
            # Créé avec les droits de CELUI qui accepte : les règles d'écriture jouent.
            bookmark = self.env["bf.bookmark"].create({
                "name": proposal.name, "url": proposal.url,
                "folder_id": proposal.target_folder_id.id,
                "description": proposal.note,
            })
            proposal.sudo().write({"state": "accepted", "result_bookmark_id": bookmark.id})
            proposal.sudo().activity_ids.action_feedback(_("Accepté"))
        return True

    def action_refuse(self):
        self._check_can_decide()
        self.sudo().write({"state": "refused"})
        self.sudo().activity_ids.action_feedback(_("Refusé"))
        return True

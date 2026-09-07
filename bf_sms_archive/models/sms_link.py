"""Registre des rattachements : quel SMS (ou quel appel) est parti sur quelle fiche.

Avant ce modèle, poser un SMS sur une tâche était un **aller simple** : la note
partait dans le chatter et rien, côté message, ne gardait trace de sa
destination. Un même SMS qui concerne deux tâches produisait donc deux notes
étrangères l'une à l'autre, la Messagerie ne pouvait pas dire « celui-là est
déjà parti là-bas », et défaire un envoi voulait dire retrouver la note à la
main.

Le registre est **par élément et par fiche**, pas par conversation : le lien
fil ↔ tâche (``sms.archive.thread.task_ids``) répond à « de quoi parle cette
conversation », celui-ci répond à « où est parti CE message ». Les deux
coexistent, et le premier continue d'être posé au passage quand la cible est
une tâche.

La cible est un couple ``res_model``/``res_id`` plutôt qu'un ``Many2one`` vers
``project.task`` : depuis la 5.8.0 le sorcier vise n'importe quelle fiche dotée
d'un chatter (ticket, opportunité, contact), et un registre qui ne saurait
compter que les tâches perdrait de vue tout le reste.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)


class SmsArchiveLink(models.Model):
    _name = "sms.archive.link"
    _description = "Rattachement d'un SMS ou d'un appel à une fiche"
    _order = "create_date desc, id desc"

    # Les deux sources possibles. Une ligne en porte exactement une : la
    # contrainte SQL plus bas le garantit, et les deux index d'unicité
    # tolèrent le NULL de l'autre colonne (PostgreSQL ne compare pas les NULL).
    message_id = fields.Many2one(
        comodel_name="sms.archive.message",
        string="Message",
        ondelete="cascade",
        index=True,
    )
    call_id = fields.Many2one(
        comodel_name="call.archive.call",
        string="Appel",
        ondelete="cascade",
        index=True,
    )

    res_model = fields.Char(
        string="Modèle de la fiche",
        required=True,
        index=True,
    )
    res_id = fields.Integer(
        string="ID de la fiche",
        required=True,
        index=True,
    )
    # Un seul champ pour désigner la cible, et c'est délibéré : chercher
    # « les messages de la tâche 42 » avec deux conditions séparées
    # (`res_model` d'un côté, `res_id` de l'autre) sur un One2many les fait
    # porter sur DEUX lignes différentes : un message rattaché à la tâche 7 et
    # au ticket 42 ressortirait. Une clé unique évite le faux positif.
    res_ref = fields.Char(
        string="Référence",
        compute="_compute_res_ref",
        store=True,
        index=True,
    )
    res_name = fields.Char(
        string="Fiche",
        compute="_compute_res_name",
    )
    res_model_label = fields.Char(
        string="Type de fiche",
        compute="_compute_res_name",
    )

    mail_message_id = fields.Many2one(
        comodel_name="mail.message",
        string="Note publiée",
        ondelete="set null",
        index=True,
        help="La note effectivement posée dans le chatter de la fiche. "
             "Une note consolidée couvre souvent plusieurs messages : elle "
             "n'est retirée que lorsque plus aucun rattachement ne la vise.",
    )

    thread_id = fields.Many2one(
        comodel_name="sms.archive.thread",
        string="Conversation",
        compute="_compute_thread_id",
        store=True,
        index=True,
        ondelete="cascade",
    )
    # Porté par les règles d'enregistrement : sans lui, il faudrait traverser
    # deux relations à chaque lecture.
    owner_id = fields.Many2one(
        comodel_name="res.users",
        string="Propriétaire",
        related="thread_id.owner_id",
        store=True,
        index=True,
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Rattaché par",
        default=lambda self: self.env.uid,
    )
    is_auto = fields.Boolean(
        string="Suivi automatique",
        default=False,
        help="Posé par le relais automatique du fil, pas à la main.",
    )

    _sql_constraints = [
        (
            "item_required",
            "CHECK (message_id IS NOT NULL OR call_id IS NOT NULL)",
            "Un rattachement porte sur un message ou sur un appel.",
        ),
        (
            "message_target_uniq",
            "UNIQUE (message_id, res_ref)",
            "Ce message est déjà rattaché à cette fiche.",
        ),
        (
            "call_target_uniq",
            "UNIQUE (call_id, res_ref)",
            "Cet appel est déjà rattaché à cette fiche.",
        ),
    ]

    # ── Calculs ────────────────────────────────────────────────────

    @api.depends("res_model", "res_id")
    def _compute_res_ref(self):
        for link in self:
            link.res_ref = (
                f"{link.res_model},{link.res_id}"
                if link.res_model and link.res_id else False
            )

    @api.depends("message_id.thread_id", "call_id.thread_id")
    def _compute_thread_id(self):
        for link in self:
            link.thread_id = link.message_id.thread_id or link.call_id.thread_id

    @api.depends("res_model", "res_id")
    def _compute_res_name(self):
        """Nom vivant de la fiche, lu par lot et jamais figé.

        Un instantané pris au moment du rattachement vieillirait : une tâche
        renommée continuerait d'apparaître sous son ancien titre, ce qui est
        exactement le genre de détail qui fait douter du registre entier.
        """
        labels = dict(self.env["bf.chatter.target"]._thread_model_selection())
        by_model = {}
        for link in self:
            if link.res_model and link.res_id:
                by_model.setdefault(link.res_model, set()).add(link.res_id)
        names = {}
        for model, ids in by_model.items():
            if model not in self.env:
                continue
            # sudo : le registre dit où le message est parti même si le lecteur
            # n'a pas accès à la fiche. Il n'en voit que le nom, et le bouton
            # « Ouvrir » vérifie ses droits pour de vrai.
            for record in self.env[model].sudo().browse(sorted(ids)).exists():
                names[(model, record.id)] = record.display_name
        for link in self:
            key = (link.res_model, link.res_id)
            link.res_name = names.get(key) or _("Fiche supprimée")
            link.res_model_label = labels.get(link.res_model, link.res_model or "")

    @api.depends("res_name", "message_id", "call_id")
    def _compute_display_name(self):
        # Explicite plutôt que `_rec_name = "res_name"` : le nom de la fiche est
        # calculé et non stocké, et le `name_search` qu'un `_rec_name` implique
        # tomberait alors sur une colonne inexistante.
        for link in self:
            item = link.message_id.display_name or link.call_id.display_name or ""
            link.display_name = f"{item} → {link.res_name}" if item else link.res_name

    # ── Enregistrement ─────────────────────────────────────────────

    @api.model
    def _register_links(self, items, record, note=None, is_auto=False):
        """Enregistre le rattachement de `items` vers `record`. Idempotent.

        `items` est un recordset de ``sms.archive.message`` **ou** de
        ``call.archive.call``. Les deux modèles ne s'unissent pas, l'appelant
        appelle donc une fois par famille.

        Reposter une sélection déjà rattachée ne crée pas de doublon : la ligne
        existante pointe désormais vers la nouvelle note. C'est ce qui rend
        « défaire » possible sans laisser un rattachement orphelin derrière.
        """
        if not items or not record:
            return self.browse()
        field = "message_id" if items._name == "sms.archive.message" else "call_id"
        ref = f"{record._name},{record.id}"
        # sudo sur la recherche seulement : la ligne existante peut appartenir à
        # un collègue (fil partagé), et la manquer créerait un doublon que la
        # contrainte SQL refuserait ensuite en pleine figure de l'utilisateur.
        existing = self.sudo().search([(field, "in", items.ids), ("res_ref", "=", ref)])
        by_item = {link[field].id: link for link in existing}
        if note and existing:
            existing.write({"mail_message_id": note.id})
        to_create = [{
            field: item.id,
            "res_model": record._name,
            "res_id": record.id,
            "mail_message_id": note.id if note else False,
            "is_auto": is_auto,
        } for item in items if item.id not in by_item]
        created = self.create(to_create) if to_create else self.browse()
        return existing | created

    @api.model
    def _targets_for(self, items):
        """{id de l'élément: [fiches rattachées]}, en une requête pour tout un fil.

        Sert à la Messagerie, qui affiche le badge sur chaque bulle : le faire
        message par message rendrait le chargement d'une conversation
        quadratique en nombre de rattachements.
        """
        if not items:
            return {}
        field = "message_id" if items._name == "sms.archive.message" else "call_id"
        links = self.search([(field, "in", items.ids)])
        out = {}
        for link in links:
            out.setdefault(link[field].id, []).append({
                "link_id": link.id,
                "res_model": link.res_model,
                "res_id": link.res_id,
                "name": link.res_name,
                "model_label": link.res_model_label,
                "is_auto": link.is_auto,
            })
        return out

    @api.model
    def _targets_for_rpc(self, items):
        """`_targets_for` avec des clés en chaînes, pour un retour RPC.

        XML-RPC refuse une clé de dictionnaire qui n'est pas une chaîne, et les
        points d'entrée de la Messagerie sont appelés des deux côtés : en
        JSON-RPC par le navigateur, en XML-RPC par les outils."""
        return {str(k): v for k, v in self._targets_for(items).items()}

    # ── Gestes ─────────────────────────────────────────────────────

    def action_open_target(self):
        """Ouvre la fiche visée, après vérification des droits du lecteur."""
        self.ensure_one()
        if not self.res_model or self.res_model not in self.env:
            raise UserError(_("Cette fiche n'existe plus."))
        record = self.env[self.res_model].browse(self.res_id)
        if not record.exists():
            raise UserError(_("Cette fiche a été supprimée."))
        try:
            record.check_access("read")
        except AccessError as exc:
            raise UserError(_(
                "Accès refusé sur %(model)s #%(id)s : %(err)s",
                model=self.res_model, id=self.res_id, err=exc,
            )) from exc
        return {
            "type": "ir.actions.act_window",
            "res_model": self.res_model,
            "res_id": self.res_id,
            "view_mode": "form",
            "views": [[False, "form"]],
        }

    def action_undo(self):
        """Retire le rattachement, et la note du chatter quand elle n'est plus visée.

        Une note consolidée couvre souvent plusieurs messages. La supprimer dès
        le premier rattachement retiré ferait disparaître du chatter les
        messages qu'on voulait garder : elle n'est donc retirée que lorsque le
        dernier rattachement qui la vise s'en va, et seulement si le lecteur a
        le droit d'écrire sur la fiche.
        """
        # Les décisions sont prises AVANT de supprimer les lignes : après le
        # `unlink`, `link.mail_message_id` ne rend plus rien et le décompte des
        # rattachements restants serait fait sur un recordset vide.
        candidates = []
        gone_ids = set(self.ids)
        for link in self:
            note = link.mail_message_id
            if note and note.exists() and link._may_write_target(note):
                candidates.append(note)
        self.unlink()
        removed_notes = 0
        for note in candidates:
            if not note.exists():
                continue
            still_used = self.sudo().search_count([
                ("mail_message_id", "=", note.id), ("id", "not in", list(gone_ids)),
            ])
            if still_used:
                continue
            note.sudo().unlink()
            removed_notes += 1
        return removed_notes

    def _may_write_target(self, note):
        """Le lecteur peut-il modifier le chatter qui porte cette note ?"""
        model, res_id = note.model, note.res_id
        if not model or not res_id or model not in self.env:
            return False
        record = self.env[model].browse(res_id).exists()
        if not record:
            return False
        try:
            record.check_access("write")
        except AccessError:
            return False
        return True

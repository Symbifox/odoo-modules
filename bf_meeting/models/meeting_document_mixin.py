from odoo import Command, _, api, fields, models


# Champs qu'une ligne de document peut porter. Tout le reste des valeurs
# envoyées par le client est ignoré — en particulier `res_model` / `res_id`,
# qui sont posés depuis l'enregistrement parent : une commande forgée ne peut
# pas rattacher une pièce jointe ailleurs.
_DOCUMENT_FIELDS = (
    'name',
    'description',
    'type',
    'url',
    'datas',
    'mimetype',
    'bf_visibility_window',
    'bf_visible_from',
    'bf_visible_until',
)


class BfMeetingDocumentMixin(models.AbstractModel):
    """Onglet « Documents » partagé par ``meeting.agenda`` et ``meeting.record``.

    ``ir.attachment`` n'a pas de clé étrangère vers les modèles de rencontre —
    le lien est la paire ``res_model`` / ``res_id`` —, donc l'onglet est un
    One2many **calculé**. Un x2many calculé s'écrit par la branche non stockée
    de ``_RelationalMulti.write_real``, où la commande 0 se résume à
    ``comodel.new(vals)`` : une ligne qui vit en cache et meurt avec la
    requête, et la commande 2/3 à un simple retrait du cache. Rien n'atteint
    jamais la base tout seul.

    Les commandes sont donc interceptées dans ``create`` et ``write`` et
    appliquées directement à ``ir.attachment``. L'``inverse`` du champ n'est
    conservé que pour ce qu'il implique — un champ calculé sans inverse est
    ``readonly``, et le client web n'enverrait alors aucune commande.
    """

    _name = 'bf.meeting.document.mixin'
    _description = 'Documents de rencontre (socle)'

    meeting_attachment_ids = fields.One2many(
        'ir.attachment',
        compute='_compute_meeting_attachment_ids',
        inverse='_inverse_meeting_attachment_ids',
        string='Documents',
    )

    def _compute_meeting_attachment_ids(self):
        for rec in self:
            rec.meeting_attachment_ids = rec._bf_linked_attachments()

    def _inverse_meeting_attachment_ids(self):
        """Volontairement vide — voir la docstring de la classe.

        Tout ce qui persiste passe par ``_bf_apply_document_commands``, appelé
        depuis ``create`` / ``write``. Un inverse ne reçoit que la valeur du
        champ, donc des lignes ``NewId`` dont les valeurs ne survivent pas à
        une invalidation du cache ; s'y fier perdait le contenu des documents
        et supprimait ceux d'une écriture précédente dans la même transaction.
        """

    def _bf_linked_attachments(self):
        """Pièces jointes rattachées à cet enregistrement, **telles que
        l'utilisateur les voit**.

        Volontairement sans ``sudo`` : ``ir.attachment._search`` restreint le
        résultat à la fenêtre de visibilité (voir ``models/ir_attachment.py``),
        et le retrait ci-dessous ne porte jamais que sur ce que cette même
        recherche a rendu. Un participant qui ne voit pas un pre-read hors
        fenêtre ne peut donc pas l'effacer sans le savoir.
        """
        self.ensure_one()
        record_id = self._origin.id
        if not record_id:
            return self.env['ir.attachment']
        return self.env['ir.attachment'].search([
            ('res_model', '=', self._name),
            ('res_id', '=', record_id),
        ])

    @api.model_create_multi
    def create(self, vals_list):
        # Copie avant de retirer la clé : `create` ne doit pas modifier le
        # dictionnaire de l'appelant, qui le relit parfois après coup.
        vals_list = [dict(vals) for vals in vals_list]
        differes = [vals.pop('meeting_attachment_ids', None) for vals in vals_list]
        records = super().create(vals_list)
        for rec, commandes in zip(records, differes):
            if commandes:
                rec._bf_apply_document_commands(commandes)
        return records

    def write(self, vals):
        commandes = None
        if vals and 'meeting_attachment_ids' in vals:
            vals = dict(vals)
            commandes = vals.pop('meeting_attachment_ids')
        result = super().write(vals)
        if commandes:
            for rec in self:
                rec._bf_apply_document_commands(commandes)
        return result

    def _bf_apply_document_commands(self, commandes):
        """Appliquer les commandes x2many du client aux pièces jointes."""
        self.ensure_one()
        Attachment = self.env['ir.attachment']
        visibles = self._bf_linked_attachments()
        gardees = set(visibles.ids)
        creees = Attachment.browse()

        for commande in commandes or ():
            if not commande:
                continue
            code = commande[0]
            if code == Command.CREATE:
                creees |= Attachment.create(self._bf_document_vals(commande[2]))
            elif code == Command.UPDATE:
                cible = Attachment.browse(commande[1])
                changements = self._bf_document_vals(commande[2], update=True)
                if changements:
                    cible.write(changements)
                    # Parité avec l'onchange de l'interface : changer la
                    # fenêtre sans donner de bornes les redérive de la date
                    # de la rencontre.
                    if 'bf_visibility_window' in changements and not (
                            'bf_visible_from' in changements
                            or 'bf_visible_until' in changements):
                        cible._bf_apply_visibility_window()
            elif code in (Command.DELETE, Command.UNLINK):
                gardees.discard(commande[1])
            elif code == Command.LINK:
                gardees.add(commande[1])
            elif code == Command.CLEAR:
                gardees.clear()
            elif code == Command.SET:
                gardees = set(commande[2] or ())

        # Une ligne retirée de la liste veut dire que le document disparaît :
        # la pièce jointe n'a pas d'autre ancrage que cet enregistrement.
        retirees = visibles.filtered(lambda a: a.id not in gardees)
        if retirees:
            retirees.unlink()

        if creees:
            # La fenêtre de visibilité se calcule à partir de la date de la
            # rencontre, que l'onchange ne pouvait pas atteindre depuis une
            # ligne encore dépourvue de `res_model` / `res_id`.
            creees._bf_apply_visibility_window()

    def _bf_document_vals(self, valeurs, update=False):
        """Valeurs d'``ir.attachment`` tirées d'une commande du client."""
        self.ensure_one()
        vals = {k: v for k, v in (valeurs or {}).items() if k in _DOCUMENT_FIELDS}
        if vals.get('url'):
            vals['type'] = 'url'
            vals.pop('datas', None)
        elif vals.get('datas'):
            vals['type'] = 'binary'
        if update:
            return vals
        vals['name'] = (vals.get('name') or '').strip() or _('Document sans nom')
        vals['res_model'] = self._name
        vals['res_id'] = self.id
        return vals

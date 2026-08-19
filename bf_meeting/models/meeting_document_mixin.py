from odoo import _, fields, models


# Champs qu'une ligne de document peut porter avant d'exister comme
# `ir.attachment` réel. Tout le reste est ignoré : la ligne est un
# enregistrement NewId qui transporte ce que le client web a envoyé.
_NEW_DOCUMENT_FIELDS = (
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
    de ``_RelationalMulti.write_real`` : la commande 0 s'y résume à
    ``comodel.new(vals)``, c'est-à-dire une ligne qui vit en cache et meurt
    avec la requête, et la commande 2/3 à un retrait du cache. Matérialiser
    l'une et exécuter l'autre est le travail de l'``inverse``, qui était un
    ``pass`` jusqu'à la 18.0.3.47.0 : tout document ajouté depuis l'onglet
    disparaissait à l'enregistrement, sans un mot.
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

    def _bf_linked_attachments(self):
        """Pièces jointes rattachées à cet enregistrement, **telles que
        l'utilisateur les voit**.

        Volontairement sans ``sudo`` : ``ir.attachment._search`` restreint le
        résultat à la fenêtre de visibilité (voir ``models/ir_attachment.py``),
        et l'inverse ci-dessous ne supprime jamais que ce que cette même
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

    def _inverse_meeting_attachment_ids(self):
        Attachment = self.env['ir.attachment']
        for rec in self:
            if not rec.id or isinstance(rec.id, models.NewId):
                # Rien à rattacher tant que la rencontre n'a pas d'id : le
                # `res_id` n'existerait pas. Les lignes seront rejouées au
                # premier enregistrement.
                continue

            lines = rec.meeting_attachment_ids
            # Les lignes déjà en base (id entier) par opposition aux lignes
            # que le client vient de créer en cache (id NewId).
            kept = lines.browse([lid for lid in lines._ids if isinstance(lid, int)])

            # Retraits — calculés AVANT les créations, sinon les pièces
            # jointes qu'on vient de créer passeraient pour des lignes
            # retirées et seraient supprimées dans la foulée.
            dropped = rec._bf_linked_attachments() - kept
            if dropped:
                dropped.unlink()

            created = Attachment.browse()
            for line in lines - kept:
                created |= Attachment.create(rec._bf_document_vals(line))
            if created:
                # La fenêtre de visibilité se calcule à partir de la date de la
                # rencontre, que l'onchange ne pouvait pas atteindre depuis une
                # ligne encore dépourvue de `res_model` / `res_id`.
                created._bf_apply_visibility_window()

    def _bf_document_vals(self, line):
        """Valeurs d'``ir.attachment`` tirées d'une ligne encore en cache."""
        self.ensure_one()
        cache = self.env.cache
        vals = {}
        for fname in _NEW_DOCUMENT_FIELDS:
            field = line._fields.get(fname)
            if field is not None and cache.contains(line, field):
                vals[fname] = field.convert_to_write(line[fname], line)

        vals['name'] = (vals.get('name') or '').strip() or _('Document sans nom')
        if vals.get('url'):
            vals['type'] = 'url'
            vals.pop('datas', None)
        elif vals.get('datas'):
            vals['type'] = 'binary'
        vals['res_model'] = self._name
        vals['res_id'] = self.id
        return vals

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
    'bf_meeting_document',
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

    # Fenêtres de visibilité dont les documents partent aux participants avec
    # le courriel et le PDF. Chaque modèle donne les siennes.
    _bf_shared_windows = ('always', 'custom')

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
        # Coché par défaut : un document ajouté à l'onglet est fait pour
        # partir. Décoché à la main, il reste dans l'onglet sans partir.
        # Le client web envoie toutes les colonnes d'une ligne neuve, la case
        # comprise : c'est le contexte de l'onglet (`default_…` dans les deux
        # vues) qui la coche à l'écran ; ce `setdefault` vaut pour l'API.
        vals.setdefault('bf_meeting_document', True)
        return vals

    def _bf_shared_documents(self, at_now=True):
        """Documents de l'onglet que les participants reçoivent maintenant.

        Trois filtres, dans cet ordre :

        * la pièce vient de l'onglet (``bf_meeting_document``) — pas du
          chatter, ni de la copie d'échange, ni d'un champ texte ;
        * sa fenêtre convient à ce qu'on envoie (``_bf_shared_windows`` :
          avant la rencontre pour l'OdJ, après pour le compte rendu ; une
          fenêtre « pendant » ne part jamais) ;
        * elle est visible à cet instant : un pre-read échu ne part plus avec
          un renvoi. ``at_now=False`` lève ce dernier filtre : le repère
          d'envoi (`agenda_diff`) ne doit pas voir un document « retiré »
          du seul fait que l'heure de la rencontre est passée.

        En ``sudo`` : le rendu se fait sous l'identité de l'expéditeur, ou
        d'une tâche planifiée, et ne doit pas dépendre de ce qu'il voit.
        """
        self.ensure_one()
        Attachment = self.env['ir.attachment'].sudo()
        record_id = self._origin.id
        if not record_id:
            return Attachment
        attachments = Attachment.search([
            ('res_model', '=', self._name),
            ('res_id', '=', record_id),
            ('bf_meeting_document', '=', True),
            ('bf_visibility_window', 'in', list(self._bf_shared_windows)),
        ], order='id')
        return attachments.filtered(
            lambda a: (a.bf_is_visible_now or not at_now) and (
                (a.type == 'binary' and a.file_size) or _bf_is_web_url(a.url)))

    def _bf_shared_document_links(self):
        """Les documents à lister dans le courriel et le PDF : nom et lien.

        Un lien URL part tel quel. Un fichier téléversé part par un lien à
        jeton (``/web/content`` + ``access_token``) : un destinataire externe
        n'a pas de session, et c'est le jeton qui lui ouvre la pièce. Le
        jeton se crée au premier rendu et ne change plus ensuite : un renvoi
        donne le même lien.
        """
        self.ensure_one()
        base = self.get_base_url()
        links = []
        for attachment in self._bf_shared_documents():
            if attachment.type == 'url':
                url = attachment.url.strip()
            else:
                token = attachment.generate_access_token()[0]
                url = '%s/web/content/%s?access_token=%s&download=true' % (
                    base, attachment.id, token)
            links.append({'name': attachment.name, 'url': url})
        return links


def _bf_is_web_url(url):
    """Un lien qu'on peut mettre dans un courriel : http ou https, rien d'autre."""
    url = (url or '').strip().lower()
    return url.startswith('https://') or url.startswith('http://')

"""Reprendre un compte rendu exporté par un autre locataire Symbifox."""

import base64
import logging

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.meeting_exchange import MAX_FILE_BYTES

_logger = logging.getLogger(__name__)


class MeetingExchangeImportWizard(models.TransientModel):
    """Assistant d'import : lire le fichier, montrer ce qu'il contient, créer.

    Le fichier arrive par courriel : c'est de l'entrée non fiable, même quand
    l'expéditeur l'est. L'assistant valide avant de montrer, montre avant de
    créer, et ne crée jamais autre chose qu'un compte rendu : ni contact, ni
    tâche, ni projet.
    """

    _name = 'meeting.exchange.import.wizard'
    _description = "Importer un compte rendu d'un autre locataire"

    file = fields.Binary(string='Fichier .json', required=True, attachment=False)
    filename = fields.Char(string='Nom du fichier')
    project_id = fields.Many2one(
        'project.project',
        string='Projet',
        help="Facultatif. Le projet d'origine ne voyage qu'en texte : les "
             "identifiants d'une autre base ne veulent rien dire ici.",
    )
    preview_html = fields.Html(string='Contenu', readonly=True, sanitize=False)
    state = fields.Selection([
        ('upload', 'Fichier'),
        ('preview', 'Vérification'),
    ], default='upload')
    duplicate_id = fields.Many2one('meeting.record', string='Déjà importé', readonly=True)

    def _parse(self):
        self.ensure_one()
        if not self.file:
            raise UserError(_("Choisissez un fichier."))
        # Le plafond se lit sur le base64, AVANT de décoder : `parse_payload`
        # le vérifie aussi, mais trop tard pour empêcher un envoi de cent mégas
        # d'être décodé en mémoire d'abord. Le base64 pèse 4/3 de l'original.
        if len(self.file) > (MAX_FILE_BYTES * 4) // 3 + 1024:
            raise UserError(_(
                "Le fichier dépasse la limite de %s ko.", MAX_FILE_BYTES // 1024))
        try:
            raw = base64.b64decode(self.file)
        except Exception as err:  # noqa: BLE001 - le contenu vient du navigateur
            raise UserError(_("Le fichier n'a pas pu être lu.")) from err
        return self.env['meeting.exchange'].parse_payload(raw)

    def action_check(self):
        """Valider le fichier et montrer ce qu'il contient, sans rien créer."""
        self.ensure_one()
        parsed = self._parse()
        meeting = parsed['meeting']
        ref = self.env['meeting.exchange'].source_ref(parsed)
        duplicate = self.env['meeting.record'].search(
            [('exchange_source_ref', '=', ref)], limit=1)

        source = parsed['source']
        lines = [
            (_('Provenance'), '%s (%s)' % (
                source['company'] or _('organisation non déclarée'),
                source['db'] or _('base non déclarée'))),
            (_('Titre'), meeting['name'] or meeting['room_name'] or '(sans titre)'),
            (_('Date'), meeting['date'] or '(sans date)'),
            (_('Participants'), str(len(meeting['roster']))),
            (_('Sujets'), str(len(meeting['topics']))),
            (_('Décisions'), str(len(meeting['decisions']))),
            (_("Éléments d'action"), str(len(meeting['action_items']))),
            (_('Questions ouvertes'), str(len(meeting['open_questions']))),
        ]
        html = Markup('<ul>%s</ul>') % Markup('').join(
            Markup('<li><strong>%s :</strong> %s</li>') % (label, value)
            for label, value in lines
        )
        # Compté par la MÊME boucle que l'import : un aperçu qui annonce un
        # autre nombre que ce qui se produit ensuite ne rassure personne.
        _presences, non_apparies, _matched = self.env['meeting.exchange']._split_roster(
            meeting['roster'])
        unmatched = len(non_apparies)
        if unmatched > 0:
            html += Markup(
                '<p>⚠️ %s participant(s) sur %s n\'ont pas de fiche contact ici. '
                'Ils seront nommés dans le compte rendu, sans création de fiche.</p>'
            ) % (unmatched, len(meeting['roster']))
        if meeting['action_items']:
            html += Markup(
                "<p>Les %s élément(s) d'action sont repris comme texte : "
                "aucune tâche ne sera créée.</p>") % len(meeting['action_items'])
        for warning in parsed['warnings']:
            html += Markup('<p>⚠️ %s</p>') % escape(warning)
        if duplicate:
            html += Markup(
                '<p>🔴 Ce compte rendu a déjà été importé (%s). Importer de '
                'nouveau créera un doublon.</p>') % escape(duplicate.display_name)

        self.write({
            'preview_html': html,
            'state': 'preview',
            'duplicate_id': duplicate.id if duplicate else False,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }

    def action_import(self):
        """Créer le compte rendu. Rien d'autre."""
        self.ensure_one()
        parsed = self._parse()
        record = self.env['meeting.exchange'].apply_payload(
            parsed, project=self.project_id or None)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Compte rendu importé'),
            'res_model': 'meeting.record',
            'res_id': record.id,
            'view_mode': 'form',
        }

    @api.onchange('file')
    def _onchange_file_reset(self):
        """Changer de fichier remet la vérification à zéro : sans ça,
        l'aperçu affiché décrirait le fichier précédent."""
        for wizard in self:
            wizard.state = 'upload'
            wizard.preview_html = False
            wizard.duplicate_id = False

import re

from odoo import api, fields, models
from odoo.exceptions import UserError


def _next_version_number(number):
    """« 1.0 » → « 1.1 », « 2025.11 » → « 2025.12 »; sinon rien à proposer."""
    match = re.fullmatch(r'\s*(\d+)\.(\d+)\s*', number or '')
    if not match:
        return ''
    return '%s.%s' % (match.group(1), int(match.group(2)) + 1)


class ProjectDocumentReleaseWizard(models.TransientModel):
    """Publier une version depuis la fiche du document.

    Tant qu'aucune version n'est publiée, ``current_version`` reste vide et le
    PDF affiche « Brouillon (non publié) », même si le document est actif.
    « Activer » passe donc par cet assistant quand le document n'a encore
    aucune version publiée.
    """
    _name = 'project.document.release.wizard'
    _description = 'Publier une version de document'

    document_id = fields.Many2one(
        'project.document',
        string='Document',
        required=True,
        ondelete='cascade',
    )
    activate = fields.Boolean(
        string='Activer le document',
        help="Coché quand l'assistant est ouvert par « Activer ».",
    )
    pending_version_id = fields.Many2one(
        'project.document.version',
        string='Version en préparation',
        domain="[('document_id', '=', document_id), "
               "('state', 'in', ('draft', 'review', 'approved'))]",
        help="Version déjà créée mais pas encore publiée. Laisser vide pour "
             "en créer une nouvelle.",
    )
    version_number = fields.Char(string='Version', required=True)
    change_type = fields.Selection(
        selection=lambda self: self.env['project.document.version']
        ._fields['change_type'].selection,
        string='Type de modification',
        default='minor',
    )
    change_summary = fields.Char(string='Résumé des modifications')
    effective_date = fields.Date(
        string="Date d'entrée en vigueur",
        default=fields.Date.context_today,
    )

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        document = self.env['project.document'].browse(
            vals.get('document_id') or self.env.context.get('default_document_id')
        )
        if not document:
            return vals
        pending = document.version_ids.filtered(
            lambda v: v.state in ('draft', 'review', 'approved')
        ).sorted('id', reverse=True)[:1]
        published = document.version_ids.filtered(
            lambda v: v.state in ('released', 'superseded')
        ).sorted(lambda v: (v.sequence_index or 0, v.id), reverse=True)[:1]
        if pending:
            vals['pending_version_id'] = pending.id
            vals['version_number'] = pending.version_number
            vals['change_type'] = pending.change_type
            vals['change_summary'] = pending.change_summary
        elif published:
            vals['version_number'] = _next_version_number(published.version_number)
        else:
            vals['version_number'] = '1.0'
            vals['change_type'] = 'major'
            vals['change_summary'] = 'Première publication'
        return vals

    @api.onchange('pending_version_id')
    def _onchange_pending_version_id(self):
        if self.pending_version_id:
            self.version_number = self.pending_version_id.version_number
            self.change_type = self.pending_version_id.change_type
            self.change_summary = self.pending_version_id.change_summary

    def action_release(self):
        """Publie la version (et active le document si demandé)."""
        self.ensure_one()
        document = self.document_id
        number = (self.version_number or '').strip()
        if not number:
            raise UserError("Indiquez un numéro de version.")
        taken = document.version_ids.filtered(
            lambda v: v.version_number == number and v != self.pending_version_id
        )
        if taken:
            raise UserError(
                "La version %s existe déjà pour ce document." % number
            )
        vals = {
            'version_number': number,
            'change_type': self.change_type,
            'change_summary': self.change_summary,
            'effective_date': self.effective_date,
        }
        if self.pending_version_id:
            version = self.pending_version_id
            version.write(vals)
        else:
            version = self.env['project.document.version'].create(
                dict(vals, document_id=document.id)
            )
        version.action_release()
        if self.activate and document.state == 'draft':
            document.write({'state': 'active'})
        return {'type': 'ir.actions.act_window_close'}

    def action_activate_only(self):
        """Active le document sans publier de version."""
        self.ensure_one()
        self.document_id.write({'state': 'active'})
        return {'type': 'ir.actions.act_window_close'}

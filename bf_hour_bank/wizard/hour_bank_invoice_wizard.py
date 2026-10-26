import base64
from collections import defaultdict

from markupsafe import Markup

from odoo import api, fields, models, Command, _
from odoo.exceptions import UserError
from odoo.tools.misc import format_date, formatLang


class HourBankInvoiceWizard(models.TransientModel):
    """Facture brouillon fabriquée depuis la banque d'heures.

    Jamais validée ni envoyée ici : la banque ne crédite qu'une facture validée,
    et c'est à la personne qui facture de la relire avant."""
    _name = 'hour.bank.invoice.wizard'
    _description = "Génération d'une facture depuis la banque d'heures"

    bank_id = fields.Many2one(
        'hour.bank.client', required=True, string="Banque d'heures",
    )
    company_id = fields.Many2one(related='bank_id.company_id')
    currency_id = fields.Many2one(related='company_id.currency_id')
    # Pour les domaines de la vue.
    product_filter_mode = fields.Selection(related='bank_id.product_filter_mode')
    filter_product_ids = fields.Many2many(related='bank_id.filter_product_ids')
    invoice_partner_ids = fields.Many2many(related='bank_id.invoice_partner_ids')
    bank_commercial_partner_id = fields.Many2one(
        related='bank_id.partner_id.commercial_partner_id',
    )

    invoice_mode = fields.Selection(
        [('postpaid', 'Postpayé : heures consommées'),
         ('prepaid', "Prépayé : tranche d'heures")],
        string="Mode", compute='_compute_from_bank', store=True, readonly=False, precompute=True, required=True,
    )
    date_cutoff = fields.Date(
        string="Heures portées jusqu'au", default=fields.Date.context_today, required=True,
    )
    block_hours = fields.Float(
        string="Tranche (h)", compute='_compute_from_bank', store=True, readonly=False, precompute=True,
    )
    split_by_project = fields.Boolean(
        string="Ventiler par projet", compute='_compute_from_bank', store=True, readonly=False, precompute=True,
    )
    product_id = fields.Many2one(
        'product.product', string="Produit",
        compute='_compute_from_bank', store=True, readonly=False, precompute=True,
    )
    partner_id = fields.Many2one(
        'res.partner', string="Facturer à",
        compute='_compute_from_bank', store=True, readonly=False, precompute=True,
    )
    price_unit = fields.Float(
        string="Taux horaire", digits='Product Price',
        compute='_compute_price_unit', store=True, readonly=False, precompute=True,
    )

    uncovered_hours = fields.Float(
        string="Non couvertes à la coupe (h)", compute='_compute_postpaid_hours',
    )
    draft_hours = fields.Float(
        string="Déjà en brouillon (h)", compute='_compute_postpaid_hours',
    )
    draft_move_ids = fields.Many2many(
        'account.move', string="Brouillons", compute='_compute_postpaid_hours',
    )

    line_ids = fields.One2many(
        'hour.bank.invoice.wizard.line', 'wizard_id', string="Lignes",
        compute='_compute_line_ids', store=True, readonly=False,
    )
    total_hours = fields.Float(string="Total (h)", compute='_compute_totals')
    amount_untaxed = fields.Monetary(string="Montant avant taxes", compute='_compute_totals')

    # ------------------------------------------------------------------
    # Valeurs proposées
    # ------------------------------------------------------------------

    @api.depends('bank_id')
    def _compute_from_bank(self):
        for wiz in self:
            bank = wiz.bank_id
            if not bank:
                wiz.update({'invoice_mode': 'postpaid', 'block_hours': 0.0,
                            'split_by_project': False, 'product_id': False,
                            'partner_id': False})
                continue
            last = bank._bf_last_credit_line()
            wiz.invoice_mode = bank.invoice_mode
            wiz.block_hours = bank.invoice_block_hours
            wiz.split_by_project = bank.invoice_split_by_project
            wiz.product_id = bank.invoice_product_id or last.product_id.filtered(
                bank._bf_product_credits)
            # Le contact de la dernière facture comptée (un client peut être
            # facturé à un contact enfant, pas à la société), sinon le premier partenaire de
            # facturation, sinon le client.
            wiz.partner_id = (
                last.move_id.partner_id.filtered(bank._bf_partner_credits)
                or bank.invoice_partner_ids[:1]
                or bank.partner_id
            )

    @api.depends('product_id')
    def _compute_price_unit(self):
        for wiz in self:
            if wiz.bank_id.invoice_hourly_rate:
                wiz.price_unit = wiz.bank_id.invoice_hourly_rate
            elif wiz.product_id:
                wiz.price_unit = wiz.bank_id._bf_last_credit_line(wiz.product_id).price_unit
            else:
                wiz.price_unit = 0.0

    @api.depends('bank_id', 'date_cutoff')
    def _compute_postpaid_hours(self):
        for wiz in self:
            bank = wiz.bank_id
            if not bank or not wiz.date_cutoff:
                wiz.uncovered_hours = wiz.draft_hours = 0.0
                wiz.draft_move_ids = False
                continue
            drafts = bank._bf_draft_credit_lines()
            wiz.draft_hours = sum(drafts.mapped('quantity'))
            wiz.draft_move_ids = drafts.move_id
            wiz.uncovered_hours = sum(
                d['hours'] for d in bank._bf_uncovered_debits(wiz.date_cutoff)
            )

    # Ni product_id ni le taux : changer de produit n'efface pas les lignes
    # corrigées à la main, le nom du produit est ajouté à la création.
    @api.depends('bank_id', 'invoice_mode', 'date_cutoff', 'block_hours',
                 'split_by_project', 'partner_id')
    def _compute_line_ids(self):
        for wiz in self:
            wiz.line_ids = [Command.clear()] + [
                Command.create(vals) for vals in wiz._bf_line_values()
            ]

    @api.depends('line_ids.quantity', 'price_unit')
    def _compute_totals(self):
        for wiz in self:
            wiz.total_hours = sum(wiz.line_ids.mapped('quantity'))
            wiz.amount_untaxed = wiz.total_hours * wiz.price_unit

    def _bf_lang(self):
        return self.partner_id.lang or self.env.lang

    def _bf_line_values(self):
        """Lignes proposées, libellées dans la langue du contact facturé."""
        self.ensure_one()
        bank = self.bank_id
        if not bank:
            return []
        lang = self._bf_lang()
        env = self.with_context(lang=lang).env

        if self.invoice_mode == 'prepaid':
            if self.block_hours <= 0:
                return []
            hours = formatLang(env, self.block_hours,
                               digits=0 if self.block_hours.is_integer() else 2)
            return [{
                'sequence': 10,
                'name': env._("Tranche de %(hours)s h", hours=hours),
                'quantity': self.block_hours,
            }]

        if not self.date_cutoff:
            return []
        drafts = bank._bf_draft_credit_lines()
        uncovered = bank._bf_uncovered_debits(
            self.date_cutoff, extra_credit=sum(drafts.mapped('quantity')),
        )
        if not uncovered:
            return []
        total = round(sum(d['hours'] for d in uncovered), 2)
        period = {
            'start': format_date(env, uncovered[0]['date'], lang_code=lang),
            'end': format_date(env, self.date_cutoff, lang_code=lang),
        }
        if not self.split_by_project:
            return [{
                'sequence': 10,
                'name': env._("Du %(start)s au %(end)s\n"
                              "Détail des heures : voir le rapport de banque d'heures joint.",
                              **period),
                'quantity': total,
            }]

        by_project = defaultdict(float)
        for debit in uncovered:
            by_project[debit['project_id']] += debit['hours']
        values = []
        for project_id, hours in sorted(by_project.items(), key=lambda kv: -kv[1]):
            # sudo : le projet peut être privé pour la personne qui facture.
            project = self.env['project.project'].sudo().browse(project_id) if project_id else False
            label = (project.with_context(lang=lang).name if project
                     else env._("Ajustements"))
            values.append({
                'project_id': project_id or False,
                'name': env._("%(project)s — du %(start)s au %(end)s", project=label, **period),
                'quantity': round(hours, 2),
            })
        # Le reste d'arrondi va à la plus grosse ligne : le total ventilé égale
        # la ligne unique, et un projet arrondi à zéro ne fait pas de ligne vide.
        values[0]['quantity'] = round(values[0]['quantity'] + total
                                      - sum(v['quantity'] for v in values), 2)
        values = [v for v in values if v['quantity'] > 0]
        for seq, vals in enumerate(values, 1):
            vals['sequence'] = seq * 10
        return values

    # ------------------------------------------------------------------
    # Création
    # ------------------------------------------------------------------

    def _bf_check(self, lines):
        self.ensure_one()
        bank = self.bank_id
        company = bank.company_id or self.env.company
        if not self.product_id:
            raise UserError(_("Choisissez le produit à facturer."))
        if not bank._bf_product_credits(self.product_id):
            raise UserError(_(
                "Le produit « %(product)s » n'est pas compté par le filtre produits de "
                "cette banque : une fois validée, la facture ne réduirait pas le solde. "
                "Choisissez un produit du filtre, ou ajoutez-le au filtre de la banque.",
                product=self.product_id.display_name,
            ))
        if not self.partner_id:
            raise UserError(_("Choisissez à qui facturer."))
        if not bank._bf_partner_credits(self.partner_id):
            raise UserError(_(
                "Une facture adressée à « %(partner)s » ne serait pas comptée par cette "
                "banque (filtre partenaires, ou autre client). Choisissez un partenaire "
                "de facturation de la banque.",
                partner=self.partner_id.display_name,
            ))
        if not bank._bf_company_credits(company):
            raise UserError(_(
                "Le filtre sociétés de cette banque exclut %(company)s : la facture "
                "ne serait pas comptée.", company=company.name,
            ))
        if self.price_unit <= 0:
            raise UserError(_("Indiquez le taux horaire."))
        if not lines:
            raise UserError(_(
                "Rien à facturer : aucune heure non couverte à la date de coupe, "
                "une fois déduites les heures déjà en brouillon."
            ))

    def action_create_invoice(self):
        self.ensure_one()
        bank = self.bank_id
        company = bank.company_id or self.env.company
        lines = self.line_ids.filtered(lambda l: l.quantity > 0)
        self._bf_check(lines)
        # Relevé AVANT la création : relu après, le compte inclurait le
        # brouillon qu'on fabrique ici.
        draft_hours = sum(bank._bf_draft_credit_lines().mapped('quantity'))

        # Taxes et compte de la dernière ligne comptée sur ce produit, comme le
        # ferait une facturation faite à la main : ils varient d'un client à l'autre.
        last = bank._bf_last_credit_line(self.product_id)
        copied, move_vals = {}, {}
        if last and last.company_id == company:
            copied = {'tax_ids': [Command.set(last.tax_ids.ids)],
                      'account_id': last.account_id.id}
            # Le journal et la devise vont avec le taux repris : un taux en
            # dollars US ne doit pas tomber sur un brouillon en dollars CA.
            move_vals = {'journal_id': last.move_id.journal_id.id,
                         'currency_id': last.move_id.currency_id.id}
        product_name = self.product_id.with_context(lang=self._bf_lang()).name
        move = self.env['account.move'].with_company(company).create({
            'move_type': 'out_invoice',
            'partner_id': self.partner_id.id,
            **move_vals,
            'invoice_line_ids': [Command.create({
                'sequence': line.sequence,
                'product_id': self.product_id.id,
                'name': "%s — %s" % (product_name, line.name),
                'quantity': line.quantity,
                'price_unit': self.price_unit,
                **copied,
            }) for line in lines.sorted('sequence')],
        })
        # Dernier mot à la banque elle-même : chaque ligne du brouillon doit être
        # une ligne qu'elle comptera une fois la facture validée (société réelle
        # du journal retenu, filtres). Sinon tout est annulé.
        counted = self.env['account.move.line'].sudo().search_count(
            bank._bf_credit_line_domain(state='draft') + [('move_id', '=', move.id)])
        if counted != len(move.invoice_line_ids):
            raise UserError(_(
                "La facture créée ne serait pas comptée par cette banque une fois "
                "validée (société %(company)s, produit, contact ou filtres). Rien n'a "
                "été créé ; vérifiez la configuration de la banque.",
                company=move.company_id.name,
            ))

        pdf = bank._get_pdf_binary()
        attachment = self.env['ir.attachment'].create({
            'name': "Banque_heures_%s_%s.pdf" % (
                bank.partner_id.name.replace(' ', '_'), self.date_cutoff.isoformat()),
            'type': 'binary',
            'datas': base64.b64encode(pdf),
            'mimetype': 'application/pdf',
            'res_model': 'account.move',
            'res_id': move.id,
        })
        # no_new_invoice : sans lui, Odoo prend le PDF posté sur un brouillon
        # pour une facture à importer (EDI, OCR) et le dit au chatter.
        move.with_context(no_new_invoice=True).message_post(
            body=Markup(_("Brouillon généré depuis la banque d'heures %(bank)s. "
                          "Rapport de la banque joint.")) % {'bank': bank._get_html_link()},
            message_type='comment',
            subtype_xmlid='mail.mt_note',
            attachment_ids=[attachment.id],
        )

        total = sum(lines.mapped('quantity'))
        if self.invoice_mode == 'prepaid':
            detail = _("tranche prépayée")
        else:
            detail = _("heures portées jusqu'au %(cutoff)s",
                       cutoff=format_date(self.env, self.date_cutoff))
            if draft_hours:
                detail += _(", moins %(hours)s h déjà en brouillon",
                            hours=formatLang(self.env, draft_hours))
        # La trace se pose en sudo : facturer ne demande pas le droit de
        # modifier la banque (réservé aux gestionnaires de projet).
        bank.sudo().message_post(
            # Le lien porte le nom du brouillon (« Facture brouillon »).
            body=Markup(_("%(move)s générée depuis la banque : %(hours)s h à %(rate)s "
                          "(%(product)s), %(detail)s.")) % {
                'move': move._get_html_link(),
                'hours': formatLang(self.env, total),
                'rate': formatLang(self.env, self.price_unit, currency_obj=move.currency_id),
                'product': self.product_id.display_name,
                'detail': detail,
            },
            author_id=self.env.user.partner_id.id,
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': move.id,
            'views': [[False, 'form']],
            'target': 'current',
            'context': {'default_move_type': 'out_invoice'},
        }


class HourBankInvoiceWizardLine(models.TransientModel):
    _name = 'hour.bank.invoice.wizard.line'
    _description = "Ligne proposée pour la facture de la banque d'heures"
    _order = 'sequence, id'

    wizard_id = fields.Many2one('hour.bank.invoice.wizard', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    project_id = fields.Many2one('project.project', string="Projet")
    name = fields.Text(string="Libellé", required=True)
    quantity = fields.Float(string="Heures", digits='Product Unit of Measure')

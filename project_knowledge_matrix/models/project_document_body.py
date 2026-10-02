import base64

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .document_section import body_hash, is_blank_html

# États d'une version qui n'est pas encore figée : son texte est le corps
# vivant du document, et il peut encore changer.
PENDING_STATES = ('draft', 'review', 'approved')

DRAFT_WATERMARK = 'BROUILLON, NON APPROUVÉ'


def watermark_uri(text):
    """Filigrane en tuile SVG, pour le fond de la page imprimée.

    Un élément ``position: fixed`` ne se répète pas d'une page à l'autre avec
    le wkhtmltopdf d'Odoo (mesuré : seule la page 1 le portait). Un fond en
    mosaïque couvre toutes les pages, quelle que soit la longueur du document.
    """
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="520" height="360">'
        '<text x="260" y="190" text-anchor="middle" '
        'font-family="Lexend, Helvetica, Arial, sans-serif" font-size="30" '
        'font-weight="700" fill="#dc3545" fill-opacity="0.13" '
        'transform="rotate(-30 260 180)">%s</text></svg>'
    ) % escape(text)
    return 'data:image/svg+xml;base64,%s' % base64.b64encode(
        svg.encode('utf-8')).decode('ascii')


def version_label(version):
    """Le numéro à imprimer pour cette version, état compris.

    Une version publiée s'imprime sous son seul numéro. Toute autre le dit :
    « 2.1 — Brouillon, non approuvé ». Sans version, « Brouillon (non publié) ».
    """
    if not version:
        return 'Brouillon (non publié)'
    number = version.version_number or ''
    suffix = {
        'released': '',
        'superseded': ' (remplacée)',
        'withdrawn': ' (retirée)',
        'approved': ' — Approuvée, non publiée',
        'review': ' — Brouillon, non approuvé (en approbation)',
    }.get(version.state, ' — Brouillon, non approuvé')
    return number + suffix


def _image_mime(b64_value):
    """Type MIME réel d'une image base64, par ses octets d'en-tête.

    Le logo de société de Blue Fox est un SVG: le servir en `image/png`
    donnait une image cassée dans le PDF. On renifle les octets plutôt que de
    présumer PNG.
    """
    try:
        head = base64.b64decode(b64_value)[:16]
    except Exception:
        return 'image/png'
    if head[:4] == b'\x89PNG':
        return 'image/png'
    if head[:2] == b'\xff\xd8':
        return 'image/jpeg'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    stripped = head.lstrip()
    if stripped[:4].lower() == b'<svg' or stripped[:5].lower() == b'<?xml':
        return 'image/svg+xml'
    return 'image/png'


class ProjectDocumentBody(models.Model):
    """Corps vivant du document: les sections rédigées directement dans Odoo.

    Un document reste par défaut un pointeur vers un fichier externe
    (body_source='external'). Il ne bascule en corps interne que si son type
    possède un gabarit de sections, et sur action explicite.
    """
    _inherit = 'project.document'

    body_source = fields.Selection(
        selection=[
            ('external', 'Fichier externe'),
            ('internal', 'Rédigé dans Odoo'),
        ],
        string='Source du corps',
        default='external',
        required=True,
        index=True,
        tracking=True,
        help="Fichier externe: le document vit sur Nextcloud et Odoo n'en tient "
             "que la fiche. Rédigé dans Odoo: le texte vit dans les sections "
             "ci-dessous et le PDF est produit par le module.",
    )
    section_ids = fields.One2many(
        'project.document.section',
        'document_id',
        string='Sections',
    )
    section_count = fields.Integer(
        string='Nombre de sections',
        compute='_compute_body_state',
    )
    body_hash = fields.Char(
        string='Empreinte du corps',
        compute='_compute_body_state',
        store=True,
    )
    body_char_count = fields.Integer(
        string='Caractères rédigés',
        compute='_compute_body_state',
        store=True,
    )
    supports_body = fields.Boolean(
        related='type_id.supports_body',
        string='Corps interne possible',
        readonly=True,
    )
    has_unpublished_changes = fields.Boolean(
        string='Modifications non publiées',
        compute='_compute_has_unpublished_changes',
        store=True,
    )
    unpublished_section_codes = fields.Char(
        string='Sections modifiées',
        compute='_compute_has_unpublished_changes',
        store=True,
    )

    @api.depends('section_ids', 'section_ids.content_hash', 'section_ids.code',
                 'section_ids.sequence', 'section_ids.char_count',
                 'section_ids.content_kind')
    def _compute_body_state(self):
        for doc in self:
            sections = doc.section_ids.sorted(lambda s: (s.sequence, s.id))
            doc.section_count = len(sections)
            doc.body_char_count = sum(sections.mapped('char_count'))
            # Les sections générées sont exclues de l'empreinte: leur contenu
            # est produit au gel, à partir de la fiche du document. Les
            # inclure ferait passer chaque document pour « modifié » en
            # permanence, puisque le brouillon les garde vides.
            doc.body_hash = body_hash([
                (s.code, s.content) for s in sections if s.content_kind == 'html'
            ])

    @api.depends('body_hash', 'body_source', 'latest_version_id',
                 'latest_version_id.body_hash')
    def _compute_has_unpublished_changes(self):
        for doc in self:
            if doc.body_source != 'internal':
                doc.has_unpublished_changes = False
                doc.unpublished_section_codes = False
                continue
            latest = doc.latest_version_id
            if not latest or not latest.body_hash:
                doc.has_unpublished_changes = bool(doc.section_count)
                doc.unpublished_section_codes = False
                continue
            doc.has_unpublished_changes = doc.body_hash != latest.body_hash
            doc.unpublished_section_codes = ', '.join(
                doc._changed_section_codes(latest)
            ) or False

    def _changed_section_codes(self, version):
        """Codes des sections rédigées dont le contenu diffère de la version.

        Les sections générées par le système sont ignorées: elles sont
        recalculées à chaque gel, donc elles différeraient toujours.
        """
        self.ensure_one()
        frozen = {
            s.code: s.content_hash for s in version.section_ids
            if s.content_kind == 'html'
        }
        live = {
            s.code: s.content_hash for s in self.section_ids
            if s.content_kind == 'html'
        }
        changed = [code for code, h in live.items() if frozen.get(code) != h]
        changed += [code for code in frozen if code not in live]
        return sorted(changed)

    # ------------------------------------------------------------------
    # Gabarit
    # ------------------------------------------------------------------

    def action_apply_section_template(self):
        """Ajoute les sections manquantes du gabarit du type de document.

        Strictement additif: ne touche jamais à une section existante, ne
        supprime rien, ne réordonne rien.
        """
        created = 0
        for doc in self:
            if not doc.type_id.section_line_ids:
                raise UserError(
                    f"Le type « {doc.type_id.name} » n'a pas de gabarit de sections. "
                    "Configurez-le dans Configuration > Types de documents."
                )
            existing = set(doc.section_ids.mapped('code'))
            for line in doc.type_id.section_line_ids:
                if line.section_type_id.code in existing:
                    continue
                self.env['project.document.section'].create(line._section_vals(doc))
                created += 1
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Gabarit appliqué'),
                'message': _('%s section(s) ajoutée(s).') % created,
                'type': 'success',
                'sticky': False,
            },
        }

    def action_adopt_internal_body(self):
        """Bascule le document en corps interne et sème le gabarit."""
        for doc in self:
            if not doc.type_id.supports_body:
                raise UserError(
                    f"Le type « {doc.type_id.name} » n'a pas de gabarit de sections, "
                    "donc ce document ne peut pas héberger son corps dans Odoo."
                )
            doc.body_source = 'internal'
        self.action_apply_section_template()
        return True

    # ------------------------------------------------------------------
    # Sections générées
    # ------------------------------------------------------------------

    def _render_computed_section(self, render_key, section):
        """Produit le HTML d'une section générée par le système.

        Appelé au gel d'une version: le résultat est figé dans l'instantané,
        pour qu'une réimpression dans trois ans affiche le responsable et les
        dates de l'époque, pas ceux du jour.
        """
        self.ensure_one()
        renderer = getattr(self, f'_render_section_{render_key or ""}', None)
        if renderer is None:
            return Markup('')
        return renderer(section)

    def _pkm_render_version(self):
        """Version au nom de laquelle les sections générées sont rendues.

        Par défaut, la version publiée en vigueur. Le contexte
        ``pkm_render_version_id`` la désigne explicitement : au gel (la version
        qu'on fige, pas « la dernière publiée » au sens du calcul stocké) et à
        l'impression d'un brouillon (la version en préparation, ou aucune —
        valeur 0 — si le brouillon n'a pas encore de numéro). Sans cela, le
        bloc de gouvernance d'un brouillon 2.1 se réclamait de la 2.0.
        """
        self.ensure_one()
        Version = self.env['project.document.version']
        if 'pkm_render_version_id' not in self.env.context:
            return self.latest_version_id
        version = Version.browse(self.env.context['pkm_render_version_id'] or [])
        return version if version.document_id == self else Version

    def _render_section_governance(self, section):
        """Bloc « Gouvernance de cette documentation »."""
        self.ensure_one()
        version = self._pkm_render_version()
        rows = [
            ('Référence', self.code or ''),
            ('Type', self.type_id.name or ''),
            ('Responsable', self.owner_id.name or self.author_id.name or ''),
            ('Auteur', self.author_id.name or ''),
            ('Approbation', version.approved_by_id.name or ''),
            ('Version', version_label(version)),
            ("Entrée en vigueur", version.effective_date or ''),
            ('Dernière révision', self.last_review_date or ''),
            ('Prochaine révision', self.review_date or ''),
            ('Cycle de révision', f"{self.review_interval_months} mois"
                if self.review_interval_months else ''),
        ]
        cells = Markup('').join(
            Markup('<tr><td style="width:38%%"><strong>%s</strong></td><td>%s</td></tr>')
            % (label, value)
            for label, value in rows if value
        )
        return Markup('<table class="table table-sm o_pkm_governance">%s</table>') % cells

    def _render_section_revision_history(self, section):
        """Bloc « Historique des versions »."""
        self.ensure_one()
        versions = self.version_ids.filtered(
            lambda v: v.state in ('released', 'superseded')
        ).sorted(lambda v: (v.sequence_index or 0), reverse=True)
        if not versions:
            return Markup('')
        rows = Markup('').join(
            Markup('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>') % (
                v.version_number or '',
                v.effective_date or v.release_date or '',
                dict(v._fields['change_type'].selection).get(v.change_type, ''),
                v.change_summary or '',
            )
            for v in versions
        )
        return Markup(
            '<table class="table table-sm o_pkm_history">'
            '<thead><tr><th>Version</th><th>Date</th><th>Nature</th>'
            '<th>Résumé</th></tr></thead><tbody>%s</tbody></table>'
        ) % rows

    def _render_section_approvals(self, section):
        """Bloc « Approbations »."""
        self.ensure_one()
        version = self._pkm_render_version()
        if not version:
            return Markup('')
        rows = [
            ('Rédigé par', version.author_id.name or '', version.create_date or ''),
            ('Approuvé par', version.approved_by_id.name or '', version.approval_date or ''),
        ]
        cells = Markup('').join(
            Markup('<tr><td><strong>%s</strong></td><td>%s</td><td>%s</td></tr>')
            % (label, who, when)
            for label, who, when in rows if who
        )
        return Markup(
            '<table class="table table-sm o_pkm_approvals"><tbody>%s</tbody></table>'
        ) % cells

    # ------------------------------------------------------------------
    # Document final (PDF brandé)
    # ------------------------------------------------------------------

    def _report_sections(self, version=None, draft=False):
        """Sections à imprimer, dans l'ordre, contenu déjà rendu.

        Mêmes règles que le PDF (voir ``_report_print_context``) : sans
        argument, le corps de la version publiée en vigueur, tel qu'il a été
        figé. C'est ce que lisent les modules compagnons (portail d'accusé de
        réception, signature d'une distribution, affichage au poste avec
        ``bf_process``) : ils doivent montrer le texte approuvé, jamais le brouillon de travail.
        """
        self.ensure_one()
        return self._report_print_context(version=version, draft=draft)['sections']

    def _report_live_rows(self, render_version):
        """Le corps vivant (brouillon de travail), rendu pour l'impression.

        Les sections rédigées vides sont sautées : un titre suivi de rien ne
        vaut pas d'occuper une page. Les sections générées sont rendues au nom
        de ``render_version`` (une version en préparation, ou aucune).
        """
        self.ensure_one()
        rows = []
        sections = self.section_ids.with_context(
            pkm_render_version_id=render_version.id or 0)
        for section in sections.sorted(lambda s: (s.sequence, s.id)):
            if section.content_kind == 'html' and section.is_empty:
                continue
            html = section._render_content()
            if not html:
                continue
            rows.append({
                'title': section.name,
                'code': section.code,
                'html': html,
                'page_break': section.page_break_before,
            })
        return rows

    def _pkm_pending_version(self):
        """La version en préparation (brouillon, en révision, approuvée)."""
        self.ensure_one()
        return self.version_ids.filtered(
            lambda v: v.state in PENDING_STATES
        ).sorted('id', reverse=True)[:1]

    def _report_print_context(self, version=None, draft=False):
        """Ce qu'imprime le PDF : un corps, et le numéro qui va AVEC ce corps.

        Le défaut corrigé en 18.0.13.3.4 : le rapport lisait le corps
        vivant et prenait son numéro dans ``current_version``. Une politique
        2.0 publiée, avec une 2.1 en approbation, s'imprimait « VERSION 2.0 »
        sur le texte de la 2.1. Désormais le corps et le numéro viennent
        toujours de la même version.

        * ``draft`` : le brouillon de travail, marqué comme tel, au numéro de
          la version en préparation s'il y en a une.
        * ``version`` : cette version-là. Son instantané figé si elle en a un ;
          le corps vivant, marqué brouillon, si elle est encore en
          préparation ; rien s'il s'agit d'une ancienne version jamais figée
          (son texte vit dans sa pièce jointe ou sur Nextcloud).
        * ni l'un ni l'autre : la version publiée en vigueur, figée. Sans
          version publiée, le brouillon, marqué comme tel.

        Cas hérité : une version publiée avant que le corps ne soit rédigé
        dans Odoo n'a pas d'instantané. On imprime alors le corps vivant sous
        son numéro, avec une note qui le dit, tant qu'aucune version n'est en
        préparation ; s'il y en a une, le corps vivant est le sien et il
        s'imprime en brouillon.
        """
        self.ensure_one()
        Version = self.env['project.document.version']
        version = version or Version
        if version and version.document_id != self:
            raise UserError("Cette version n'appartient pas à ce document.")
        released = self.latest_version_id

        if draft:
            return self._report_ctx_live(self._pkm_pending_version())
        if not version:
            if released and released.section_ids:
                return self._report_ctx_frozen(released)
            if released:
                pending = self._pkm_pending_version()
                if pending:
                    return self._report_ctx_live(pending, unfrozen=released)
                return self._report_ctx_unfrozen(released)
            return self._report_ctx_live(self._pkm_pending_version())
        if version.section_ids:
            return self._report_ctx_frozen(version)
        if version.state in PENDING_STATES:
            return self._report_ctx_live(version)
        if version == released:
            return self._report_ctx_unfrozen(version)
        return self._report_ctx_unavailable(version)

    def _report_ctx(self, mode, version, sections, **extra):
        values = {
            'mode': mode,
            'version': version,
            'version_label': version_label(version),
            'sections': sections,
            'watermark': False,
            'banner': False,
            'note': False,
        }
        values.update(extra)
        values['watermark_uri'] = (
            watermark_uri(values['watermark']) if values['watermark'] else False
        )
        return values

    def _report_state_marks(self, version):
        """Filigrane et bandeau d'une version qui n'est pas celle en vigueur."""
        current = self.latest_version_id
        en_vigueur = (
            " La version en vigueur est la %s." % current.version_number
            if current and current != version else ''
        )
        state = version.state if version else 'draft'
        if state == 'released':
            return {}
        if state == 'superseded':
            return {
                'watermark': 'VERSION REMPLACÉE',
                'banner': "VERSION REMPLACÉE : elle n'est plus en vigueur." + en_vigueur,
            }
        if state == 'withdrawn':
            return {
                'watermark': 'VERSION RETIRÉE',
                'banner': "VERSION RETIRÉE : elle n'est plus en vigueur." + en_vigueur,
            }
        if state == 'approved':
            return {
                'watermark': 'APPROUVÉE, NON PUBLIÉE',
                'banner': "APPROUVÉE, NON PUBLIÉE : son texte n'est figé qu'à la "
                          "publication." + en_vigueur,
            }
        return {
            'watermark': DRAFT_WATERMARK,
            'banner': "%s : ce texte n'est pas en vigueur et peut encore "
                      "changer." % DRAFT_WATERMARK + en_vigueur,
        }

    def _report_ctx_frozen(self, version):
        rows = [
            {
                'title': s.name,
                'code': s.code,
                'html': Markup(s.content or ''),
                'page_break': s.page_break_before,
            }
            for s in version.section_ids.sorted(lambda s: (s.sequence, s.id))
            if not is_blank_html(s.content)
        ]
        return self._report_ctx('frozen', version, rows,
                                **self._report_state_marks(version))

    def _report_ctx_live(self, version, unfrozen=None):
        marks = self._report_state_marks(version)
        note = (
            "Document généré à partir d'un brouillon non publié. Le contenu "
            "peut encore évoluer avant le gel d'une version."
        )
        if unfrozen:
            note += (
                " La version publiée %s n'a pas de contenu figé dans Odoo."
                % unfrozen.version_number
            )
        return self._report_ctx('live', version, self._report_live_rows(version),
                                note=note, **marks)

    def _report_ctx_unfrozen(self, version):
        return self._report_ctx(
            'unfrozen', version, self._report_live_rows(version),
            note="Le corps de la version %s n'a pas été figé à sa publication : "
                 "elle a été publiée avant que son texte ne soit rédigé dans "
                 "Odoo. Le texte ci-dessous est celui du document aujourd'hui."
                 % version.version_number,
        )

    def _report_ctx_unavailable(self, version):
        return self._report_ctx(
            'unavailable', version, [],
            note="Le contenu de la version %s n'a pas été figé dans Odoo : il "
                 "vit dans le fichier joint à la version ou sur Nextcloud."
                 % version.version_number,
            **self._report_state_marks(version)
        )

    def _report_brand(self):
        """Palette + logo pour le rendu, via la société du document."""
        self.ensure_one()
        company = self.company_id or self.env.company
        brand = dict(company._pkm_brand())
        # Logo en URI de données, prêt pour <img t-att-src>: éviter de
        # bricoler l'encodage base64 dans le QWeb.
        logo = brand.get('logo_dark') or brand.get('logo')
        brand['logo_uri'] = False
        if logo:
            raw = logo if isinstance(logo, str) else logo.decode('ascii')
            brand['logo_uri'] = 'data:%s;base64,%s' % (_image_mime(raw), raw)
        return brand

    def action_generate_body_pdf(self):
        """Produit le PDF brandé du corps documentaire."""
        self.ensure_one()
        if self.body_source != 'internal':
            raise UserError(
                "Ce document pointe vers un fichier externe: son corps ne vit "
                "pas dans Odoo, il n'y a rien à générer ici."
            )
        if not self._report_sections():
            raise UserError(
                "Aucune section remplie: rédigez le corps avant de générer le PDF."
            )
        return self.env.ref(
            'project_knowledge_matrix.action_report_document_body'
        ).report_action(self)

    def action_generate_draft_pdf(self):
        """Aperçu du brouillon de travail, marqué « Brouillon, non approuvé ».

        Le PDF du document imprime la version publiée ; c'est ici qu'on relit
        ses retouches avant de les soumettre.
        """
        self.ensure_one()
        if self.body_source != 'internal':
            raise UserError(
                "Ce document pointe vers un fichier externe: son corps ne vit "
                "pas dans Odoo, il n'y a rien à générer ici."
            )
        if not self._report_sections(draft=True):
            raise UserError(
                "Aucune section remplie: rédigez le corps avant de générer le PDF."
            )
        return self.env.ref(
            'project_knowledge_matrix.action_report_document_draft'
        ).report_action(self)

    # ------------------------------------------------------------------
    # Création
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        documents = super().create(vals_list)
        for doc in documents:
            if doc.body_source == 'internal' and not doc.section_ids \
                    and doc.type_id.section_line_ids:
                for line in doc.type_id.section_line_ids:
                    self.env['project.document.section'].create(line._section_vals(doc))
        return documents

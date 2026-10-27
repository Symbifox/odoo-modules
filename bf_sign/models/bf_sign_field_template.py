from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

from .bf_sign_field import copy_compatible


class BfSignFieldTemplate(models.Model):
    """A reusable layout of signature pads.

    Saves the placement of pads for a document so the same layout can be applied
    to future requests for that document. Pads reference a signer by **rank**
    (signer_index: 0 = first signer, 1 = second…) rather than a specific signer,
    so the template is reusable across requests with different signers.
    """

    _name = "bf.sign.field.template"
    _description = "Modèle de disposition des pavés"
    _order = "name"

    name = fields.Char(string="Nom", required=True)
    company_id = fields.Many2one(
        "res.company", string="Société", default=lambda self: self.env.company)
    line_ids = fields.One2many(
        "bf.sign.field.template.line", "template_id", string="Pavés", copy=True)
    field_count = fields.Integer(compute="_compute_counts", string="Pavés")
    signer_count = fields.Integer(compute="_compute_counts", string="Signataires")
    active = fields.Boolean(default=True)
    # Le formulaire que la disposition habille. Sans lui, choisir
    # un modèle ne ramène que des pavés et il faut repartir d'un PDF local.
    # Facultatif : un « bloc de signature » posé sur des contrats différents
    # n'a pas de document à lui, et ne doit surtout pas en imposer un.
    document_file = fields.Binary(string="Document (PDF)", attachment=True)
    document_filename = fields.Char(string="Nom du fichier")

    @api.depends("line_ids", "line_ids.signer_index")
    def _compute_counts(self):
        for rec in self:
            rec.field_count = len(rec.line_ids)
            rec.signer_count = (
                max(rec.line_ids.mapped("signer_index")) + 1) if rec.line_ids else 0

    def copy(self, default=None):
        """Dupliquer un modèle, recopies comprises.

        `copy_from_line_id` n'est pas copié tel quel (`copy=False`) : il viserait
        une ligne de l'ANCIEN modèle, ce que la contrainte refuse, et toute la
        duplication échouait. Les recopies naissent en saisie et le lien se
        rattache à la ligne correspondante, appariée par position.
        """
        copies = super().copy(default)
        for source, dup in zip(self, copies):
            anciennes = source.line_ids.sorted("id")
            neuves = dup.line_ids.sorted("id")
            if len(anciennes) != len(neuves):
                continue
            par_ligne = dict(zip(anciennes.ids, neuves))
            for old, new in zip(anciennes, neuves):
                if old.fill_mode == "copy" and old.copy_from_line_id.id in par_ligne:
                    new.write({"fill_mode": "copy",
                               "copy_from_line_id": par_ligne[old.copy_from_line_id.id].id})
        return copies


class BfSignFieldTemplateLine(models.Model):
    _name = "bf.sign.field.template.line"
    _description = "Pavé d'un modèle de disposition"
    _order = "signer_index, page, sequence, id"

    template_id = fields.Many2one(
        "bf.sign.field.template", required=True, ondelete="cascade", index=True)
    signer_index = fields.Integer(
        string="Rang du signataire", default=0,
        help="0 = premier signataire, 1 = deuxième, etc.")
    field_type = fields.Selection(
        selection=[
            ("signature", "Signature"),
            ("initials", "Paraphe"),
            ("date", "Date"),
            ("text", "Texte"),
            ("name", "Nom"),
            ("email", "Courriel"),
            ("number", "Nombre"),
            ("checkbox", "Case à cocher"),
            ("cells", "Cases par caractère"),
            ("select", "Liste de choix"),
        ],
        string="Type", default="signature", required=True)
    page = fields.Integer(string="Page", default=1, required=True)
    # 🔴 Un gabarit de pavés se réapplique à des documents de longueurs
    # DIFFÉRENTES. Les ententes de service de Blue Fox font 9, 11, 12, 36 et
    # 37 pages : un pavé enregistré « page 9 » se pose au milieu du texte dès
    # que le document suivant en compte douze. Le bloc de signature, lui, est
    # toujours à la fin, d'où un ancrage relatif plutôt qu'un numéro absolu.
    page_mode = fields.Selection(
        selection=[
            ("absolute", "Page fixe"),
            ("last", "Dernière page"),
        ],
        string="Ancrage de la page", default="absolute", required=True,
        help="« Page fixe » applique le numéro de page tel quel. "
             "« Dernière page » le recalcule sur le document visé, ce qui "
             "permet de réutiliser le même gabarit sur des documents de "
             "longueurs différentes.")
    pos_x = fields.Float(default=0.60)
    pos_y = fields.Float(default=0.80)
    width = fields.Float(default=0.25)
    height = fields.Float(default=0.08)
    fill_mode = fields.Selection(
        selection=[
            ("auto", "Automatique (signataire / date de signature)"),
            ("fixed", "Valeur fixe (préparateur)"),
            ("signer", "Rempli par le signataire"),
            ("copy", "Recopie d'un autre pavé"),
        ],
        string="Mode de remplissage", default="signer", required=True)
    # Même lien que `bf.sign.field.copy_from_id`, entre lignes du
    # même modèle ; `apply_field_template` le traduit en lien entre pavés.
    copy_from_line_id = fields.Many2one(
        "bf.sign.field.template.line", string="Recopie de", ondelete="set null",
        copy=False,
        domain="[('template_id', '=', template_id), ('fill_mode', '!=', 'copy')]",
        help="Ligne du même modèle dont ce pavé reprend la valeur.")
    required = fields.Boolean(string="Obligatoire", default=True)
    value_text = fields.Char(string="Valeur fixe / étiquette")
    # Carried alongside the geometry: a ``cells`` pad without its box count and a
    # ``select`` pad without its choices are not the same pad once re-applied.
    cell_count = fields.Integer(string="Nombre de cases", default=0)
    option_values = fields.Text(string="Choix offerts")
    sequence = fields.Integer(default=10)

    @api.depends("signer_index", "page", "field_type", "fill_mode", "value_text")
    def _compute_display_name(self):
        types = dict(self._fields["field_type"]._description_selection(self.env))
        for line in self:
            label = line.value_text if line.fill_mode != "fixed" and line.value_text \
                else types.get(line.field_type, line.field_type)
            line.display_name = _("p. %(page)s · %(label)s · signataire %(rang)s",
                                  page=line.page, label=label,
                                  rang=line.signer_index + 1)

    @api.constrains("fill_mode", "copy_from_line_id", "field_type", "template_id")
    def _check_copy_from_line(self):
        targets = self.search([("copy_from_line_id", "in", self.ids)]) if self.ids \
            else self.browse()
        for line in self | targets:
            src = line.copy_from_line_id
            if line.fill_mode != "copy":
                if src:
                    raise ValidationError(_(
                        "Seul un pavé en mode « Recopie d'un autre pavé » désigne "
                        "un pavé source."))
                continue
            if not src:
                raise ValidationError(_(
                    "Choisissez la ligne dont ce pavé reprend la valeur."))
            if src == line or src.template_id != line.template_id:
                raise ValidationError(_(
                    "Une ligne recopie une AUTRE ligne du même modèle."))
            if src.fill_mode == "copy":
                raise ValidationError(_(
                    "La ligne source est elle-même une recopie : désignez la ligne "
                    "d'origine."))
            if not copy_compatible(src.field_type, line.field_type):
                raise ValidationError(_(
                    "Une case à cocher ne recopie qu'une case à cocher, et une "
                    "valeur écrite qu'une valeur écrite."))

    def copy_data(self, default=None):
        # Le lien ne se copie pas (`copy=False`) : sans lui, la ligne naît en
        # saisie, et `BfSignFieldTemplate.copy` le rattache ensuite.
        vals_list = super().copy_data(default)
        for vals in vals_list:
            if vals.get("fill_mode") == "copy" and not vals.get("copy_from_line_id"):
                vals["fill_mode"] = "signer"
        return vals_list

    def unlink(self):
        # Même règle que pour les pavés : la recopie d'une ligne retirée
        # redevient une case à remplir.
        orphans = self.search([("copy_from_line_id", "in", self.ids)]) - self
        if orphans:
            orphans.write({"fill_mode": "signer", "copy_from_line_id": False})
        return super().unlink()

    def resolve_page(self, page_count=None):
        """Page number this pad lands on for a document of ``page_count`` pages.

        ``page_count`` is None when the target document could not be read: the
        stored number is then used as-is. A pad is never dropped and never
        lands outside the document, because a pad that vanishes at apply time
        is a signature block that nobody notices is missing.
        """
        self.ensure_one()
        page = self.page or 1
        if self.page_mode == "last" and page_count:
            page = page_count
        if page_count:
            page = max(1, min(page, page_count))
        return max(1, page)

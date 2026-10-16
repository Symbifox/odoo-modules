from odoo import api, fields, models


class BfMeetingLangMixin(models.AbstractModel):
    """Langue d'un ordre du jour ou d'un compte rendu, choisie à la main ou non.

    La langue (`lang`, champ calculé stocké de chaque modèle) se recalcule
    quand le client ou les lecteurs changent, TANT QU'ON NE L'A PAS CHOISIE À
    LA MAIN (règle du 2026-10-16) et tant que le document n'est pas
    envoyé. Le choix se reconnaît ainsi :

    * au formulaire, par `_onchange_lang_manual` : l'onchange d'Odoo ne
      protège que les champs de l'appel en cours, et le calcul écraserait un
      choix fait avant d'ajouter des participants. L'onchange repasse aussi
      quand c'est le calcul qui change la langue ; elle égale alors la règle,
      donc rien n'est figé ;
    * `lang_manual` fourni avec la langue (formulaire, copie d'un OdJ vers son
      compte rendu) : il fait foi ;
    * langue écrite SANS lui par du code (meeting-processor : langue parlée ;
      rendez-vous : langue du demandeur ; XML-RPC) : c'est un choix, sauf
      quand la même écriture change aussi le client ou les lecteurs (le
      formulaire renvoie la langue que son calcul vient de suivre) ; on la
      compare alors à la règle.
    """

    _name = 'bf.meeting.lang.mixin'
    _description = 'Meeting language (shared)'

    # Champs dont le changement fait suivre la langue au formulaire (par modèle).
    _BF_CHAMPS_LANGUE = ()

    lang_manual = fields.Boolean(
        string='Language chosen by hand',
        # Copié avec la langue (champ calculé readonly=False, donc copié) :
        # sinon `create` prendrait la copie pour un choix et la figerait.
        copy=True,
        help="Set when someone picks a language other than the one the rule "
             "gives. The language then stops following the client and the "
             "participants.",
    )

    def _bf_lecteurs(self):
        """Les personnes qui lisent le document, internes comprises."""
        return self.env['res.partner']

    @api.onchange('lang')
    def _onchange_lang_manual(self):
        from .meeting_record import _langue_automatique
        for rec in self:
            rec.lang_manual = bool(rec.lang) and rec.lang != _langue_automatique(rec)

    def _bf_ecrire_marque(self, manuel):
        # Sans repasser par notre `write` : la marque n'est pas un choix de langue.
        for rec in self:
            if rec.lang_manual != manuel:
                super(BfMeetingLangMixin, rec).write({'lang_manual': manuel})

    def _bf_comparer_a_la_regle(self):
        from .meeting_record import _langue_automatique
        for rec in self:
            rec._bf_ecrire_marque(bool(rec.lang) and rec.lang != _langue_automatique(rec))

    @api.model_create_multi
    def create(self, vals_list):
        ecrites = [bool(v.get('lang')) and 'lang_manual' not in v for v in vals_list]
        records = super().create(vals_list)
        for rec, ecrite in zip(records, ecrites):
            if ecrite:
                rec._bf_ecrire_marque(True)
        return records

    def write(self, vals):
        result = super().write(vals)
        if 'lang' in vals and 'lang_manual' not in vals:
            if any(champ in vals for champ in self._BF_CHAMPS_LANGUE):
                self._bf_comparer_a_la_regle()
            else:
                self._bf_ecrire_marque(bool(vals['lang']))
        return result

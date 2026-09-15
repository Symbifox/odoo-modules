"""Les types de fiche qu'une pastille peut viser, choisis par la gestion.

🔴 **Une liste blanche, pas « n'importe quel modèle ».** Elle borne deux choses :
ce que la gravure propose, et ce que la route de recherche de l'application
accepte d'interroger. Une recherche ouverte à tous les modèles serait une API de
lecture générale posée à côté des pastilles.

⚠️ Jusqu'à la 2.2.0, cette liste était un paramètre système en texte
(``bf_nfc.modeles_cibles``), réservé à l'administrateur système, qu'on remplissait
de noms techniques, et que le site ignorait. La migration 2.3.0 le convertit en
lignes ici, et la même liste sert maintenant au site et à l'application.

Les modèles qu'un geste EXIGE (l'équipement du prêt, le point de tournée) n'ont
pas besoin d'y figurer : le catalogue des gestes les ajoute de lui-même.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Des modèles qu'une pastille peut viser, mais qu'on ne propose qu'à la gestion :
# ils déclenchent des traitements.
MODELES_GESTION = {"ir.cron", "ir.actions.server"}


class BfNfcTargetType(models.Model):
    _name = "bf.nfc.target.type"
    _description = "Type de fiche visable par une pastille"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    model = fields.Selection(selection="_selection_modeles", string="Type de fiche", required=True)
    name = fields.Char(string="Libellé", compute="_compute_name", store=True, readonly=False,
                       translate=True)
    active = fields.Boolean(default=True)
    gestion_seulement = fields.Boolean(
        string="Réservé à la gestion", compute="_compute_gestion_seulement", store=True,
        readonly=False,
        help="Le type n'est proposé qu'aux gestionnaires des pastilles. Toujours coché "
             "pour ce qui déclenche un traitement (tâche planifiée, action).")

    _sql_constraints = [
        ("modele_unique", "unique(model)", "Ce type de fiche est déjà dans la liste."),
    ]

    @api.model
    def _selection_modeles(self):
        """Les modèles concrets de cette base, par leur nom lisible.

        ⚠️ Lu dans le registre et pas dans ``ir.model`` : la gestion des pastilles
        n'a pas le droit de lire ``ir.model``, et la liste déroulante tombait sur
        une erreur d'accès pour un gestionnaire qui n'est pas administrateur.
        """
        rendu = []
        for nom, classe in self.env.registry.items():
            if classe._abstract or classe._transient or not classe._auto:
                continue
            if nom.startswith(("ir.", "base.", "res.users", "bus.", "mail.", "bf.nfc.tap",
                               "bf.nfc.device", "bf.nfc.sdm")) and nom not in MODELES_GESTION:
                continue
            rendu.append((nom, "%s (%s)" % (classe._description or nom, nom)))
        return sorted(rendu, key=lambda paire: paire[1].lower())

    @api.depends("model")
    def _compute_name(self):
        # ⚠️ Toujours assigner : un calcul stocké qui saute un enregistrement lève
        # au moment d'écrire, pas au moment de calculer.
        for ligne in self:
            if ligne.model and not ligne.name and ligne.model in self.env:
                ligne.name = self.env["ir.model"].sudo()._get(ligne.model).name
            else:
                ligne.name = ligne.name

    @api.depends("model")
    def _compute_gestion_seulement(self):
        for ligne in self:
            ligne.gestion_seulement = ligne.gestion_seulement or ligne.model in MODELES_GESTION

    @api.constrains("model", "gestion_seulement")
    def _check_gestion(self):
        for ligne in self:
            if ligne.model in MODELES_GESTION and not ligne.gestion_seulement:
                raise ValidationError(_("« %s » déclenche un traitement : ce type reste réservé "
                                        "à la gestion.", ligne.name or ligne.model))

    @api.model
    def _noms(self, gestion):
        """Les modèles de la liste, dans l'ordre, présents dans cette base."""
        lignes = self.sudo().search([])
        return [l.model for l in lignes
                if l.model in self.env and (gestion or not l.gestion_seulement)]

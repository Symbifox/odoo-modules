from odoo import _, api, models
from odoo.exceptions import AccessError


class BfHelpdeskOnchangeGuard(models.AbstractModel):
    """Contrôle d'accès des ``onchange`` sur les modèles de l'assistance.

    Sur un enregistrement neuf, Odoo ne contrôle ni les groupes des champs
    demandés ni l'accès aux billets passés en valeur. Les champs liés et
    calculés en sudo rendraient alors, à qui le demande, les données d'un
    billet ou d'un contact qu'il ne voit pas. On refuse donc le portail (il n'a
    aucun formulaire de l'interface), les champs qu'un groupe réserve et tout
    billet que l'usager ne peut pas lire, qu'il vienne des valeurs, d'une
    sous-ligne x2many ou des valeurs par défaut (``default_*`` du contexte et
    ``ir.default`` par usager, que tout interne pose par RPC).
    """

    _name = "bf.helpdesk.onchange.guard"
    _description = "Garde d'accès des onchange de l'assistance"

    # Modèles dont un many2one passé en valeur doit être lisible : la fiche en
    # lit ensuite des champs liés, ou le nom, en sudo.
    _bf_guarded_comodels = ("helpdesk.ticket", "project.knowledge.item", "survey.user_input")

    def onchange(self, values, field_names, fields_spec):
        self._bf_check_onchange(values or {}, fields_spec or {})
        return super().onchange(values, field_names, fields_spec)

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        if not self.env.su:
            self._bf_check_refs(self._bf_refs_in(self, defaults))
        return defaults

    def _bf_check_onchange(self, values, fields_spec):
        if self.env.su:
            return
        if self.env.user.share:
            raise AccessError(_("Ce formulaire est réservé aux agents."))
        self.check_field_access_rights(
            "read", [name for name in fields_spec if name in self._fields])
        self._bf_check_refs(self._bf_refs_in(self, values))
        # Lier un enregistrement existant par une commande x2many (un sondage,
        # un journal de triage) rendrait ses champs liés, calculés en sudo.
        for name, value in values.items():
            field = self._fields.get(name)
            if field is not None and field.type in ("one2many", "many2many"):
                linked = self._bf_onchange_ids(field, value)
                if linked:
                    self.env[field.comodel_name].browse(linked).check_access("read")

    def _bf_check_refs(self, refs):
        for comodel, ids in refs.items():
            if ids:
                self.env[comodel].browse(ids).check_access("read")

    @api.model
    def _bf_refs_in(self, model, values):
        """Les enregistrements gardés que désignent des valeurs, sous-lignes
        x2many comprises : {modèle: identifiants}."""
        refs = {}
        for name, value in (values or {}).items():
            field = model._fields.get(name)
            if field is None or not field.relational:
                continue
            if field.comodel_name in self._bf_guarded_comodels and field.comodel_name in self.env:
                refs.setdefault(field.comodel_name, set()).update(
                    self._bf_onchange_ids(field, value))
            if field.type in ("one2many", "many2many") and isinstance(value, (list, tuple)):
                comodel = self.env[field.comodel_name]
                for command in value:
                    if isinstance(command, (list, tuple)) and len(command) > 2 \
                            and command[0] in (0, 1) and isinstance(command[2], dict):
                        for sub_model, ids in self._bf_refs_in(comodel, command[2]).items():
                            refs.setdefault(sub_model, set()).update(ids)
        return refs

    @staticmethod
    def _bf_onchange_ids(field, value):
        """Les identifiants qu'une valeur d'onchange désigne."""
        if field.type == "many2one":
            if isinstance(value, dict):
                value = value.get("id")
            return [value] if isinstance(value, int) and value else []
        if field.type not in ("many2many", "one2many") or not isinstance(value, (list, tuple)):
            return []
        ids = []
        for command in value:
            if isinstance(command, int):
                ids.append(command)
                continue
            if not isinstance(command, (list, tuple)) or not command:
                continue
            if command[0] in (1, 4) and len(command) > 1 and isinstance(command[1], int):
                ids.append(command[1])
            elif command[0] == 6 and len(command) > 2:
                ids.extend(i for i in command[2] if isinstance(i, int))
        return ids


class HelpdeskTicket(models.Model):
    _name = "helpdesk.ticket"
    _inherit = ["helpdesk.ticket", "bf.helpdesk.onchange.guard"]

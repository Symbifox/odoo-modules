from odoo import fields, models

#: Les compteurs de Gen, dans l'ordre de l'écran. Session et
#: semaine sont les deux fenêtres du forfait ; jetons et coût, la conversation.
GEN_METERS = ("session", "week", "tokens", "cost")


class ResUsers(models.Model):
    _inherit = "res.users"

    # Ce que la liste des conversations de Gen affiche, le titre
    # ou l'élément associé. Sur l'usager et non sur l'appareil, pour que le
    # web et le mobile suivent le même choix.
    gen_list_mode = fields.Selection(
        [("title", "Titre"), ("element", "Élément associé")],
        string="Liste Gen",
        default="title",
    )
    # Le jour (local à la personne) de la dernière notification
    # « conversations à suivre ». Une seule par jour, même inscrite à plusieurs
    # courriels quotidiens.
    gen_closure_push_date = fields.Date(string="Gen follow-up push", copy=False)

    # Les compteurs de Gen que la personne a choisi
    # d'afficher, parmi ceux que la configuration lui permet de voir. Une
    # liste plutôt que quatre cases : un compteur neuf s'ajoute sans colonne.
    gen_meters = fields.Char(string="Gen counters", default=",".join(GEN_METERS))

    def _gen_meters_allowed(self):
        """Les compteurs que la configuration permet à cet usager de voir.

        Un groupe vide dans la configuration = tous les usagers internes.
        Le forfait couvre ses deux fenêtres (session, semaine).
        """
        self.ensure_one()
        if not self._is_internal():
            return set()
        ICP = self.env["ir.config_parameter"].sudo()
        groupes = set(self.sudo().groups_id.ids)
        permis = set()
        for meters, cle in ((("session", "week"), "bf_claude_chat.meter_plan_group_id"),
                            (("tokens",), "bf_claude_chat.meter_usage_group_id"),
                            (("cost",), "bf_claude_chat.meter_cost_group_id")):
            try:
                gid = int(ICP.get_param(cle) or 0)
            except ValueError:
                gid = 0
            if not gid or gid in groupes:
                permis.update(meters)
        return permis

    def _gen_meters_shown(self):
        """{compteur: affiché} : permis par la configuration ET choisi."""
        self.ensure_one()
        choisis = {m.strip() for m in (self.gen_meters or "").split(",")}
        permis = self._gen_meters_allowed()
        return {m: m in permis and m in choisis for m in GEN_METERS}

    def _set_gen_meters(self, meters):
        """Retenir le choix de la personne ; un nom inconnu est ignoré.

        `meters` ne parle que des compteurs que l'écran lui montre (les
        permis) : le choix déjà fait sur un compteur masqué par la
        configuration reste tel quel, pour le jour où il redevient permis.
        """
        self.ensure_one()
        voulus = set(meters or [])
        anciens = {m.strip() for m in (self.gen_meters or "").split(",")}
        permis = self._gen_meters_allowed()
        garde = [m for m in GEN_METERS
                 if (m in voulus if m in permis else m in anciens)]
        self.sudo().gen_meters = ",".join(garde)
        return self._gen_meters_shown()

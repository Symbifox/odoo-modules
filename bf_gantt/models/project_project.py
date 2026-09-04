# -*- coding: utf-8 -*-
"""Ce que le projet gagne : un bouton, et une adresse à donner.

`project.project` hérite déjà de `portal.mixin`, donc il a son `access_token` et
son `_document_check_access`. On ne réinvente rien : on pose seulement le
drapeau qui dit si l'échéancier est ouvert, parce qu'un token qui existe n'est
pas une permission de publier.
"""
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

GROUPE_GESTION = "bf_gantt.group_bf_gantt_manager"


class ProjectProject(models.Model):
    _inherit = "project.project"

    bf_gantt_published = fields.Boolean(
        string="Échéancier publié",
        copy=False,
        help="Ouvre l'adresse à token en lecture seule. Décochée, l'adresse "
             "répond « accès refusé » même avec le bon token.",
    )
    bf_gantt_grouping = fields.Selection(
        selection=[
            ("stage", "Étape du projet"),
            ("milestone", "Jalon"),
            ("assignee", "Responsable"),
            ("company", "Société"),
            ("step", "Étape de progression"),
            ("none", "Aucun"),
        ],
        string="Regroupement de l'échéancier",
        default="stage",
        help="Le regroupement retenu à l'ouverture, ici comme au portail.",
    )

    # ------------------------------------------------------------------
    # 🔴 La garde de publication, et elle est SERVEUR
    # ------------------------------------------------------------------
    #
    # `groups=` dans la vue ne garde rien : le champ reste écrivable par RPC, et
    # `project.project` en écriture est un droit que beaucoup de monde a. Sans ce
    # contrôle, un simple administrateur de projets pouvait ouvrir au public les
    # noms de tâches et leurs responsables sans jamais avoir reçu le groupe.
    # `bf.gantt.plan` n'avait pas le défaut : son ACL borne déjà l'écriture.

    def _bf_gantt_exiger_le_droit_de_publier(self):
        # ⚠️ Le superusager passe, comme partout dans Odoo : une migration, un
        # fichier de données ou une action serveur tournent sous `env.su`, et une
        # garde applicative qui les refuse casse l'installation sans rien
        # protéger de plus (qui peut écrire une action serveur est déjà admin).
        if not self.env.su and not self.env.user.has_group(GROUPE_GESTION):
            raise AccessError(_(
                "Publier un échéancier le rend lisible sans compte. Ce geste "
                "demande le groupe « Échéancier : gestion et publication »."))

    def write(self, valeurs):
        # ⚠️ Uniquement NOTRE champ. `access_token` appartient à `portal.mixin` et
        # le cœur d'Odoo l'écrit dans des flux qui n'ont rien à voir avec nous ;
        # le garder ici casserait l'envoi d'un projet par courriel pour un usager
        # qui n'a pas ce groupe. Le geste de régénérer le token, lui, a sa garde
        # explicite dans son action.
        if "bf_gantt_published" in valeurs:
            self._bf_gantt_exiger_le_droit_de_publier()
        return super().write(valeurs)

    @api.model_create_multi
    def create(self, liste_valeurs):
        for valeurs in liste_valeurs:
            if valeurs.get("bf_gantt_published"):
                self._bf_gantt_exiger_le_droit_de_publier()
        return super().create(liste_valeurs)

    def action_bf_gantt(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("bf_gantt.action_bf_gantt")
        action["context"] = {
            "default_bf_gantt_kind": "project",
            "default_bf_gantt_id": self.id,
        }
        action["params"] = {
            "kind": "project",
            "res_id": self.id,
            "grouping": self.bf_gantt_grouping or "stage",
        }
        return action

    def action_bf_gantt_publier(self):
        self._bf_gantt_exiger_le_droit_de_publier()
        for projet in self:
            projet.bf_gantt_published = True
            projet._portal_ensure_token()
        return True

    def action_bf_gantt_depublier(self):
        self._bf_gantt_exiger_le_droit_de_publier()
        self.write({"bf_gantt_published": False})
        return True

    def action_bf_gantt_regenerer_token(self):
        self._bf_gantt_exiger_le_droit_de_publier()
        for projet in self:
            projet.access_token = uuid.uuid4().hex
        return True

    def action_bf_gantt_copier_lien(self):
        """Rend l'adresse complète, prête à coller dans un courriel.

        ⚠️ Le même droit que publier. La méthode est publique, donc appelable
        par RPC par n'importe quel usager qui lit le projet, et elle FRAPPE le
        jeton au passage : sans cette garde, un lecteur pouvait se fabriquer
        l'adresse privée d'un échéancier et la distribuer. Le bouton est déjà
        caché aux autres dans la vue, mais cacher un bouton n'a jamais rien
        fermé.
        """
        self.ensure_one()
        self._bf_gantt_exiger_le_droit_de_publier()
        self._portal_ensure_token()
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        lien = "%s/mon/echeancier/project/%s?access_token=%s" % (
            base.rstrip("/"), self.id, self.access_token)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "info",
                "sticky": True,
                "title": _("Adresse de l'échéancier"),
                "message": lien,
            },
        }

# -*- coding: utf-8 -*-
"""Le portail client de Progression : une adresse à donner au client du mandat.

Le client voit où en est son mandat et ce qu'on attend de lui, sans compte.

🔴 Le portail a SON jeton, pas l'`access_token` de `portal.mixin`. Celui du
projet ouvre aussi les routes standard d'Odoo (`/my/projects/<id>` et
`/my/projects/<id>/task/<id>`) : chaque tâche, sa description, ses pièces
jointes. Donner au client l'adresse de sa progression ne doit pas lui donner le
dossier interne au passage.

Ce que la page montre, et rien d'autre : le nom du projet, le client, les étapes
cochées « Visible portail client », l'avancement de chacune et la consigne de
l'étape en cours. Aucun nom de tâche, aucun responsable, aucune heure.
"""
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import consteq

GROUPE_PUBLICATION = "project.group_project_manager"
ETATS_FERMES = ("1_done", "1_canceled")
# Un jeton du portail fait 43 caractères ; au-delà de 128, c'est un intrus.
LONGUEUR_MAX_JETON = 128
# Un jeton écrit à la main (par RPC) doit rester aussi difficile à deviner.
LONGUEUR_MIN_JETON = 32


def jetons_egaux(attendu, jeton):
    """Compare deux jetons en temps constant, sans jamais lever.

    ⚠️ `consteq` lève `TypeError` sur une chaîne non ASCII (et sur ce qui n'est
    pas une chaîne) : un visiteur obtenait un 500, et la trace Python dans les
    routes JSON. Tout jeton hors forme est refusé AVANT la comparaison.
    """
    if not isinstance(attendu, str) or not isinstance(jeton, str):
        return False
    if not attendu or not jeton or len(jeton) > LONGUEUR_MAX_JETON:
        return False
    if not attendu.isascii() or not jeton.isascii():
        return False
    return consteq(attendu, jeton)


class ProjectProject(models.Model):
    _inherit = "project.project"

    progression_portal_published = fields.Boolean(
        string="Client portal published",
        copy=False,
        help="Opens the progression page to whoever holds its address. "
             "Unchecked, the address answers \"not found\", even with the "
             "right token.",
    )
    progression_portal_token = fields.Char(
        string="Client portal token",
        copy=False,
        readonly=True,
        groups=GROUPE_PUBLICATION,
    )

    # ------------------------------------------------------------------
    # 🔴 La garde de publication est SERVEUR
    # ------------------------------------------------------------------
    #
    # Un `groups=` dans la vue ne ferme rien : le champ s'écrit par RPC, et
    # écrire un projet est un droit que beaucoup d'usagers ont.

    def _progression_exiger_le_droit_de_publier(self):
        # Le superusager passe : une migration ou un fichier de données tournent
        # sous `env.su`, et les refuser casserait l'installation sans rien protéger.
        if not self.env.su and not self.env.user.has_group(GROUPE_PUBLICATION):
            raise AccessError(_(
                "Publishing the client portal makes the progression of this "
                "project readable without an account. This action requires "
                "the \"Project: Administrator\" group."))

    @api.constrains("progression_portal_token")
    def _progression_controler_le_jeton(self):
        """Le champ est en lecture seule dans la vue, pas par RPC : un
        administrateur de projets pouvait y écrire « 1234 », et le portail
        s'ouvrait à qui le devine. Vide, il ne s'ouvre à personne (le jeton se
        refait à la publication). Sinon : 32 à 128 caractères ASCII visibles,
        sans espace (le jeton est un segment d'adresse)."""
        for projet in self.sudo():
            jeton = projet.progression_portal_token
            if not jeton:
                continue
            if (not LONGUEUR_MIN_JETON <= len(jeton) <= LONGUEUR_MAX_JETON
                    or not all("!" <= c <= "~" for c in jeton)):
                raise ValidationError(_(
                    "The client portal token must be %(min)s to %(max)s printable "
                    "ASCII characters, without spaces.",
                    min=LONGUEUR_MIN_JETON, max=LONGUEUR_MAX_JETON))

    def write(self, valeurs):
        if "progression_portal_published" in valeurs:
            self._progression_exiger_le_droit_de_publier()
        return super().write(valeurs)

    @api.model_create_multi
    def create(self, liste_valeurs):
        for valeurs in liste_valeurs:
            if valeurs.get("progression_portal_published"):
                self._progression_exiger_le_droit_de_publier()
        return super().create(liste_valeurs)

    def _progression_assurer_le_jeton(self):
        for projet in self.sudo():
            if not projet.progression_portal_token:
                projet.progression_portal_token = secrets.token_urlsafe(32)

    def action_progression_portal_publish(self):
        self._progression_exiger_le_droit_de_publier()
        self._progression_assurer_le_jeton()
        self.write({"progression_portal_published": True})
        return True

    def action_progression_portal_unpublish(self):
        self._progression_exiger_le_droit_de_publier()
        self.write({"progression_portal_published": False})
        return True

    def action_progression_portal_regenerate_token(self):
        """L'ancienne adresse cesse de répondre ; la nouvelle est à redonner."""
        self._progression_exiger_le_droit_de_publier()
        for projet in self.sudo():
            projet.progression_portal_token = secrets.token_urlsafe(32)
        return True

    def _progression_portal_url(self):
        self.ensure_one()
        base = self.get_base_url().rstrip("/")
        return "%s/progression/%s/%s" % (
            base, self.id, self.sudo().progression_portal_token)

    def action_progression_portal_copy_link(self):
        """Rend l'adresse complète, prête à coller dans un courriel.

        ⚠️ Le même droit que publier : la méthode est publique, donc appelable
        par RPC par quiconque lit le projet. Sans la garde, un lecteur se
        fabriquerait l'adresse privée du client et pourrait la distribuer.
        """
        self.ensure_one()
        self._progression_exiger_le_droit_de_publier()
        self._progression_assurer_le_jeton()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "info",
                "sticky": True,
                "title": _("Client portal address"),
                "message": self._progression_portal_url(),
            },
        }

    # ------------------------------------------------------------------
    # Ce que voit le client
    # ------------------------------------------------------------------

    def _progression_portal_steps(self):
        """Les étapes que voit le client, dans l'ordre, avec leur état.

        Une étape = un numéro de progression ; plusieurs colonnes du projet peuvent
        en porter le même. Seules comptent les colonnes du projet cochées
        « Visible portail client ».

        L'étape en cours est la PREMIÈRE où il reste une tâche ouverte : celles
        d'avant sont faites, celles d'après sont à venir. S'il n'y a plus rien
        d'ouvert, tout est fait (ou rien n'a commencé, s'il n'y a aucune tâche).

        Rend une liste de dicts : `number`, `name`, `state` (`done`, `current`,
        `upcoming`), `percent` (ou None sans tâche), `hint` (étape en cours
        seulement) et `stage_ids`, pour les modules qui s'accrochent à l'étape.
        """
        self.ensure_one()
        projet = self.sudo()
        colonnes = projet.type_ids.filtered(
            lambda c: c.progression_step_number > 0 and c.progression_client_visible)
        if not colonnes:
            return []
        taches = self.env["project.task"].sudo().search([
            ("project_id", "=", projet.id),
            ("stage_id", "in", colonnes.ids),
        ])

        etapes = {}
        for colonne in colonnes.sorted(lambda c: (c.progression_step_number, c.sequence, c.id)):
            etape = etapes.setdefault(colonne.progression_step_number, {
                "number": colonne.progression_step_number,
                "name": colonne.progression_step_name or colonne.name,
                "hints": [],
                "stage_ids": [],
                "total": 0,
                "closed": 0,
            })
            etape["stage_ids"].append(colonne.id)
            if colonne.progression_client_action_hint:
                etape["hints"].append(colonne.progression_client_action_hint)
            des_siennes = taches.filtered(lambda t, c=colonne: t.stage_id == c)
            etape["total"] += len(des_siennes)
            etape["closed"] += len(des_siennes.filtered(lambda t: t.state in ETATS_FERMES))

        ordre = [etapes[n] for n in sorted(etapes)]
        en_cours = next((i for i, e in enumerate(ordre) if e["total"] > e["closed"]), None)
        if en_cours is None and not taches:
            en_cours = -1  # rien n'a commencé : tout est à venir

        resultat = []
        for i, etape in enumerate(ordre):
            if en_cours is None or i < en_cours:
                etat = "done"
            elif i == en_cours:
                etat = "current"
            else:
                etat = "upcoming"
            resultat.append({
                "number": etape["number"],
                "name": etape["name"],
                "state": etat,
                "percent": (round(100 * etape["closed"] / etape["total"])
                            if etape["total"] else None),
                "hint": "\n\n".join(etape["hints"]) if etat == "current" else "",
                "stage_ids": etape["stage_ids"],
            })
        return resultat

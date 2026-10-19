# -*- coding: utf-8 -*-
"""Le formulaire ne nomme que ce qu'on peut lire.

🔴 `onchange` (module web) rend le nom affiché des many2one de la fiche, valeurs ET
`default_*` du contexte compris, et le calcule sans regarder les droits de l'appelant :
un `default_result_bookmark_id` ou un `default_folder_id` choisi par RPC rendait le
titre d'un signet perso d'autrui, ou le chemin complet d'un dossier perso. Les
identifiants se suivent : tout se laissait énumérer, sans rien créer.

Le garde refuse, avant `onchange`, tout signet ou dossier que la personne ne peut pas
lire. Il lit aussi ce que rend `default_get` : chacun peut se poser ses propres valeurs
par défaut (`ir.default`, par RPC, sans mode développeur), et `onchange` comme `create`
les servent. « Introuvable » et « interdit » rendent la même réponse. La valeur que la fiche
porte déjà n'est pas revérifiée : la personne qui ouvre une proposition reçue voit le
formulaire tel qu'il est.
"""
from odoo import _, api, models
from odoo.exceptions import AccessError

GUARDED_COMODELS = ("bf.bookmark", "bf.bookmark.folder")


def refuse_unreadable_targets(records, values):
    """Lève si `values` désigne un signet ou un dossier illisible pour l'appelant.
    Les `default_*` du contexte et les `ir.default` arrivent par `default_get`, gardé
    lui aussi."""
    if records.env.su:
        return
    current = records[:1] if len(records) == 1 else None
    for name, field in records._fields.items():
        if field.type != "many2one" or field.comodel_name not in GUARDED_COMODELS:
            continue
        raw = (values or {}).get(name)
        target_id = raw.get("id") if isinstance(raw, dict) else raw
        if not isinstance(target_id, int) or isinstance(target_id, bool) or not target_id:
            continue
        if current is not None and current[name].id == target_id:
            continue
        target = records.env[field.comodel_name].browse(target_id)
        if not target.exists() or not target.has_access("read"):
            raise AccessError(_("Introuvable."))


class OnchangeGuard(models.AbstractModel):
    """Le même garde sur chaque modèle du module qui pointe un signet ou un dossier."""
    _name = "bf.bookmark.onchange.guard"
    _description = "Signets : garde des formulaires"

    def onchange(self, values, field_names, fields_spec):
        refuse_unreadable_targets(self, values)
        return super().onchange(values, field_names, fields_spec)

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        refuse_unreadable_targets(self.browse(), defaults)
        return defaults


class NameGuard(models.AbstractModel):
    """🔴 Le nom affiché d'un signet ou d'un dossier, calculé pour quelqu'un qui ne peut
    pas le lire, ne dit rien.

    Odoo calcule le nom affiché d'un many2one en `sudo()` (`web_read`, donc `onchange`
    et les sous-lignes imbriquées) : quel que soit le chemin qui y mène (une ligne
    d'usage créée dans un formulaire, le signet d'origine d'une proposition vu par son
    décideur, le signet accepté déplacé ensuite), le titre ou le nom d'un dossier perso
    d'autrui sortait. Plutôt que de fermer les chemins un à un, le nom se masque à la
    source : en sudo pour un usager réel qui ne peut pas lire la fiche, il devient
    « Signet » ou « Dossier ». Le superutilisateur (uid 1, les tâches planifiées) reste
    superutilisateur même hors `sudo()` et voit le vrai nom.
    La clé de cache de `uid` porte aussi le drapeau sudo : la valeur masquée n'est
    jamais servie à une lecture normale."""
    _name = "bf.bookmark.name.guard"
    _description = "Signets : nom affiché selon les droits"

    def _masked_display_name(self):
        raise NotImplementedError

    @api.depends_context("lang", "uid", "allowed_company_ids")
    def _compute_display_name(self):
        super()._compute_display_name()
        if not self.env.su or not self.ids:
            return
        readable = self.with_env(self.env(su=False)).with_context(
            active_test=False)._filtered_access("read")
        for record in self - readable:
            record.display_name = record._masked_display_name()


class BookmarkFolder(models.Model):
    _name = "bf.bookmark.folder"
    _inherit = ["bf.bookmark.folder", "bf.bookmark.onchange.guard", "bf.bookmark.name.guard"]

    def _masked_display_name(self):
        return _("Dossier")


class Bookmark(models.Model):
    _name = "bf.bookmark"
    _inherit = ["bf.bookmark", "bf.bookmark.onchange.guard", "bf.bookmark.name.guard"]

    def _masked_display_name(self):
        return _("Signet")


class BookmarkUsage(models.Model):
    _name = "bf.bookmark.usage"
    _inherit = ["bf.bookmark.usage", "bf.bookmark.onchange.guard"]


class BookmarkProposal(models.Model):
    _name = "bf.bookmark.proposal"
    _inherit = ["bf.bookmark.proposal", "bf.bookmark.onchange.guard"]

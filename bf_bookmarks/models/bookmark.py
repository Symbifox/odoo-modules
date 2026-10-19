# -*- coding: utf-8 -*-
from urllib.parse import urlsplit

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

# 🔴 Le web, et rien d'autre. Un signet d'entreprise part dans le navigateur de tout
# le personnel : `javascript:` s'exécuterait dans la page ouverte au clic, et
# `file://hote/partage` fait fuir sous Windows une empreinte NTLM vers l'hôte nommé.
# `ftp:` n'est plus ouvert par aucun navigateur courant.
ALLOWED_SCHEMES = ("http", "https")

# Recopiés du dossier, et rien d'autre ne doit les fixer. 🔴 L'ORM accepte d'ÉCRIRE un
# champ `related` stocké (à la création, il l'ignore) : écrits directement par RPC, ils
# sortaient un signet de son dossier aux yeux des règles. Un signet perso devenait
# « entreprise », visible de tout le personnel, sans passer par le contrôle d'après,
# qui ne jouait que sur `folder_id`.
DERIVED_FIELDS = ("scope", "company_id", "department_id", "project_id", "owner_id")


class Bookmark(models.Model):
    _name = "bf.bookmark"
    _description = "Signet"
    _order = "sequence, name, id"

    name = fields.Char(string="Titre", required=True)
    url = fields.Char(string="Adresse", required=True)
    description = fields.Text(string="Note")
    sequence = fields.Integer(string="Séquence", default=10)
    active = fields.Boolean(default=True)
    folder_id = fields.Many2one(
        "bf.bookmark.folder", string="Dossier", required=True, ondelete="cascade",
        index=True, default=lambda self: self.env["bf.bookmark.folder"]._personal_root())

    archived_from_browser = fields.Boolean(
        string="Supprimé depuis le navigateur", readonly=True, copy=False)
    archived_date = fields.Datetime(string="Supprimé le", readonly=True, copy=False)

    # Recopiés du dossier pour que les règles d'accès se lisent sur la fiche.
    scope = fields.Selection(related="folder_id.scope", store=True, index=True)
    company_id = fields.Many2one(related="folder_id.company_id", store=True, index=True)
    department_id = fields.Many2one(related="folder_id.department_id", store=True, index=True)
    project_id = fields.Many2one(related="folder_id.project_id", store=True, index=True)
    owner_id = fields.Many2one(related="folder_id.owner_id", store=True, index=True)

    @api.model
    def _clean_url(self, url):
        url = (url or "").strip()
        parts = urlsplit(url)
        if parts.scheme.lower() not in ALLOWED_SCHEMES:
            raise ValidationError(_(
                "Adresse refusée : « %(url)s ». Seules les adresses %(schemes)s sont "
                "acceptées.", url=url[:80], schemes=", ".join(ALLOWED_SCHEMES)))
        if not parts.netloc:
            raise ValidationError(_("Adresse incomplète : « %s ».", url[:80]))
        return url

    @api.constrains("url")
    def _check_url_scheme(self):
        """🔴 Le même filtre que `create`/`write`, mais sur ce qui est ENREGISTRÉ : une
        adresse fournie par `default_url` dans le contexte, par une commande one2many
        ou par `copy` ne passait pas par le nettoyage, et `javascript:` partait dans
        le navigateur de tout le personnel par l'extension."""
        for mark in self:
            self._clean_url(mark.url)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if "url" in vals:
                vals["url"] = self._clean_url(vals["url"])
        records = super().create(vals_list)
        records._check_derived_fields()
        return records

    def write(self, vals):
        if "url" in vals:
            vals["url"] = self._clean_url(vals["url"])
        result = super().write(vals)
        if "folder_id" in vals:
            self._check_target_after_write()
        self._check_derived_fields()
        return result

    def _check_derived_fields(self):
        """Les champs recopiés disent ce que dit le dossier, quel que soit le chemin
        par lequel la fiche a été écrite (valeurs, `default_*` du contexte, sudo)."""
        if not self:
            return
        self.flush_recordset()
        for mark in self.sudo().with_context(active_test=False):
            if any(mark[field] != mark.folder_id[field] for field in DERIVED_FIELDS):
                raise AccessError(_("La portée d'un signet suit son dossier : déplacez-le."))

    def _check_target_after_write(self):
        """Refuse un signet déplacé là où on n'a pas le droit d'écrire.

        Voir `bf.bookmark.folder` : Odoo ne vérifie la règle d'une écriture que sur
        l'état d'avant.
        """
        if not self:
            return
        self.flush_recordset()
        self.invalidate_recordset()
        self.check_access("write")

    def _archive_from_browser(self):
        self.ensure_one()
        self.write({"active": False, "archived_from_browser": True,
                    "archived_date": fields.Datetime.now()})

    def action_restore_from_browser(self):
        """Restaure des signets supprimés depuis le navigateur, et leurs dossiers."""
        folders = self.with_context(active_test=False).folder_id.filtered(lambda f: not f.active)
        if folders:
            folders.action_restore_from_browser()
        self.with_context(active_test=False).write(
            {"active": True, "archived_from_browser": False, "archived_date": False})
        return True

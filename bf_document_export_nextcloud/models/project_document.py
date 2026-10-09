import logging
import os
import re

from odoo import models
from odoo.exceptions import UserError

from .export_template import nc_under

_logger = logging.getLogger(__name__)


# A folder holds several files: the published PDF of the document's language comes
# first, then its other formats.
FOLDER_PREFERENCE = (".pdf", ".docx", ".odt", ".pptx", ".odp", ".xlsx", ".ods")
# The language a file name ends with: « - FR », « (EN) », « - ENG ».
LANGUAGE_TAG = re.compile(r"[\s(_-]+(FRA|FR|ENG|EN)\)?$", re.IGNORECASE)
LANGUAGE_CODES = {"FRA": "FR", "FR": "FR", "ENG": "EN", "EN": "EN"}


def _language_of(name):
    match = LANGUAGE_TAG.search(os.path.splitext(name)[0].strip())
    return LANGUAGE_CODES[match.group(1).upper()] if match else None


class ProjectDocument(models.Model):
    _inherit = "project.document"

    def _bf_export_pick_in_folder(self, names):
        """The file of a folder that stands for this document, or None.

        A deck's folder holds « <date> - <Nom> (<module>) - FR|EN.pdf|pptx »: the
        file whose name ends with the document's language, in the first format of
        FOLDER_PREFERENCE. Older decks write « (FR) », or tag only the English file
        (« - ENG »): when only the other language is tagged, the untagged files are
        this document's. A folder with a single file gives that file.
        """
        langue = (self.language or "")[:2].upper()
        tags = {n: _language_of(n) for n in names}
        if any(tags.values()):
            siens = [n for n in names if tags[n] == langue] or [n for n in names if tags[n] is None]
        else:
            siens = list(names)
        for extension in FOLDER_PREFERENCE:
            choix = [n for n in siens if n.lower().endswith(extension)]
            if len(choix) == 1:
                return choix[0]
            if len(choix) > 1:
                return None
        return names[0] if len(names) == 1 else None

    def _bf_export_external_file(self, version=None, cache=None):
        """A file attached to the version wins; otherwise read the live Nextcloud file.

        The live file is what the document points to today. For the version in
        force that is the right content; archived versions without an attached
        file are left out by the exporter rather than shown with today's text.
        """
        found = super()._bf_export_external_file(version, cache)
        if found:
            return found
        self.ensure_one()
        cache = {} if cache is None else cache
        if not self.nc_file_path:
            # Say what the registry holds instead, so the record can be fixed.
            source_path = getattr(self, "source_path", False)
            external_url = getattr(self, "external_url", False)
            if source_path:
                cache["reason"] = (self.env._(
                    "source path « %s » without a Nextcloud link, document left out", source_path), False)
            elif external_url:
                cache["reason"] = (self.env._(
                    "external link only (%s), document left out", external_url), False)
            return None
        # The template names the Nextcloud and the folders an export may read: the
        # configuration's service account reads anything, whoever linked the file.
        # A record without a configuration (the twin of a translated pair, often)
        # is read through the template's.
        config = cache.get("nc_source_config")
        folders = cache.get("nc_source_folders") or []
        if not (config and folders):
            # Shared: reported once, with the list of the documents it kept out.
            cache["reason"] = (self.env._(
                "no Nextcloud source (configuration and folders) set on the export template"), True)
            return None
        if self.nc_config_id and self.nc_config_id != config:
            cache["reason"] = (self.env._(
                "Nextcloud configuration « %s » is not the template's source, document left out",
                self.nc_config_id.name), False)
            return None
        if not any(nc_under(self.nc_file_path, folder) for folder in folders):
            cache["reason"] = (self.env._(
                "Nextcloud path « %s » outside the template's source folders, document left out",
                self.nc_file_path), False)
            return None
        unusable = cache.setdefault("nc_unusable", {})
        if config.id not in unusable and config.id not in cache.setdefault("nc_checked", set()):
            # One check per configuration: a missing key or password would
            # otherwise fail, and log, once per document.
            try:
                config._get_auth()
                cache["nc_checked"].add(config.id)
            except UserError as error:
                unusable[config.id] = self.env._(
                    "Nextcloud « %(name)s » unreadable (%(error)s)", name=config.name, error=str(error))
        if config.id in unusable:
            cache["reason"] = (unusable[config.id], True)
            return None
        path = self.nc_file_path.rstrip("/") or "/"
        if not os.path.splitext(os.path.basename(path))[1] or self.nc_file_path.endswith("/"):
            # A path without an extension is often a folder (a slide deck's folder,
            # for one): a GET on it answers a short WebDAV notice, not the document.
            try:
                entries = config._webdav_propfind(path, depth="1")
            except UserError:
                entries = []
            if entries and entries[0].get("is_dir"):
                names = [e["name"] for e in entries[1:] if not e.get("is_dir")]
                chosen = self._bf_export_pick_in_folder(names)
                if not chosen:
                    cache["reason"] = (self.env._(
                        "Nextcloud folder %(folder)s: no single file for language %(lang)s among %(count)s",
                        folder=path, lang=(self.language or "?")[:2].upper(), count=len(names)), False)
                    return None
                path = f"{path}/{chosen}"
        try:
            content = config._webdav_get(path)
        except UserError as error:
            text = str(error)
            if "HTTP 401" in text or "HTTP 403" in text or "telechargement" in text:
                unusable[config.id] = self.env._(
                    "Nextcloud « %(name)s » unreadable (%(error)s)", name=config.name, error=text)
                cache["reason"] = (unusable[config.id], True)
            else:
                cache["reason"] = (self.env._("not found on Nextcloud: %s", path), False)
            _logger.info("Export: %s unreadable on Nextcloud (%s)", path, text)
            return None
        return os.path.basename(path), content

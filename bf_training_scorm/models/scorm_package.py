import base64
import io
import logging
import posixpath
import zipfile

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

#: Les deux familles, et leurs espaces de noms de manifeste.
VERSIONS = [
    ("1.2", "SCORM 1.2"),
    ("2004", "SCORM 2004"),
]

#: ⚠️ Le *schemaversion* d'un manifeste n'est pas normalisé au caractère près.
#: On a relevé « 1.2 », « CAM 1.3 », « 2004 3rd Edition », « 2004 4th Edition ».
#: On classe donc par appartenance, jamais par égalité.
_MARQUEURS_2004 = ("2004", "cam 1.3", "1.3")


class ScormPackage(models.Model):
    _name = "bf.scorm.package"
    _description = "Paquet SCORM"
    _order = "id desc"

    name = fields.Char(string="Nom", required=True)
    slide_id = fields.Many2one(
        "slide.slide", string="Contenu", ondelete="cascade", index=True)
    archive = fields.Binary(string="Paquet (.zip)", attachment=True, required=True)
    archive_filename = fields.Char(string="Nom du fichier")
    version = fields.Selection(VERSIONS, string="Version", readonly=True)
    launch_href = fields.Char(string="Point d'entrée", readonly=True)
    identifier = fields.Char(string="Identifiant du manifeste", readonly=True)
    sco_count = fields.Integer(string="Nombre de SCO", readonly=True)
    uses_sequencing = fields.Boolean(
        string="Déclare du séquencement", readonly=True,
        help="Le manifeste porte des règles de séquencement SCORM 2004. Elles ne "
             "sont PAS appliquées : le contenu jouera son premier SCO.")
    attempt_ids = fields.One2many("bf.scorm.attempt", "package_id", string="Tentatives")

    # ------------------------------------------------------------------
    # Lecture du manifeste
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        paquets = super().create(vals_list)
        paquets._lire_le_manifeste()
        return paquets

    def write(self, vals):
        resultat = super().write(vals)
        if "archive" in vals:
            self._lire_le_manifeste()
        return resultat

    def _ouvrir(self):
        """L'archive, ouverte en lecture.

        🔴 Refusée si elle n'est pas un zip lisible. Un paquet cassé accepté au
        dépôt se découvre au premier clic d'un apprenant, c'est-à-dire au pire
        moment et par la pire personne.
        """
        self.ensure_one()
        if not self.archive:
            raise UserError(_("Aucun paquet déposé."))
        try:
            return zipfile.ZipFile(io.BytesIO(base64.b64decode(self.archive)))
        except (zipfile.BadZipFile, ValueError) as exc:
            raise UserError(
                _("Ce fichier n'est pas une archive lisible : %s", exc)) from exc

    def _lire_le_manifeste(self):
        for paquet in self:
            if not paquet.archive:
                continue
            with paquet._ouvrir() as zf:
                noms = zf.namelist()
                if "imsmanifest.xml" not in noms:
                    raise UserError(_(
                        "Ce paquet n'a pas d'« imsmanifest.xml » à sa racine : "
                        "ce n'est pas un paquet SCORM, ou il a été rezippé avec "
                        "son dossier parent."))
                brut = zf.read("imsmanifest.xml")
            paquet._appliquer_manifeste(brut)

    def _appliquer_manifeste(self, brut):
        self.ensure_one()
        try:
            racine = etree.fromstring(brut)
        except etree.XMLSyntaxError as exc:
            raise UserError(
                _("Le manifeste de ce paquet est illisible : %s", exc)) from exc

        ns = {k: v for k, v in (racine.nsmap or {}).items() if k}
        defaut = racine.nsmap.get(None)
        if defaut:
            ns["ims"] = defaut
        prefixe = "ims:" if defaut else ""

        # La version : d'abord `schemaversion`, puis l'espace de noms ADL.
        schema = racine.findtext(f".//{prefixe}schemaversion", namespaces=ns) or ""
        version = "2004" if any(
            m in schema.lower() for m in _MARQUEURS_2004) else "1.2"
        if not schema and any("adlseq" in (v or "") for v in (racine.nsmap or {}).values()):
            version = "2004"

        ressources = racine.findall(f".//{prefixe}resource", namespaces=ns)
        # Le point d'entrée : la première ressource jouable (scormtype=sco),
        # à défaut la première qui porte un href.
        lancement = None
        sco = 0
        for res in ressources:
            type_scorm = ""
            for cle, valeur in res.attrib.items():
                # 🔴 La CASSE de cet attribut diffère entre les deux normes :
                # SCORM 1.2 écrit `adlcp:scormtype`, SCORM 2004 `adlcp:scormType`.
                # Une comparaison sensible à la casse ne voit qu'une famille sur
                # deux — et comme le point d'entrée a un repli, le paquet se
                # lançait quand même : seul le compte de SCO tombait à zéro,
                # silencieusement.
                if cle.rsplit("}", 1)[-1].lower() == "scormtype":
                    type_scorm = (valeur or "").lower()
            href = res.get("href")
            if type_scorm == "sco":
                sco += 1
                if href and not lancement:
                    lancement = href
        if not lancement:
            for res in ressources:
                if res.get("href"):
                    lancement = res.get("href")
                    break
        if not lancement:
            raise UserError(_(
                "Aucune ressource jouable dans ce manifeste : il ne déclare "
                "aucun « href »."))

        sequencement = bool(racine.findall(".//{*}sequencing"))
        self.write({
            "version": version,
            "launch_href": lancement,
            "identifier": racine.get("identifier") or "",
            "sco_count": sco,
            "uses_sequencing": sequencement,
        })

    # ------------------------------------------------------------------
    # Service des fichiers
    # ------------------------------------------------------------------
    def _lire_fichier(self, chemin):
        """Un fichier du paquet, ou None.

        🔴 La seule garde qui compte ici : un chemin qui SORT du paquet est
        refusé, quelle que soit sa forme. `posixpath.normpath` réduit d'abord
        `a/../../etc/passwd` à `../etc/passwd`, et tout ce qui commence par
        « .. » ou par « / » après normalisation est dehors. Comparer des chaînes
        brutes laisserait passer `a/./../../`, l'encodage `%2e%2e`, et les
        barres obliques inverses de Windows.
        """
        self.ensure_one()
        normalise = posixpath.normpath((chemin or "").replace("\\", "/").lstrip("/"))
        if normalise.startswith("..") or normalise.startswith("/") or normalise == ".":
            return None
        with self._ouvrir() as zf:
            try:
                return zf.read(normalise)
            except KeyError:
                return None

import base64
import hashlib
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Les champs dont l'écriture remplace réellement des octets. `checksum`,
# `file_size` et `store_fname` n'y sont pas : `ir.attachment.write` les retire
# des valeurs avant d'écrire, ils ne peuvent donc pas être le signal.
CHAMPS_CONTENU = ("raw", "datas", "db_datas")


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    version_ids = fields.One2many(
        "bf.attachment.version", "attachment_id", string="Versions conservées")
    version_count = fields.Integer(
        string="Nombre de versions", compute="_compute_version_count")

    def _compute_version_count(self):
        comptes = {}
        if self.ids:
            groupes = self.env["bf.attachment.version"]._read_group(
                [("attachment_id", "in", self.ids)], ["attachment_id"], ["__count"])
            comptes = {piece.id: nombre for piece, nombre in groupes}
        for piece in self:
            piece.version_count = comptes.get(piece.id, 0)

    # ------------------------------------------------------------------
    # Le crochet
    # ------------------------------------------------------------------
    def write(self, vals):
        instantanes = []
        if not self.env.context.get("bf_sans_version"):
            instantanes = self._bf_preparer_instantanes(vals)
        resultat = super().write(vals)
        if instantanes:
            self.env["bf.attachment.version"].sudo()._bf_enregistrer(instantanes)
        return resultat

    def unlink(self):
        """Emporter les versions AVANT que la cascade SQL ne les efface en silence.

        ``attachment_id`` porte ``ondelete='cascade'`` : sans ce passage, les
        lignes de version disparaîtraient sans que leur ``unlink`` Python tourne,
        et chaque pièce de conservation resterait orpheline avec son fichier.
        """
        if self.ids:
            versions = self.env["bf.attachment.version"].sudo().search(
                [("attachment_id", "in", self.ids)])
            if versions:
                versions.unlink()
        return super().unlink()

    # ------------------------------------------------------------------
    # Décider quoi conserver
    # ------------------------------------------------------------------
    def _bf_preparer_instantanes(self, vals):
        """Lire les octets d'AVANT, tant qu'ils sont encore là.

        La lecture doit précéder ``super().write`` : l'inverse de ``raw`` réécrit
        ``store_fname`` puis marque l'ancien fichier pour le ramasse-miettes.
        """
        if not any(champ in vals for champ in CHAMPS_CONTENU):
            return []
        Version = self.env["bf.attachment.version"]
        if not Version._bf_actif():
            return []

        empreinte_entrante = self._bf_empreinte_entrante(vals)
        origine = self._bf_origine()
        extensions = Version._bf_extensions()
        exclus = Version._bf_modeles_exclus()
        taille_max = Version._bf_taille_max_octets()

        prets = []
        for piece in self:
            if not piece._bf_versionnable(extensions, exclus, taille_max):
                continue
            # Une réécriture à l'identique ne remplace rien. Sans ce contrôle,
            # `force_storage` d'Odoo, qui réécrit chaque pièce avec son propre
            # contenu, fabriquerait une version par pièce du parc.
            if empreinte_entrante and empreinte_entrante == piece.checksum:
                continue
            octets = piece.raw
            if not octets:
                continue
            prets.append({
                "attachment_id": piece.id,
                "name": piece.name,
                "mimetype": piece.mimetype,
                "file_size": piece.file_size,
                "checksum": piece.checksum,
                "origine": origine,
                "raw": octets,
            })
        return prets

    def _bf_versionnable(self, extensions, exclus, taille_max):
        self.ensure_one()
        if self.type != "binary":
            return False
        # Une pièce portant `res_field` est le STOCKAGE d'un champ binaire
        # (icône, image, tableur intégré), pas un document. La versionner
        # doublerait le poids de chaque champ image à chaque retouche.
        if self.res_field:
            return False
        if self.res_model and self.res_model in exclus:
            return False
        morceaux = (self.name or "").rsplit(".", 1)
        if len(morceaux) != 2 or morceaux[1].lower() not in extensions:
            return False
        if taille_max and (self.file_size or 0) > taille_max:
            return False
        return True

    @api.model
    def _bf_empreinte_entrante(self, vals):
        """L'empreinte du contenu qui ARRIVE, quand on peut la calculer.

        Rend ``None`` quand le contenu n'est pas lisible dans les valeurs : on
        conserve alors par précaution, plutôt que de supposer qu'il est
        identique.
        """
        donnees = None
        if "raw" in vals:
            donnees = vals["raw"]
            if isinstance(donnees, str):
                donnees = donnees.encode()
        elif "datas" in vals:
            try:
                donnees = base64.b64decode(vals["datas"] or b"")
            except Exception:
                return None
        elif "db_datas" in vals:
            donnees = vals["db_datas"]
        if donnees is None or isinstance(donnees, memoryview):
            donnees = bytes(donnees) if donnees is not None else None
        if not isinstance(donnees, (bytes, bytearray)):
            return None
        return hashlib.sha1(bytes(donnees) or b"").hexdigest()

    @api.model
    def _bf_origine(self):
        """D'où vient le remplacement, lu sur la requête en cours.

        Aucun des deux connecteurs bureautiques n'est un module maison : on ne
        peut pas leur demander de poser un indicateur. Le chemin HTTP, lui, est
        un fait observable.
        """
        try:
            from odoo.http import request
            chemin = request.httprequest.path or ""
        except Exception:
            return "autre"
        if chemin.startswith("/onlyoffice/"):
            return "onlyoffice"
        if chemin.startswith("/collabora_odoo/"):
            return "collabora"
        if chemin.startswith("/web/") or chemin.startswith("/mail/"):
            return "interface"
        return "autre"

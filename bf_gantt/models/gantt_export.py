# -*- coding: utf-8 -*-
"""Le guichet des sorties.

Un seul endroit sait quels formats existent, comment ils se nomment et quel
type MIME les accompagne. Le composant OWL, le portail et l'assistant d'import
passent tous par ici : ajouter un format se fait à un seul endroit, et personne
ne peut fabriquer un nom de fichier de son côté.
"""
import base64
import re
import unicodedata
from datetime import date

from odoo import _, api, models
from odoo.exceptions import UserError

from ..generateur import mspdi as gen_mspdi
from ..generateur import pdf as gen_pdf
from ..generateur import png as gen_png
from ..generateur import svg as gen_svg
from ..generateur import xlsx as gen_xlsx

FORMATS = {
    "pdf": ("application/pdf", "pdf"),
    "png": ("image/png", "png"),
    "svg": ("image/svg+xml", "svg"),
    "xlsx": ("application/vnd.openxmlformats-officedocument."
             "spreadsheetml.sheet", "xlsx"),
    "mspdi": ("application/xml", "xml"),
}


def epurer(nom, defaut="echeancier"):
    """Un nom de fichier sûr : sans accent, sans espace, sans séparateur."""
    nom = unicodedata.normalize("NFKD", nom or "")
    nom = nom.encode("ascii", "ignore").decode("ascii")
    nom = re.sub(r"[^A-Za-z0-9]+", "-", nom).strip("-").lower()
    return nom[:60] or defaut


class BfGanttExport(models.AbstractModel):
    _name = "bf.gantt.export"
    _description = "Sorties de fichiers d'un échéancier"

    @api.model
    def formats(self):
        return [
            {"key": "pdf", "label": _("PDF"), "hint": _("Vectoriel, pour imprimer")},
            {"key": "png", "label": _("PNG"), "hint": _("Image, pour un courriel")},
            {"key": "svg", "label": _("SVG"), "hint": _("Vectoriel, pour retoucher")},
            {"key": "xlsx", "label": _("Excel"), "hint": _("Tableau, réimportable")},
            {"key": "mspdi", "label": _("MS Project"),
             "hint": _("MSPDI, aussi lu par OpenProject")},
        ]

    def _rendre(self, payload, format_, echelle="week", zoom=1.0):
        """Rend (octets, type MIME, nom de fichier) pour un échéancier déjà lu.

        🔴 Privée, et le trait de soulignement est la garde. Publique, elle était
        appelable par RPC **par n'importe quel usager connecté, portail compris**,
        avec un `payload` fabriqué de toutes pièces : de quoi faire tracer une
        plage absurde sans posséder le moindre enregistrement. Les points
        d'entrée légitimes (`telecharger`, `joindre`, le portail) lisent tous la
        source, qui vérifie les droits.

        ⚠️ `zoom` ne concerne que ce qui se regarde à l'écran, PNG et SVG. Le PDF
        garde son échelle 1:1 : une page d'impression agrandie ne dit plus la
        vérité sur ses dimensions, et l'imprimante refait le travail de toute
        façon.
        """
        if format_ not in FORMATS:
            raise UserError(_("Format inconnu : %s", format_))
        mime, extension = FORMATS[format_]

        if format_ == "pdf":
            contenu = gen_pdf.rendre(
                payload, echelle=echelle,
                titre_pied=payload.get("company", {}).get("name", ""))
        elif format_ == "png":
            contenu = gen_png.rendre(payload, echelle=echelle, zoom=zoom)
        elif format_ == "svg":
            contenu = gen_svg.rendre(payload, echelle=echelle, zoom=zoom)
        elif format_ == "xlsx":
            contenu = gen_xlsx.rendre(payload)
        else:
            contenu = gen_mspdi.rendre(payload)

        nom = "%s-%s.%s" % (epurer(payload.get("title")),
                            date.today().isoformat(), extension)
        return contenu, mime, nom

    @api.model
    def telecharger(self, kind, res_id, format_, echelle="week", grouping="stage",
                    zoom=1.0):
        """Ce que le bouton du composant OWL appelle.

        Rend le contenu en base64 : c'est ce que le client sait transformer en
        téléchargement sans passer par une route de plus.
        """
        payload = self.env["bf.gantt.source"].get_echeancier(
            kind, res_id, grouping=grouping)
        contenu, mime, nom = self._rendre(payload, format_, echelle=echelle,
                                          zoom=zoom)
        return {
            "name": nom,
            "mimetype": mime,
            "content": base64.b64encode(contenu).decode("ascii"),
        }

    @api.model
    def joindre(self, kind, res_id, format_, echelle="week", grouping="stage"):
        """Dépose la sortie en pièce jointe sur l'enregistrement d'origine.

        Utile pour un devis ou un compte rendu : le fichier reste au dossier au
        lieu de vivre dans le dossier de téléchargement de quelqu'un.
        """
        modele = "bf.gantt.plan" if kind == "plan" else "project.project"
        # ⚠️ Déposer une pièce jointe et poster au fil est une ÉCRITURE. S'en
        # remettre au contrôle de `ir.attachment` marchait, mais la garde du
        # module était alors plus faible que son effet.
        self.env[modele].browse(int(res_id)).check_access("write")
        payload = self.env["bf.gantt.source"].get_echeancier(
            kind, res_id, grouping=grouping)
        contenu, mime, nom = self._rendre(payload, format_, echelle=echelle)
        piece = self.env["ir.attachment"].create({
            "name": nom,
            "datas": base64.b64encode(contenu),
            "mimetype": mime,
            "res_model": modele,
            "res_id": int(res_id),
        })
        enregistrement = self.env[modele].browse(int(res_id))
        if hasattr(enregistrement, "message_post"):
            # ⚠️ `attachment_ids` sur `message_post` rattache bien la pièce au
            # message ; la poser seulement par `res_id` la laisse invisible dans
            # le fil.
            enregistrement.message_post(
                body=_("Échéancier exporté : %s", nom),
                attachment_ids=[piece.id],
            )
        return {"attachment_id": piece.id, "name": nom}

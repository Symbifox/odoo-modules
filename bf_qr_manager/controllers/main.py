"""La porte /q/ : celle qu'imprime une étiquette QR.

Un code QR se scanne avec l'appareil photo, par n'importe qui, connecté ou non.
La porte /nfc/ du socle exige une session et enverrait donc tout visiteur à
l'écran de connexion. Celle-ci trie d'abord :

* code inconnu ou retiré : **404 franc**, comme le socle ;
* étiquette vierge : l'association pour qui en a le droit, une page neutre pour
  les autres ;
* visiteur sans compte : l'adresse publique si l'étiquette en a une, sinon la
  connexion, qui ramène ici ;
* personne connectée : la porte /nfc/ du socle, avec ses confirmations.

🔴 **La porte publique n'exécute aucun geste.** Elle ne fait que suivre une
adresse calculée par ``_qr_adresse_publique``, qui refuse tout geste qui écrit.
Un scan anonyme ne laisse que sa ligne de journal et le compteur de scans de
l'étiquette.
"""
import io
from urllib.parse import quote

from werkzeug.exceptions import Forbidden, NotFound

from odoo import _, fields, http
from odoo.http import content_disposition, request


class PorteQr(http.Controller):

    @http.route("/q/<string:code>", type="http", auth="public", methods=["GET"],
                website=True, sitemap=False)
    def ouvrir(self, code, **kw):
        tag = request.env["bf.nfc.tag"]._resoudre(code)
        if not tag:
            raise NotFound()
        user = request.env.user
        anonyme = user._is_public()
        if tag.qr_vierge:
            if not anonyme and tag.with_user(user)._peut_associer():
                return request.redirect("/odoo/bf.nfc.tag/%s" % tag.id)
            return request.render("bf_qr_manager.page_vierge", {
                "tag": tag, "anonyme": anonyme,
                "connexion": "/web/login?redirect=%s" % quote("/q/%s" % tag.code),
            })
        if anonyme:
            adresse = tag._qr_adresse_publique()
            if adresse:
                tag._journaliser("qr_public", "ok", _("Adresse publique ouverte."),
                                 url=adresse, acteur=user)
                tag.write({"tap_count": tag.tap_count + 1,
                           "last_tap_date": fields.Datetime.now()})
                return request.redirect(adresse, local=False)
            return request.redirect("/web/login?redirect=%s" % quote("/q/%s" % tag.code))
        return request.redirect("/nfc/%s" % tag.code)

    @http.route("/bf_qr/planche/<int:assistant_id>", type="http", auth="user", methods=["GET"])
    def planche(self, assistant_id, **kw):
        # ⚠️ Un modèle transitoire ne se lit que par qui l'a créé : un identifiant
        # deviné ne sort pas les étiquettes de quelqu'un d'autre.
        assistant = request.env["bf.qr.imprimer"].browse(assistant_id).exists()
        if not assistant or assistant.create_uid != request.env.user:
            raise NotFound()
        pdf = assistant._pdf()
        return request.make_response(pdf, headers=[
            ("Content-Type", "application/pdf"),
            ("Content-Disposition", content_disposition("etiquettes-qr.pdf", "inline")),
        ])

    @http.route("/bf_qr/modele/<int:batch_id>", type="http", auth="user", methods=["GET"])
    def modele(self, batch_id, **kw):
        """Le tableur d'association d'un lot, prérempli de ses étiquettes vierges."""
        import openpyxl
        from openpyxl.styles import Font

        lot = request.env["bf.qr.batch"].browse(batch_id).exists()
        if not lot:
            raise NotFound()
        vierges = lot.tag_ids.filtered(lambda t: t.active and t.qr_vierge)
        if not vierges._peut_associer():
            raise Forbidden()
        classeur = openpyxl.Workbook()
        feuille = classeur.active
        feuille.title = "Associations"
        entete = ["etiquette", "type", "fiche", "adresse", "geste", "libelle", "posee_sur", "public"]
        feuille.append(entete)
        for cellule in feuille[1]:
            cellule.font = Font(bold=True)
        for tag in vierges.sorted("qr_numero"):
            feuille.append([tag.qr_reference, "", "", "", "", "", "", ""])
        for colonne, largeur in zip("ABCDEFGH", (14, 22, 28, 36, 10, 24, 20, 8)):
            feuille.column_dimensions[colonne].width = largeur
        tampon = io.BytesIO()
        classeur.save(tampon)
        nom = "associations-%s.xlsx" % (lot.prefixe or "qr").lower()
        return request.make_response(tampon.getvalue(), headers=[
            ("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("Content-Disposition", content_disposition(nom)),
        ])

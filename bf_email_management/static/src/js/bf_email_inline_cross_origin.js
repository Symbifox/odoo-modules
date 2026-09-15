/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { HtmlMailField } from "@mail/views/web/fields/html_mail_field/html_mail_field";

/*
 * Envoyer un courriel qui cite une image d'un autre domaine.
 *
 * Avant qu'un courriel quitte le composeur, Odoo redessine chaque image .svg
 * et .webp sur un canevas et la remplace par le PNG qu'il en relit
 * (`convertToPng`, mail/.../html_mail_field/convert_inline.js). Or un
 * navigateur refuse de relire un canevas qui a reçu une image d'un autre
 * domaine servie sans en-tête CORS : `toDataURL` lève `SecurityError`
 * (« Tainted canvases may not be exported »), toute la passe s'arrête, et
 * le courriel ne part pas.
 *
 * Il suffit de répondre à un courriel qui contient une telle image, y compris
 * une annonce de son propre site : un site servi sous « www. » alors que le
 * poste tourne sur le domaine nu, ce sont deux domaines pour le navigateur.
 * Le code est le même dans Odoo 18.0 en amont.
 *
 * `convertToPng` est privée à son module : on ne peut pas la patcher. La
 * garde se place donc un cran plus bas, et seulement le temps de la passe :
 * `drawImage` note l'adresse de l'image que chaque canevas a reçue, et un
 * `toDataURL` que le navigateur refuse rend cette adresse au lieu de lever.
 * L'image repart alors comme elle est arrivée, un .webp ou un .svg distant,
 * avec sa taille affichée fixée en attributs, comme Odoo le fait pour les
 * images qu'il convertit. Une image du même domaine est convertie en PNG
 * comme avant : le navigateur ne refuse rien, la garde ne fait rien.
 */

const nativeDrawImage = CanvasRenderingContext2D.prototype.drawImage;
const nativeToDataURL = HTMLCanvasElement.prototype.toDataURL;
const drawnImageSrc = new WeakMap();
// Deux composeurs peuvent valider en même temps : la garde reste posée tant
// qu'une passe est en cours, et se retire à la fin de la dernière.
let pendingPasses = 0;

function drawImage(image, ...args) {
    // `nodeName` plutôt qu'`instanceof` : l'image peut venir d'un autre
    // document (iframe), dont les classes ne sont pas celles de la fenêtre.
    if (image?.nodeName === "IMG" && image.hasAttribute("src")) {
        drawnImageSrc.set(this.canvas, image.getAttribute("src"));
    }
    return nativeDrawImage.call(this, image, ...args);
}

function toDataURL(...args) {
    try {
        return nativeToDataURL.apply(this, args);
    } catch (error) {
        if (error.name === "SecurityError" && drawnImageSrc.has(this)) {
            return drawnImageSrc.get(this);
        }
        throw error;
    }
}

patch(HtmlMailField, {
    async getInlinedEditorContent(cssRulesByElement, editor, el) {
        if (pendingPasses++ === 0) {
            CanvasRenderingContext2D.prototype.drawImage = drawImage;
            HTMLCanvasElement.prototype.toDataURL = toDataURL;
        }
        try {
            return await super.getInlinedEditorContent(cssRulesByElement, editor, el);
        } finally {
            if (--pendingPasses === 0) {
                CanvasRenderingContext2D.prototype.drawImage = nativeDrawImage;
                HTMLCanvasElement.prototype.toDataURL = nativeToDataURL;
            }
        }
    },
});

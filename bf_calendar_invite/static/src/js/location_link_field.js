/**
 * Le champ « Emplacement » d'une rencontre, rendu cliquable quand il porte une
 * URL.
 *
 * Mesuré sur un calendrier réel avant d'écrire quoi que ce soit : la grande
 * majorité des rencontres qui portent un emplacement n'y ont RIEN d'autre
 * qu'une URL — un salon vidéo, le plus souvent. Quelques-unes cachent l'URL
 * dans une phrase, du genre « Google Meet (instructions dans la description);
 * https://meet.google.com/… ». Le reste, une minorité mais pas une poignée,
 * ce sont des adresses physiques et des mentions comme « Réunion Microsoft
 * Teams ».
 *
 * ⚠️ C'est ce dernier lot qui interdit le widget `url` du cœur, qui aurait été
 * la réponse d'une ligne : il transforme la valeur ENTIÈRE en lien, et
 * préfixe d'un `http://` tout ce qui n'en a pas. « Allianz Stadium
 * (Twickenham), Londres, Angleterre » serait devenu un hyperlien vers
 * `http://Allianz Stadium (Twickenham), Londres, Angleterre`. Un lien mort sur
 * cette minorité-là est pire que pas de lien du tout : il se voit, il s'offre, et
 * il ne mène nulle part.
 *
 * D'où le découpage en segments : seule la portion réellement reconnue comme
 * URL devient un `<a>`, le texte autour reste du texte, et une valeur sans URL
 * se rend exactement comme avant.
 */

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { CharField, charField } from "@web/views/fields/char/char_field";

/**
 * ⚠️ Le schéma est exigé explicitement (`https?://`), et ce n'est pas une
 * commodité : c'est ce qui rend impossible de fabriquer un `javascript:` ou un
 * `data:` en écrivant dans un champ d'emplacement. Le `href` n'est jamais
 * composé, il est recopié tel quel depuis ce que ce motif a reconnu.
 */
const MOTIF_URL = /https?:\/\/[^\s<>"']+/gi;

/**
 * La ponctuation qui suit une URL dans une phrase lui appartient rarement.
 * « …; https://exemple.com/salle. » — le point final est celui de la phrase.
 * Une parenthèse fermante ne se retire QUE si rien ne l'a ouverte dans l'URL,
 * sinon on couperait les liens qui en contiennent légitimement.
 */
function rogner(url) {
    let fin = url.length;
    while (fin > 0) {
        const c = url[fin - 1];
        if (".,;:!?".includes(c)) {
            fin -= 1;
        } else if (c === ")" && !url.slice(0, fin).includes("(")) {
            fin -= 1;
        } else {
            break;
        }
    }
    return url.slice(0, fin);
}

export class BfLocationLinkField extends CharField {
    static template = "bf_calendar_invite.LocationLinkField";

    /**
     * La valeur découpée en morceaux de texte et morceaux cliquables.
     *
     * Rendre une liste de segments plutôt qu'un fragment HTML est délibéré :
     * un `t-out` d'HTML fabriqué ici demanderait de faire confiance à
     * l'échappement fait à la main sur une valeur que n'importe qui peut
     * écrire — y compris par la synchronisation CalDAV entrante, donc depuis
     * l'extérieur. Avec des segments, `t-esc` échappe chaque morceau, et OWL
     * s'en charge.
     */
    get segments() {
        const valeur = this.props.record.data[this.props.name] || "";
        const morceaux = [];
        let curseur = 0;
        for (const trouve of valeur.matchAll(MOTIF_URL)) {
            const url = rogner(trouve[0]);
            if (!url) {
                continue;
            }
            if (trouve.index > curseur) {
                morceaux.push({ text: valeur.slice(curseur, trouve.index) });
            }
            morceaux.push({ text: url, href: url });
            curseur = trouve.index + url.length;
        }
        if (curseur < valeur.length) {
            morceaux.push({ text: valeur.slice(curseur) });
        }
        return morceaux;
    }

    /**
     * La première URL de la valeur, ou rien. Sert au bouton d'ouverture affiché
     * À CÔTÉ du champ en modification : sans lui, le lien ne serait cliquable
     * que sur une fiche en lecture seule, et le formulaire d'une rencontre est
     * modifiable pratiquement tout le temps — donc le lien n'aurait presque
     * jamais été là quand on en a besoin.
     */
    get premierLien() {
        return this.segments.find((m) => m.href)?.href || false;
    }

    get titreLien() {
        return _t("Ouvrir l'emplacement");
    }
}

export const bfLocationLinkField = {
    ...charField,
    component: BfLocationLinkField,
    displayName: _t("Location with clickable links"),
};

registry.category("fields").add("bf_location_link", bfLocationLinkField);

/** @odoo-module **/

/**
 * L'export du coffre, et sa relecture. Entièrement dans le navigateur.
 *
 * Pourquoi ça existe
 * ------------------
 * Avant la 18.0.10.0.0, un coffre n'avait aucune sortie. Les graines vivaient
 * à un seul endroit, ouvrables par une seule phrase, et rien
 * ne permettait d'en sortir : ni changer d'instance, ni remettre le coffre à
 * quelqu'un, ni simplement garder une copie ailleurs. Les sauvegardes tenaient
 * le chiffré, ce qui protège contre la panne mais pas contre l'enfermement.
 *
 * ⚠️ **Le chiffré est le défaut, et il le reste.** Un export en clair est un
 * coffre ouvert qui traîne dans un dossier de téléchargements, et personne ne
 * s'en souvient trois mois plus tard. C'est pour ça qu'il a été refusé du
 * 2026-09-02 au 2026-09-18.
 *
 * 🔴 **Il existe depuis, et il coûte cher exprès** : la phrase du coffre à
 * retaper, un mot à écrire à la main, un
 * avertissement qui ne ressemble à rien d'autre dans le module, et un nom de
 * fichier qui porte « EN-CLAIR » pour que personne ne se trompe sur ce qu'il
 * tient trois mois plus tard. Ce qui manquait n'était pas la prudence, c'était
 * la sortie : sans elle, changer d'outil voulait dire réenrôler cent
 * quarante-cinq comptes à la main.
 *
 * ⚠️ **La phrase de l'export n'est PAS celle du coffre**, et c'est voulu : le
 * fichier part ailleurs, il vit plus longtemps, et il ne doit pas hériter du
 * secret qui ouvre l'instance. Il porte donc son propre sel, son propre nombre
 * d'itérations et son propre témoin.
 */

import {
    deriveKey, encryptText, decryptText, toB64, randomBytes,
} from "./otp_crypto";

export const FORMAT = "symbifox-otp-export";
export const FORMAT_VERSION = 1;

/** Le témoin de l'export : un texte connu, chiffré, qui reconnaît la phrase. */
const TEMOIN = "bf_otp.export.verifier.v1";

/** Les champs recopiés tels quels. La graine, elle, est rechiffrée. */
const CHAMPS = [
    "name", "issuer", "otp_type", "algorithm", "digits", "period",
    "counter", "group_name", "sensitive", "favorite", "archived",
];

export function estUnExportSymbifox(data) {
    return !!data && data.format === FORMAT;
}

/**
 * Fabrique le fichier d'export à partir des tokens déchiffrés en mémoire.
 *
 * ⚠️ `tokens` porte les graines en clair : c'est l'état normal d'un coffre
 * ouvert, mais ça veut dire que cette fonction ne doit jamais être appelée
 * ailleurs que depuis la page, sur une clé déjà en main.
 *
 * Les tokens cassés sont ignorés plutôt qu'exportés vides : un fichier qui
 * contient une ligne sans graine se lit comme une perte, alors que c'est le
 * coffre d'origine qui était déjà abîmé. Le compte des ignorés est rendu.
 */
export async function construireExport(tokens, phrase, iterations) {
    const salt = toB64(randomBytes(16));
    const cle = await deriveKey(phrase, salt, iterations);
    const temoin = await encryptText(cle, TEMOIN);

    const lignes = [];
    let ignores = 0;
    for (const t of tokens) {
        if (t.broken || !t._secret) {
            ignores += 1;
            continue;
        }
        const { cipher, iv } = await encryptText(cle, t._secret);
        const ligne = { secret: { cipher, iv } };
        for (const champ of CHAMPS) {
            ligne[champ] = t[champ] === undefined ? null : t[champ];
        }
        // Les rattachements voyagent par leur NOM, jamais par leur identifiant :
        // un id ne veut rien dire dans une autre base, et il ne dit rien à
        // l'humain qui ouvre le fichier pour comprendre ce qu'il tient.
        ligne.partner = (t.partner_id && t.partner_id[1]) || null;
        ligne.project = (t.project_id && t.project_id[1]) || null;
        lignes.push(ligne);
    }

    return {
        fichier: {
            format: FORMAT,
            version: FORMAT_VERSION,
            exported_at: new Date().toISOString(),
            cipher: "AES-GCM-256",
            kdf: { name: "PBKDF2-SHA256", salt, iterations },
            verifier: { cipher: temoin.cipher, iv: temoin.iv },
            tokens: lignes,
        },
        exportes: lignes.length,
        ignores,
    };
}

/**
 * Relit un export Symbifox et rend les entrées avec leurs graines en clair.
 *
 * Lève `PhraseIncorrecte` si le témoin ne s'ouvre pas, ce qui distingue une
 * mauvaise phrase d'un fichier abîmé : les deux se soignent différemment et
 * confondre les deux fait chercher au mauvais endroit.
 */
export class PhraseIncorrecte extends Error {}

export async function lireExport(data, phrase) {
    if (!estUnExportSymbifox(data)) {
        throw new Error("Ce fichier n'est pas un export Symbifox.");
    }
    if (data.version > FORMAT_VERSION) {
        throw new Error(
            `Cet export est en version ${data.version}, cette page lit jusqu'à ` +
            `la ${FORMAT_VERSION}. Mettez le module à jour avant d'importer.`
        );
    }
    const kdf = data.kdf || {};
    if ((kdf.name || "PBKDF2-SHA256") !== "PBKDF2-SHA256") {
        throw new Error(`Dérivation inconnue : ${kdf.name}`);
    }
    const cle = await deriveKey(phrase, kdf.salt, kdf.iterations || 600000);
    try {
        const lu = await decryptText(cle, data.verifier.cipher, data.verifier.iv);
        if (lu !== TEMOIN) {
            throw new PhraseIncorrecte();
        }
    } catch (e) {
        if (e instanceof PhraseIncorrecte) {
            throw e;
        }
        throw new PhraseIncorrecte();
    }

    const entrees = [];
    const refuses = [];
    for (const l of data.tokens || []) {
        let graine = null;
        try {
            graine = await decryptText(cle, l.secret.cipher, l.secret.iv);
        } catch {
            // Le témoin est passé, donc la phrase est bonne : une ligne qui
            // résiste est une ligne abîmée, pas une phrase à redemander.
            refuses.push(l.name || "?");
            continue;
        }
        const e = { secret: graine, partner: l.partner || null, project: l.project || null };
        for (const champ of CHAMPS) {
            e[champ] = l[champ];
        }
        entrees.push(e);
    }
    return { entrees, refuses };
}


/** Le format du fichier en clair. Nommé pour ne ressembler à rien d'autre. */
export const FORMAT_CLAIR = "symbifox-otp-export-clair";

/**
 * L'adresse `otpauth://` d'un token, telle qu'un autre gestionnaire la lit.
 *
 * ⚠️ Chaque morceau est encodé séparément. Un émetteur qui contient une
 * espace, un deux-points ou une esperluette casserait l'adresse autrement, et
 * l'autre application lirait un compte au nom tronqué sans rien signaler.
 *
 * 🔴 L'émetteur figure DEUX fois, dans l'étiquette et dans le paramètre. Ce
 * n'est pas une redondance : les lecteurs ne s'accordent pas sur celui qu'ils
 * regardent, et n'en poser qu'un fait perdre l'émetteur chez la moitié d'entre
 * eux.
 */
export function adresseOtpauth(t) {
    const e = (t.issuer || "").trim();
    const etiquette = e
        ? `${encodeURIComponent(e)}:${encodeURIComponent(t.name || "")}`
        : encodeURIComponent(t.name || "");
    const p = new URLSearchParams();
    p.set("secret", t._secret);
    if (e) {
        p.set("issuer", e);
    }
    p.set("algorithm", t.algorithm || "SHA1");
    p.set("digits", String(t.digits || 6));
    if ((t.otp_type || "totp") === "hotp") {
        p.set("counter", String(t.counter || 0));
    } else {
        p.set("period", String(t.period || 30));
    }
    // ⚠️ `URLSearchParams` écrit les espaces en « + », que les lecteurs
    // d'adresses otpauth ne décodent pas tous pareil. `%20` est lu par tous.
    return `otpauth://${t.otp_type || "totp"}/${etiquette}?${p.toString().replace(/\+/g, "%20")}`;
}

/**
 * Fabrique le fichier EN CLAIR. Les graines y sont lisibles.
 *
 * 🔴 Aucune phrase, aucun chiffrement, aucun témoin : c'est le propos. Ce qui
 * protège ce fichier, c'est l'endroit où on le met, et rien d'autre. Le fichier
 * le dit lui-même, en première clé, pour la personne qui le rouvrira sans se
 * souvenir de ce que c'est.
 *
 * ⚠️ Les tokens cassés sont comptés et laissés dehors, comme pour le chiffré :
 * une ligne sans graine dans un export en clair se lirait comme une perte,
 * alors que c'est le coffre d'origine qui était déjà abîmé.
 */
export function construireExportClair(tokens) {
    const lignes = [];
    let ignores = 0;
    for (const t of tokens) {
        if (t.broken || !t._secret) {
            ignores += 1;
            continue;
        }
        const ligne = { secret: t._secret, otpauth: adresseOtpauth(t) };
        for (const champ of CHAMPS) {
            ligne[champ] = t[champ] === undefined ? null : t[champ];
        }
        ligne.partner = (t.partner_id && t.partner_id[1]) || null;
        ligne.project = (t.project_id && t.project_id[1]) || null;
        lignes.push(ligne);
    }
    return {
        fichier: {
            format: FORMAT_CLAIR,
            version: FORMAT_VERSION,
            AVERTISSEMENT:
                "Ce fichier contient les graines de vos tokens EN CLAIR. " +
                "Qui l'ouvre peut produire vos codes à usage unique, sans " +
                "mot de passe et sans limite de temps. Rangez-le comme vous " +
                "rangeriez vos mots de passe, ou effacez-le dès qu'il a servi.",
            exported_at: new Date().toISOString(),
            tokens: lignes,
        },
        exportes: lignes.length,
        ignores,
    };
}

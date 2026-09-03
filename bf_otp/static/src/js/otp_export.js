/** @odoo-module **/

/**
 * L'export du coffre, et sa relecture. Entièrement dans le navigateur.
 *
 * Pourquoi ça existe
 * ------------------
 * Avant la 18.0.10.0.0, un coffre n'avait aucune sortie. Cent quarante-quatre
 * graines vivaient à un seul endroit, ouvrables par une seule phrase, et rien
 * ne permettait d'en sortir : ni changer d'instance, ni remettre le coffre à
 * quelqu'un, ni simplement garder une copie ailleurs. Les sauvegardes tenaient
 * le chiffré, ce qui protège contre la panne mais pas contre l'enfermement.
 *
 * ⚠️ **Le fichier est chiffré, et il n'existe pas de version en clair.** Le
 * gestionnaire dont nous importons en offre une, bouton rouge à l'appui ; c'est
 * précisément ce qu'il ne faut pas copier. Un export en clair est un coffre
 * ouvert qui traîne dans un dossier de téléchargements, et personne ne s'en
 * souvient trois mois plus tard.
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
    "counter", "group_name", "sensitive", "favorite",
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

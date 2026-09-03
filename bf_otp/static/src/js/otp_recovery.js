/** @odoo-module **/

/**
 * Le code de relève : ouvrir le coffre quand la phrase est perdue.
 *
 * Le dessin, et pourquoi celui-là
 * -------------------------------
 * C'est **exactement** le scellé de la clé d'accès (`otp_webauthn.js`), avec un
 * code tiré au sort à la place du secret rendu par l'authentificateur. La clé du
 * coffre ne change pas, donc **aucune graine n'est ré-encryptée** : sur un coffre
 * qui en porte cent quarante-quatre, c'était le seul vrai risque à éviter.
 *
 * Ouvrir devient : saisir le code → PBKDF2 sur ce code avec le sel rangé → HKDF
 * avec une étiquette de contexte → la clé qui ouvre le scellé → les octets de la
 * clé du coffre.
 *
 * ⚠️ **Le code vaut la phrase de passe.** Il ouvre tout. Sa place est une
 * enveloppe scellée, un coffre-fort, ou un gestionnaire de mots de passe qui
 * n'est PAS celui que ces tokens protègent.
 *
 * ⚠️ **Il n'est montré qu'une fois**, à sa création. Le serveur n'en reçoit ni le
 * texte ni le condensat : il n'existe donc rien, nulle part, pour le retrouver.
 */

import {
    deriveKeyBytes, toB64, fromB64, randomBytes, wipe,
} from "./otp_crypto";
import { base32Encode } from "./otp_totp";

/** 20 octets, soit 160 bits et 32 caractères base32. Assez pour que la force
 *  brute n'ait aucun sens, assez court pour se recopier sans se tromper. */
const OCTETS_CODE = 20;

/** Étiquette de contexte : la clé de scellement d'un code de relève ne doit
 *  jamais pouvoir être confondue avec celle d'une clé d'accès. */
const ETIQUETTE = "bf_otp.vault.key.recovery.v1";

/** Tire un code neuf, présenté en groupes de quatre. */
export function genererCode() {
    const octets = randomBytes(OCTETS_CODE);
    const brut = base32Encode(octets);
    wipe(octets);
    return brut.match(/.{1,4}/g).join("-");
}

/**
 * Remet une saisie humaine dans sa forme canonique.
 *
 * ⚠️ Les tirets, les espaces et la casse sautent : personne ne recopie une
 * enveloppe au caractère près. Le zéro et le un sont ramenés sur O et I, qui
 * eux existent dans l'alphabet base32 : c'est la confusion de transcription la
 * plus courante, et la corriger ne crée aucune ambiguïté puisque 0 et 1 ne sont
 * PAS des caractères valides ici.
 */
export function normaliserCode(saisie) {
    return (saisie || "")
        .toUpperCase()
        .replace(/[\s-]/g, "")
        .replace(/0/g, "O")
        .replace(/1/g, "I");
}

/** La clé de scellement dérivée du code. HKDF au-dessus de PBKDF2, jamais les
 *  octets bruts, pour la même raison que du côté des clés d'accès. */
async function cleDeScellement(code, saltB64, iterations) {
    const bits = await deriveKeyBytes(normaliserCode(code), saltB64, iterations);
    const base = await crypto.subtle.importKey("raw", bits, "HKDF", false, ["deriveKey"]);
    wipe(bits);
    return crypto.subtle.deriveKey(
        {
            name: "HKDF",
            hash: "SHA-256",
            salt: new Uint8Array(0),
            info: new TextEncoder().encode(ETIQUETTE),
        },
        base,
        { name: "AES-GCM", length: 256 },
        false,
        ["encrypt", "decrypt"]
    );
}

/**
 * Scelle la clé du coffre sous un code de relève.
 *
 * ⚠️ `vaultKeyBytes` n'existe que le temps de l'enrôlement, et l'appelant les
 * efface derrière lui. Ils viennent de la phrase de passe redemandée à ce
 * moment-là, jamais de la clé déjà en mémoire : ajouter une porte se confirme
 * par ce qu'on SAIT, pas par le fait qu'un écran soit resté ouvert.
 */
export async function scellerPourCode(code, vaultKeyBytes, iterations) {
    const salt = toB64(randomBytes(16));
    const cle = await cleDeScellement(code, salt, iterations);
    const iv = randomBytes(12);
    const scelle = await crypto.subtle.encrypt(
        { name: "AES-GCM", iv }, cle, vaultKeyBytes
    );
    return {
        salt,
        iterations,
        wrapped_secret: toB64(scelle),
        wrapped_iv: toB64(iv),
    };
}

/**
 * Essaie un code contre chaque relève enregistrée.
 *
 * On les essaie toutes plutôt que de demander laquelle : la personne qui sort
 * une enveloppe d'un coffre-fort ne sait pas laquelle des cinq elle tient, et
 * lui poser la question serait lui demander ce que le code répond déjà.
 *
 * Rend `null` si aucune ne s'ouvre. AES-GCM authentifie, donc un mauvais code
 * lève au lieu de rendre des octets au hasard : c'est ce qui permet de dire
 * « ce n'est pas le bon code » sans rien savoir du bon.
 */
export async function ouvrirAvecCode(code, recoveries) {
    for (const r of recoveries || []) {
        try {
            const cle = await cleDeScellement(code, r.salt, r.iterations);
            const clair = await crypto.subtle.decrypt(
                { name: "AES-GCM", iv: fromB64(r.wrapped_iv) },
                cle,
                fromB64(r.wrapped_secret)
            );
            return { row_id: r.id, keyBytes: new Uint8Array(clair) };
        } catch {
            continue;
        }
    }
    return null;
}

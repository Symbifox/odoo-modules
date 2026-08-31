/** @odoo-module **/

/**
 * Le chiffrement du coffre, entièrement dans le navigateur.
 *
 * Rien ici n'a d'équivalent côté serveur, et c'est le but : la clé est dérivée
 * d'une phrase de passe qui ne quitte jamais cette page. Odoo ne reçoit que du
 * chiffré et ne peut pas le lire.
 *
 * ⚠️ `crypto.subtle` n'existe QUE dans un contexte sécurisé (https, ou
 * localhost). Sur une instance servie en http, tout ce fichier est inerte et
 * l'application doit le dire clairement plutôt que d'échouer champ par champ.
 */

const enc = new TextEncoder();
const dec = new TextDecoder();

/** Le témoin : un texte connu, chiffré, pour reconnaître la bonne phrase. */
export const VERIFIER_PLAINTEXT = "bf_otp.vault.verifier.v1";

export function cryptoAvailable() {
    return typeof crypto !== "undefined" && !!crypto.subtle;
}

export function toB64(bytes) {
    let s = "";
    const arr = new Uint8Array(bytes);
    for (let i = 0; i < arr.length; i++) {
        s += String.fromCharCode(arr[i]);
    }
    return btoa(s);
}

export function fromB64(b64) {
    const s = atob(b64);
    const out = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) {
        out[i] = s.charCodeAt(i);
    }
    return out;
}

export function randomBytes(n) {
    const b = new Uint8Array(n);
    crypto.getRandomValues(b);
    return b;
}

/**
 * Dérive la clé AES-GCM 256 à partir de la phrase.
 *
 * PBKDF2-SHA256 parce que WebCrypto le fournit nativement : pas de dépendance
 * externe, donc rien à charger depuis un CDN et rien à tenir à jour. Argon2id
 * serait meilleur contre le matériel dédié, mais il faudrait l'embarquer.
 * Le nombre d'itérations est enregistré PAR COFFRE, ce qui permet de le monter
 * plus tard sans rendre illisible ce qui existe déjà.
 */
export async function deriveKeyBytes(passphrase, saltB64, iterations) {
    const base = await crypto.subtle.importKey(
        "raw", enc.encode(passphrase), "PBKDF2", false, ["deriveBits"]
    );
    const bits = await crypto.subtle.deriveBits(
        {
            name: "PBKDF2",
            salt: fromB64(saltB64),
            iterations: iterations,
            hash: "SHA-256",
        },
        base,
        256
    );
    return new Uint8Array(bits);
}

/**
 * Fabrique la clé utilisable à partir de ses octets.
 *
 * ⚠️ `extractable = false` : la clé qui sert au chiffrement ne doit jamais
 * pouvoir être relue par un script. Les octets, eux, n'existent que le temps
 * d'un enrôlement de clé d'accès, et l'appelant les efface derrière lui.
 */
export async function keyFromBytes(bytes) {
    return crypto.subtle.importKey(
        "raw", bytes, { name: "AES-GCM", length: 256 }, false,
        ["encrypt", "decrypt"]
    );
}

export async function deriveKey(passphrase, saltB64, iterations) {
    const bytes = await deriveKeyBytes(passphrase, saltB64, iterations);
    const key = await keyFromBytes(bytes);
    bytes.fill(0);
    return key;
}

/** Efface des octets sensibles sur place. Pas une garantie — le ramasse-miettes
 *  de JavaScript peut en avoir gardé une copie — mais ça réduit la fenêtre. */
export function wipe(bytes) {
    if (bytes && bytes.fill) {
        bytes.fill(0);
    }
}

/** Chiffre un texte. Un vecteur NEUF à chaque appel — jamais réutilisé. */
export async function encryptText(key, plaintext) {
    const iv = randomBytes(12);
    const cipher = await crypto.subtle.encrypt(
        { name: "AES-GCM", iv }, key, enc.encode(plaintext)
    );
    return { cipher: toB64(cipher), iv: toB64(iv) };
}

/** Déchiffre, ou lève. AES-GCM authentifie : une mauvaise clé lève, elle ne
 *  rend pas des octets au hasard. C'est ce qui permet au témoin de servir. */
export async function decryptText(key, cipherB64, ivB64) {
    const clear = await crypto.subtle.decrypt(
        { name: "AES-GCM", iv: fromB64(ivB64) }, key, fromB64(cipherB64)
    );
    return dec.decode(clear);
}

/** Fabrique le matériel d'un coffre neuf : sel, témoin, et la clé prête. */
export async function buildVaultMaterial(passphrase, iterations) {
    const salt = toB64(randomBytes(16));
    const key = await deriveKey(passphrase, salt, iterations);
    const { cipher, iv } = await encryptText(key, VERIFIER_PLAINTEXT);
    return { salt, iterations, verifier: cipher, verifier_iv: iv, key };
}

/**
 * Ouvre un coffre existant : rend la clé, ou null si la phrase est mauvaise.
 *
 * On distingue « mauvaise phrase » d'une vraie panne : le témoin qui ne
 * déchiffre pas est une réponse, pas une erreur, et l'écran doit le dire
 * autrement qu'un plantage.
 */
export async function unlockVault(passphrase, vault) {
    const key = await deriveKey(passphrase, vault.salt, vault.iterations);
    try {
        const temoin = await decryptText(key, vault.verifier, vault.verifier_iv);
        return temoin === VERIFIER_PLAINTEXT ? key : null;
    } catch {
        return null;
    }
}

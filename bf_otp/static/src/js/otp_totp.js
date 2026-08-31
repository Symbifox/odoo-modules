/** @odoo-module **/

/**
 * RFC 4226 (HOTP) et RFC 6238 (TOTP), calculés dans le navigateur.
 *
 * Le code se produit ici parce que la graine ne vit qu'ici. WebCrypto fournit
 * HMAC-SHA1, SHA-256 et SHA-512, qui couvrent les trois algorithmes que les
 * services émettent en pratique — donc aucune bibliothèque à charger.
 */

const B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

/**
 * Décode une graine base32.
 *
 * ⚠️ Tolérant à dessein : les gens recopient les graines avec des espaces, des
 * tirets, en minuscules, avec ou sans remplissage. Refuser sur la forme ferait
 * échouer une saisie parfaitement valide.
 */
export function base32Decode(input) {
    const clean = (input || "").toUpperCase().replace(/[\s-]/g, "").replace(/=+$/, "");
    if (!clean.length || /[^A-Z2-7]/.test(clean)) {
        throw new Error("Graine invalide : l'alphabet base32 est A-Z et 2-7.");
    }
    let bits = 0;
    let value = 0;
    const out = [];
    for (const ch of clean) {
        value = (value << 5) | B32.indexOf(ch);
        bits += 5;
        if (bits >= 8) {
            bits -= 8;
            out.push((value >>> bits) & 0xff);
        }
    }
    return new Uint8Array(out);
}

const HASHES = { SHA1: "SHA-1", SHA256: "SHA-256", SHA512: "SHA-512" };

/** HOTP, RFC 4226. Le compteur est un entier 64 bits en gros-boutiste. */
export async function hotp(keyBytes, counter, digits = 6, algorithm = "SHA1") {
    const hash = HASHES[(algorithm || "SHA1").toUpperCase()];
    if (!hash) {
        throw new Error(`Algorithme inconnu : ${algorithm}`);
    }
    const msg = new Uint8Array(8);
    // BigInt plutôt qu'un décalage : au-delà de 2^31 les opérateurs binaires de
    // JavaScript travaillent sur 32 bits signés et le compteur repartirait à
    // zéro sans bruit.
    let c = BigInt(counter);
    for (let i = 7; i >= 0; i--) {
        msg[i] = Number(c & 0xffn);
        c >>= 8n;
    }
    const key = await crypto.subtle.importKey(
        "raw", keyBytes, { name: "HMAC", hash }, false, ["sign"]
    );
    const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, msg));
    const offset = mac[mac.length - 1] & 0x0f;
    const bin =
        ((mac[offset] & 0x7f) << 24) |
        ((mac[offset + 1] & 0xff) << 16) |
        ((mac[offset + 2] & 0xff) << 8) |
        (mac[offset + 3] & 0xff);
    return String(bin % 10 ** digits).padStart(digits, "0");
}

/** TOTP, RFC 6238 : un HOTP dont le compteur est le temps découpé en tranches. */
export async function totp(keyBytes, epochSeconds, period = 30, digits = 6, algorithm = "SHA1") {
    const counter = Math.floor(epochSeconds / period);
    return hotp(keyBytes, counter, digits, algorithm);
}

/** Secondes restantes avant que le code change. Sert à l'anneau de décompte. */
export function secondsLeft(epochSeconds, period = 30) {
    return period - (Math.floor(epochSeconds) % period);
}

/**
 * Lit une adresse `otpauth://`, la forme que produisent tous les QR.
 *
 * 🔴 **Ne PAS utiliser `new URL()` pour découper cette adresse.** `otpauth`
 * n'est pas un protocole « spécial » au sens de la norme WHATWG, et les
 * moteurs ne s'accordent pas :
 *
 *   Node   → host="totp", pathname="/Blue%20Fox:compte"
 *   Chrome → host="",     pathname="//totp/Blue%20Fox:compte"
 *
 * Mesuré le 2026-08-30. Un analyseur écrit contre Node passe ses tests et
 * échoue dans le navigateur, en silence, sur `host` vide. On découpe donc à la
 * main ; `URLSearchParams`, lui, se comporte pareil des deux côtés.
 *
 * Rend les métadonnées ET la graine : c'est à l'appelant de chiffrer la graine
 * sans délai et de ne jamais la garder.
 */
export function parseOtpauth(uri) {
    const m = /^otpauth:\/\/(totp|hotp)\/([^?]*)(?:\?([\s\S]*))?$/i.exec(
        (uri || "").trim()
    );
    if (!m) {
        throw new Error("Ce n'est pas une adresse otpauth://totp ou otpauth://hotp.");
    }
    const otpType = m[1].toLowerCase();
    const params = new URLSearchParams(m[3] || "");

    // Le libellé vaut « Émetteur:compte » ou « compte ». On décode APRÈS avoir
    // coupé sur le deux-points : un compte peut contenir un « %3A » encodé, qui
    // ne doit pas être pris pour le séparateur.
    const label = m[2];
    const sep = label.indexOf(":");
    const dec = (v) => {
        try {
            return decodeURIComponent(v);
        } catch {
            return v;
        }
    };
    let issuer = params.get("issuer") || "";
    let name = label;
    if (sep >= 0) {
        if (!issuer) {
            issuer = dec(label.slice(0, sep));
        }
        name = label.slice(sep + 1);
    }
    name = dec(name);

    const secret = params.get("secret");
    if (!secret) {
        throw new Error("Cette adresse ne porte pas de graine.");
    }
    const digits = parseInt(params.get("digits") || "6", 10);
    const period = parseInt(params.get("period") || "30", 10);
    return {
        name: name.trim() || "Sans nom",
        issuer: issuer.trim(),
        otp_type: otpType,
        algorithm: (params.get("algorithm") || "SHA1").toUpperCase(),
        digits: Number.isFinite(digits) ? digits : 6,
        period: Number.isFinite(period) ? period : 30,
        counter: parseInt(params.get("counter") || "0", 10) || 0,
        secret,
    };
}

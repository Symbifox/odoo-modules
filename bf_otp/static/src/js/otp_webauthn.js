/** @odoo-module **/

/**
 * Déverrouillage par clé d'accès, avec l'extension PRF de WebAuthn.
 *
 * Ce que PRF donne : pour un couple (clé d'accès, sel), l'authentificateur rend
 * 32 octets **stables** et impossibles à obtenir sans lui. On s'en sert comme
 * clé pour sceller une copie de la clé du coffre.
 *
 * ⚠️ Ce que PRF n'est PAS : une authentification. Le serveur ne vérifie aucune
 * signature ici et n'accorde aucun droit sur la foi de cette clé — la session
 * Odoo fait déjà ce travail. Une clé d'accès qui ne serait pas la bonne ne
 * déchiffrera simplement rien, ce qui est le seul verrou dont on a besoin.
 *
 * ⚠️ **Lié à l'origine.** Une clé d'accès enregistrée sur un domaine ne
 * fonctionne pas sur un autre. Le coffre ouvert par clé d'accès en production
 * ne le sera pas depuis la pré-production, et pas depuis une extension. La
 * phrase de passe reste donc le chemin de secours, toujours.
 */

import { fromB64, toB64, randomBytes } from "./otp_crypto";

/** base64url sans remplissage, la forme qu'attend et rend WebAuthn. */
export function toB64Url(bytes) {
    return toB64(bytes).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function fromB64Url(s) {
    const p = s.replace(/-/g, "+").replace(/_/g, "/");
    return fromB64(p + "=".repeat((4 - (p.length % 4)) % 4));
}

export function webauthnAvailable() {
    return (
        typeof PublicKeyCredential !== "undefined" &&
        typeof navigator !== "undefined" &&
        !!navigator.credentials
    );
}

/**
 * Dérive la clé de scellement à partir des 32 octets rendus par PRF.
 *
 * HKDF et non « les octets tels quels » : le secret PRF sert peut-être ailleurs
 * un jour, et une étiquette de contexte garantit qu'on n'utilisera jamais la
 * même clé pour deux usages différents.
 */
async function keyFromPrf(prfBytes) {
    const base = await crypto.subtle.importKey(
        "raw", prfBytes, "HKDF", false, ["deriveKey"]
    );
    return crypto.subtle.deriveKey(
        {
            name: "HKDF",
            hash: "SHA-256",
            salt: new Uint8Array(0),
            info: new TextEncoder().encode("bf_otp.vault.key.wrap.v1"),
        },
        base,
        { name: "AES-GCM", length: 256 },
        false,
        ["encrypt", "decrypt"]
    );
}

function prfResult(cred) {
    const ext = cred.getClientExtensionResults?.();
    const first = ext?.prf?.results?.first;
    return first ? new Uint8Array(first) : null;
}

/**
 * Enrôle une clé d'accès et scelle la clé du coffre pour elle.
 *
 * `vaultKeyBytes` sont les octets de la clé qui chiffre déjà les graines : on
 * ne la change PAS, on en range une copie. Aucune graine n'est ré-encryptée.
 *
 * ⚠️ Deux appels sont nécessaires : `create()` déclare qu'on veut PRF, mais
 * beaucoup d'authentificateurs ne rendent le résultat qu'à un `get()`
 * ultérieur. Demander l'évaluation dès la création et s'en contenter marcherait
 * sur certains navigateurs et échouerait en silence sur d'autres.
 */
export async function enrollPasskey(userName, displayName, vaultKeyBytes) {
    if (!webauthnAvailable()) {
        throw new Error("Ce navigateur ne gère pas les clés d'accès.");
    }
    const salt = randomBytes(32);
    const challenge = randomBytes(32);
    const userId = randomBytes(16);

    const cred = await navigator.credentials.create({
        publicKey: {
            challenge,
            rp: { name: "Blue Fox — Jetons OTP", id: location.hostname },
            user: { id: userId, name: userName, displayName: displayName || userName },
            pubKeyCredParams: [
                { type: "public-key", alg: -7 },    // ES256
                { type: "public-key", alg: -257 },  // RS256
            ],
            authenticatorSelection: {
                residentKey: "preferred",
                userVerification: "required",
            },
            timeout: 60000,
            attestation: "none",
            extensions: { prf: {} },
        },
    });
    if (!cred) {
        throw new Error("Enrôlement annulé.");
    }
    const credentialId = toB64Url(new Uint8Array(cred.rawId));

    // Second passage : obtenir le secret PRF pour de vrai.
    const secret = await evaluatePrf(credentialId, salt);
    if (!secret) {
        throw new Error(
            "Cette clé d'accès ne gère pas l'extension PRF. " +
            "Il faut un authentificateur qui la prend en charge."
        );
    }
    const key = await keyFromPrf(secret);
    const iv = randomBytes(12);
    const wrapped = await crypto.subtle.encrypt(
        { name: "AES-GCM", iv }, key, vaultKeyBytes
    );
    return {
        credential_id: credentialId,
        prf_salt: toB64(salt),
        wrapped_secret: toB64(wrapped),
        wrapped_iv: toB64(iv),
    };
}

/** Demande les 32 octets PRF pour une clé d'accès donnée, ou null. */
export async function evaluatePrf(credentialIdB64Url, salt) {
    const assertion = await navigator.credentials.get({
        publicKey: {
            challenge: randomBytes(32),
            rpId: location.hostname,
            allowCredentials: [
                { type: "public-key", id: fromB64Url(credentialIdB64Url) },
            ],
            userVerification: "required",
            timeout: 60000,
            extensions: { prf: { eval: { first: salt } } },
        },
    });
    return assertion ? prfResult(assertion) : null;
}

/**
 * Ouvre le coffre avec l'une des clés d'accès enregistrées.
 *
 * Rend les octets de la clé du coffre, ou null si l'authentificateur n'a pas
 * répondu. On essaie chaque clé enregistrée : la personne peut en avoir
 * plusieurs et n'en avoir qu'une sous la main.
 */
export async function unlockWithPasskey(credentials) {
    for (const c of credentials) {
        let secret = null;
        try {
            secret = await evaluatePrf(c.credential_id, fromB64(c.prf_salt));
        } catch {
            // Clé absente de cet appareil, ou refus : on passe à la suivante
            // sans faire échouer l'ensemble.
            continue;
        }
        if (!secret) {
            continue;
        }
        try {
            const key = await keyFromPrf(secret);
            const clair = await crypto.subtle.decrypt(
                { name: "AES-GCM", iv: fromB64(c.wrapped_iv) },
                key,
                fromB64(c.wrapped_secret)
            );
            return { row_id: c.id, keyBytes: new Uint8Array(clair) };
        } catch {
            // Le scellé ne s'ouvre pas avec ce secret : la clé d'accès est
            // bonne mais le coffre a changé de clé. On le dit à l'appelant en
            // continuant, pas en levant.
            continue;
        }
    }
    return null;
}

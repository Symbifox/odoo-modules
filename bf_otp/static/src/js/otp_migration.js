/** @odoo-module **/

/**
 * Lecture d'un export Google Authenticator (`otpauth-migration://`).
 *
 * Pourquoi ce format
 * ------------------
 * C'est celui que les gens ont réellement en main. Quelqu'un qui arrive chez
 * nous vient de Google Authenticator neuf fois sur dix, et ce qu'il obtient en
 * exportant n'est ni un JSON ni une liste d'adresses : c'est un ou plusieurs
 * codes QR dont le contenu est du protobuf encodé en base64. Sans lecteur, la
 * seule migration possible est de ressaisir les comptes un par un.
 *
 * ⚠️ **Le contenu porte les graines EN CLAIR.** Google ne chiffre rien : le QR
 * EST le secret. Il est donc lu, converti et rechiffré dans cette page, et rien
 * n'en sort. Le texte collé ne doit pas plus traîner qu'une graine.
 *
 * Pourquoi un analyseur protobuf écrit à la main
 * ----------------------------------------------
 * Trois champs de trois types sur deux messages. Charger une bibliothèque pour
 * ça voudrait dire une dépendance externe dans un module dont l'argument est
 * qu'il n'en a aucune côté cryptographie. Ce qui suit lit les varints et les
 * champs à longueur préfixée, et saute proprement ce qu'il ne connaît pas.
 */

import { base32Encode } from "./otp_totp";

/** Ce que Google écrit dans son champ `algorithm`. 4 est MD5, qu'on refuse. */
const ALGOS = { 0: "SHA1", 1: "SHA1", 2: "SHA256", 3: "SHA512", 4: "MD5" };
/** `digits` est une énumération, pas un nombre : 1 veut dire six. */
const CHIFFRES = { 0: 6, 1: 6, 2: 8 };
const TYPES = { 0: "totp", 1: "hotp", 2: "totp" };

export function estUneMigration(texte) {
    return /^otpauth-migration:\/\//i.test((texte || "").trim());
}

/** Lit un varint. Rend la valeur en BigInt et l'index qui suit. */
function varint(buf, i) {
    let valeur = 0n;
    let decalage = 0n;
    while (i < buf.length) {
        const octet = buf[i++];
        valeur |= BigInt(octet & 0x7f) << decalage;
        if ((octet & 0x80) === 0) {
            return [valeur, i];
        }
        decalage += 7n;
        if (decalage > 70n) {
            throw new Error("Varint trop long : ce n'est pas un export valide.");
        }
    }
    throw new Error("Export tronqué.");
}

/**
 * Parcourt les champs d'un message protobuf.
 *
 * ⚠️ Saute les types qu'on n'attend pas plutôt que de lever : un format qui
 * gagne un champ dans une version future ne doit pas rendre l'import
 * impossible. Un type de fil inconnu, lui, lève : là, on ne sait plus où on est
 * dans le tampon, et continuer produirait n'importe quoi en silence.
 */
function* champs(buf) {
    let i = 0;
    while (i < buf.length) {
        let cle;
        [cle, i] = varint(buf, i);
        const numero = Number(cle >> 3n);
        const type = Number(cle & 7n);
        if (type === 0) {
            let v;
            [v, i] = varint(buf, i);
            yield { numero, type, valeur: v };
        } else if (type === 2) {
            let n;
            [n, i] = varint(buf, i);
            const longueur = Number(n);
            if (i + longueur > buf.length) {
                throw new Error("Export tronqué.");
            }
            yield { numero, type, octets: buf.subarray(i, i + longueur) };
            i += longueur;
        } else if (type === 5) {
            i += 4;
        } else if (type === 1) {
            i += 8;
        } else {
            throw new Error(`Champ protobuf de type ${type} inattendu.`);
        }
    }
}

const utf8 = new TextDecoder();

function lireParametres(buf) {
    const p = {
        secret: null, name: "", issuer: "",
        algorithm: "SHA1", digits: 6, otp_type: "totp", counter: 0,
    };
    for (const c of champs(buf)) {
        switch (c.numero) {
            case 1: if (c.type === 2) { p.secret = c.octets; } break;
            case 2: if (c.type === 2) { p.name = utf8.decode(c.octets); } break;
            case 3: if (c.type === 2) { p.issuer = utf8.decode(c.octets); } break;
            case 4: if (c.type === 0) { p.algorithm = ALGOS[Number(c.valeur)] || "SHA1"; } break;
            case 5: if (c.type === 0) { p.digits = CHIFFRES[Number(c.valeur)] || 6; } break;
            case 6: if (c.type === 0) { p.otp_type = TYPES[Number(c.valeur)] || "totp"; } break;
            case 7: if (c.type === 0) { p.counter = Number(c.valeur); } break;
            default: break;
        }
    }
    return p;
}

/**
 * Lit une adresse `otpauth-migration://` et rend les comptes qu'elle porte.
 *
 * Rend aussi `lot`, quand l'export est découpé : Google produit plusieurs codes
 * QR au-delà d'une dizaine de comptes, et quelqu'un qui n'en scanne qu'un croit
 * avoir tout importé. Le dire est la moitié du travail.
 */
export function lireMigration(uri) {
    const texte = (uri || "").trim();
    if (!estUneMigration(texte)) {
        throw new Error("Ce n'est pas une adresse otpauth-migration://.");
    }
    let brut;
    try {
        const params = new URLSearchParams(texte.slice(texte.indexOf("?") + 1));
        let data = params.get("data");
        if (!data) {
            throw new Error("Cette adresse ne porte aucune donnée.");
        }
        // 🔴 Le piège du « + ». La donnée est du base64 STANDARD, qui contient
        // des « + ». `URLSearchParams` applique les règles des FORMULAIRES et
        // transforme un « + » littéral en ESPACE. Une adresse correctement
        // encodée porte « %2B » et s'en sort ; une adresse recopiée à la main,
        // ou passée par un outil qui a déjà décodé une fois, arrive avec des
        // « + » nus et ressort trouée. Le base64 n'ayant jamais d'espace, tout
        // espace ici ÉTAIT un « + » : le remettre est sûr, et c'est la seule
        // façon de ne pas produire une graine fausse en silence.
        // (Même famille que le `URLDecoder` de Java côté Android.)
        data = data.replace(/ /g, "+");
        // Certains lecteurs de QR rendent du base64url. Il n'apparaît pas dans
        // l'export de Google, mais l'accepter ne coûte rien et évite un refus
        // incompréhensible.
        data = data.replace(/-/g, "+").replace(/_/g, "/");
        brut = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
    } catch (e) {
        throw new Error(
            e.message.startsWith("Cette adresse") ? e.message
                : "Le contenu de cette adresse n'est pas lisible."
        );
    }

    const comptes = [];
    const refuses = [];
    let lot = null;
    let taille = 1;
    let index = 0;
    for (const c of champs(brut)) {
        if (c.numero === 1 && c.type === 2) {
            const p = lireParametres(c.octets);
            if (!p.secret || !p.secret.length) {
                refuses.push(p.name || "?");
                continue;
            }
            if (p.algorithm === "MD5") {
                // WebCrypto ne fait pas de MD5, et aucun service sérieux n'en
                // émet. Refuser en le nommant vaut mieux qu'importer un token
                // qui produira des codes faux jusqu'à ce qu'on cherche pourquoi.
                refuses.push(`${p.name || "?"} (MD5)`);
                continue;
            }
            let nom = p.name || "";
            let emetteur = p.issuer || "";
            // Google écrit souvent « Émetteur:compte » dans le seul champ de nom.
            if (!emetteur && nom.includes(":")) {
                const coupe = nom.indexOf(":");
                emetteur = nom.slice(0, coupe).trim();
                nom = nom.slice(coupe + 1).trim();
            }
            comptes.push({
                name: nom || "Sans nom",
                issuer: emetteur,
                otp_type: p.otp_type,
                algorithm: p.algorithm,
                digits: p.digits,
                // Google n'écrit pas la période : son export est toujours en 30 s,
                // qui est aussi le défaut de la RFC 6238.
                period: 30,
                counter: p.counter,
                secret: base32Encode(p.secret),
            });
        } else if (c.numero === 3 && c.type === 0) {
            taille = Number(c.valeur) || 1;
        } else if (c.numero === 4 && c.type === 0) {
            index = Number(c.valeur) || 0;
        }
    }
    if (taille > 1) {
        lot = { index: index + 1, taille };
    }
    return { comptes, refuses, lot };
}

/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useState, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { browser } from "@web/core/browser/browser";
// 🔴 Importé EN HAUT, jamais par `await import("@web/…")`. Le système de
// modules d'Odoo n'est pas de l'ESM natif : un `import()` dynamique d'un
// spécificateur `@web/…` part au navigateur, qui ne sait pas le résoudre et
// lève « Failed to resolve module specifier ». La fonction ne s'ouvre alors
// jamais, et rien ne le dit à l'écran. Trouvé au navigateur le 2026-09-02 :
// la suppression d'un token n'a jamais fonctionné depuis qu'elle existe.
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import {
    cryptoAvailable, buildVaultMaterial, unlockVault, encryptText, decryptText,
    deriveKeyBytes, keyFromBytes, wipe,
} from "./otp_crypto";
import {
    webauthnAvailable, enrollPasskey, unlockWithPasskey, PrfNonRendu,
} from "./otp_webauthn";
import { base32Decode, totp, hotp, secondsLeft, parseOtpauth } from "./otp_totp";
import { iconeDe, contraste } from "./otp_icons";
import {
    construireExport, lireExport, estUnExportSymbifox, PhraseIncorrecte,
} from "./otp_export";
import { estUneMigration, lireMigration } from "./otp_migration";
import {
    genererCode, scellerPourCode, ouvrirAvecCode,
} from "./otp_recovery";

/** Minutes d'inactivité après lesquelles le coffre se referme tout seul.
 *  Un coffre ouvert sur un écran laissé sans surveillance est un coffre ouvert
 *  pour qui passe : la clé vit en mémoire, elle doit en sortir. */
const VERROU_AUTO_MS = 5 * 60 * 1000;
const ITERATIONS = 600000;

export class BfOtpApp extends Component {
    static template = "bf_otp.App";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.passphraseRef = useRef("passphrase");
        this.confirmRef = useRef("confirm");

        this.state = useState({
            ready: false,
            secure: cryptoAvailable(),
            vault: null,          // les métadonnées du coffre, jamais la clé
            unlocked: false,
            busy: false,
            error: "",
            tokens: [],           // { ...métadonnées, code, left }
            search: "",
            revealed: {},         // id -> true, pour les jetons sensibles
            showForm: false,
            showImport: false,
            compact: false,
            sortMode: "recent",   // "recent" | "name"
            lookup: [],           // résultats de recherche client/projet
            lookupField: "",      // le champ qu'on est en train de remplir
            passkeyOk: false,     // le navigateur gère-t-il les clés d'accès
            showKeys: false,      // panneau de gestion des clés d'accès
            prfRefus: false,      // le fournisseur a créé la clé sans rendre PRF
            enrolPass: "",
            enrolName: "",
            form: this._formVierge(),
            importText: "",
            importPassword: "",
            importNote: "",       // ce que l'import a compris du fichier
            showExport: false,
            exportPass: "",
            exportConfirm: "",
            showRecovery: false,  // panneau des codes de relève
            recovName: "",
            recovPass: "",
            recovCode: "",        // montré UNE fois, jamais relu ailleurs
            useRecovery: false,   // l'écran verrouillé demande un code
            recovInput: "",
            showTrash: false,
            trash: [],
        });

        // La clé ne va PAS dans le state : rien qui la porte ne doit finir dans
        // un rendu, un journal, ou l'inspecteur de composants.
        this._key = null;
        this._tick = null;
        this._idleTimer = null;
        this._onActivity = () => this._armerVerrouAuto();
        this._onKey = (ev) => this._clavier(ev);

        onWillStart(async () => {
            this.state.vault = await this.orm.call("bf.otp.vault", "get_my_vault", []);
            this.state.passkeyOk = webauthnAvailable();
            this.state.ready = true;
        });

        onWillUnmount(() => this._teardown());
    }

    /**
     * Couleur stable tirée du nom de l'émetteur.
     *
     * ⚠️ Volontairement calculée, et non tirée d'une favicon : aller chercher
     * l'icône d'un service révélerait à ce service — et à qui regarde le
     * réseau — la liste des comptes qu'on protège. Une pastille locale ne dit
     * rien à personne.
     */
    /**
     * L'icône embarquée de l'émetteur, ou `null`.
     *
     * ⚠️ Aucune requête : les tracés vivent dans le paquet. Le refus d'aller
     * chercher une favicon tient toujours, et pour la même raison.
     */
    icone(t) {
        return iconeDe(t.issuer);
    }

    chipStyle(t) {
        // Quand on connaît la marque, sa couleur dit le service plus vite que
        // n'importe quelle teinte calculée. Le glyphe passe en noir ou en blanc
        // selon la luminance, sans quoi une marque claire l'avalerait.
        const ic = this.icone(t);
        if (ic) {
            return `background: ${ic.hex}; color: ${contraste(ic.hex)};`;
        }
        const src = (t.issuer || t.name || "?").toLowerCase();
        let h = 0;
        for (let i = 0; i < src.length; i++) {
            h = (h * 31 + src.charCodeAt(i)) % 360;
        }
        return `background: hsl(${h} 55% 42%);`;
    }

    chipText(t) {
        const src = (t.issuer || t.name || "?").trim();
        const mots = src.split(/[\s.\-_@]+/).filter(Boolean);
        return ((mots[0]?.[0] || "?") + (mots[1]?.[0] || "")).toUpperCase();
    }

    _formVierge() {
        return {
            id: null, name: "", issuer: "", secret: "", otp_type: "totp",
            algorithm: "SHA1", digits: 6, period: 30, counter: 0,
            group_name: "", sensitive: false, uri: "",
            partner_id: false, partner_label: "",
            project_id: false, project_label: "",
        };
    }

    // -- cycle de vie du verrou ---------------------------------------------

    _teardown() {
        this._key = null;
        if (this._tick) {
            browser.clearInterval(this._tick);
            this._tick = null;
        }
        if (this._idleTimer) {
            browser.clearTimeout(this._idleTimer);
            this._idleTimer = null;
        }
        for (const evt of ["mousemove", "keydown", "click"]) {
            document.removeEventListener(evt, this._onActivity, true);
        }
        document.removeEventListener("keydown", this._onKey);
    }

    _armerVerrouAuto() {
        if (this._idleTimer) {
            browser.clearTimeout(this._idleTimer);
        }
        this._idleTimer = browser.setTimeout(() => this.lock(true), VERROU_AUTO_MS);
    }

    /**
     * Le clavier, sur un coffre ouvert.
     *
     * ⚠️ On ne détourne JAMAIS une frappe qui vise déjà un champ : sans ce
     * garde, taper « s » dans le formulaire d'ajout viderait la saisie pour
     * remplir la recherche. Même chose pour les raccourcis du système
     * (Ctrl, Cmd, Alt), qui appartiennent au navigateur.
     */
    _clavier(ev) {
        const cible = ev.target;
        const dansUnChamp =
            cible && (cible.tagName === "INPUT" || cible.tagName === "TEXTAREA" ||
                      cible.tagName === "SELECT" || cible.isContentEditable);

        if (ev.key === "Escape") {
            if (this._unPanneauEstOuvert()) {
                this._fermerPanneaux();
            } else if (this.state.search) {
                this.state.search = "";
            } else {
                // Échap sur un coffre au repos referme : c'est le geste qu'on
                // fait en se levant, et il doit avoir un effet utile.
                this.lock();
            }
            ev.preventDefault();
            return;
        }
        if (this._unPanneauEstOuvert()) {
            return;
        }
        if (ev.key === "Enter" && !dansUnChamp) {
            const premier = this.visibleTokens[0];
            if (premier) {
                this.copyCode(premier);
                ev.preventDefault();
            }
            return;
        }
        if (dansUnChamp || ev.ctrlKey || ev.metaKey || ev.altKey) {
            return;
        }
        if (ev.key.length === 1 && /\S/.test(ev.key)) {
            const champ = document.querySelector(".o_bf_otp_search");
            if (champ) {
                champ.focus();
            }
        }
    }

    /**
     * Y a-t-il un panneau par-dessus la liste ?
     *
     * ⚠️ Une seule liste, tenue à un seul endroit. Chaque panneau ajouté
     * jusqu'ici a dû être répété dans le clavier ET dans le verrouillage, et
     * c'est exactement le genre d'énumération qu'on oublie de compléter : le
     * panneau oublié laisse alors Échap verrouiller le coffre sous un
     * formulaire à moitié rempli.
     */
    _panneaux() {
        return ["showForm", "showImport", "showKeys", "showExport",
                "showRecovery", "showTrash"];
    }

    _unPanneauEstOuvert() {
        return this._panneaux().some((nom) => this.state[nom]);
    }

    _fermerPanneaux() {
        for (const nom of this._panneaux()) {
            this.state[nom] = false;
        }
        this.state.recovCode = "";
    }

    /**
     * Le seul chemin d'ouverture, pour les deux branches.
     *
     * 🔴 La création d'un coffre neuf ne posait NI les écoutes d'activité NI
     * les raccourcis : sur cette branche, le verrouillage automatique après
     * cinq minutes ne se déclenchait donc jamais. Deux chemins qui ouvraient
     * le même coffre différemment, et un seul des deux était sûr.
     */
    _ouvrir() {
        this.state.unlocked = true;
        this._demarrerHorloge();
        for (const evt of ["mousemove", "keydown", "click"]) {
            document.addEventListener(evt, this._onActivity, true);
        }
        document.addEventListener("keydown", this._onKey);
        this._armerVerrouAuto();
    }

    lock(automatique = false) {
        this._teardown();
        this.state.unlocked = false;
        this.state.tokens = [];
        this.state.revealed = {};
        this._fermerPanneaux();
        this.state.trash = [];
        if (automatique) {
            this.notification.add(
                _t("Coffre refermé après cinq minutes sans activité."),
                { type: "info" }
            );
        }
    }

    // -- ouverture -----------------------------------------------------------

    async onUnlock(ev) {
        ev.preventDefault();
        const phrase = this.passphraseRef.el?.value || "";
        if (!phrase) {
            return;
        }
        this.state.busy = true;
        this.state.error = "";
        try {
            const key = await unlockVault(phrase, this.state.vault);
            if (!key) {
                this.state.error = _t("Phrase de passe incorrecte.");
                return;
            }
            this._key = key;
            await this._chargerJetons();
            if (this.passphraseRef.el) {
                this.passphraseRef.el.value = "";
            }
            this._ouvrir();
        } finally {
            this.state.busy = false;
        }
    }

    get credentials() {
        return (this.state.vault && this.state.vault.credentials) || [];
    }

    /**
     * Ouvre le coffre en touchant le capteur.
     *
     * ⚠️ Un échec ici n'est PAS une erreur : la clé d'accès peut être sur un
     * autre appareil, ou la personne peut avoir annulé. On le dit et on laisse
     * la phrase de passe disponible, qui reste le chemin de secours.
     */
    async onUnlockWithPasskey() {
        this.state.busy = true;
        this.state.error = "";
        let octets = null;
        try {
            const res = await unlockWithPasskey(this.credentials);
            if (!res) {
                this.state.error = _t(
                    "Aucune clé d'accès enregistrée n'a répondu sur cet appareil. " +
                    "Utilisez votre phrase de passe."
                );
                return;
            }
            octets = res.keyBytes;
            this._key = await keyFromBytes(octets);
            await this._chargerJetons();
            // Un jeton cassé partout voudrait dire que la clé est mauvaise :
            // mieux vaut le dire que d'afficher cent quarante lignes de points.
            if (this.state.tokens.length && this.state.tokens.every((t) => t.broken)) {
                this._key = null;
                this.state.tokens = [];
                this.state.error = _t(
                    "Cette clé d'accès n'ouvre plus ce coffre. Utilisez votre phrase de passe."
                );
                return;
            }
            this._ouvrir();
            this.orm.call("bf.otp.vault", "touch_credential", [res.row_id]).catch(() => {});
        } catch (e) {
            this.state.error = e.message || _t("La clé d'accès n'a pas répondu.");
        } finally {
            wipe(octets);
            this.state.busy = false;
        }
    }

    /**
     * Enrôle une clé d'accès. Redemande la phrase, exprès.
     *
     * ⚠️ On ne réutilise PAS la clé déjà en mémoire : ajouter un moyen d'ouvrir
     * un coffre est le genre de geste qu'on veut voir confirmé par ce que la
     * personne SAIT, pas seulement par le fait qu'un écran soit resté ouvert.
     * C'est aussi ce qui permet de ne manipuler les octets de la clé que le
     * temps de l'enrôlement.
     */
    async onEnrolPasskey(ev) {
        ev.preventDefault();
        this.state.error = "";
        // ⚠️ Le refus précédent doit tomber ICI, pas seulement à l'ouverture du
        // panneau : le geste qui suit un refus est justement de rebrancher un
        // autre authentificateur et de réessayer sans fermer l'écran. Sans ça,
        // l'avertissement survit à sa propre réfutation.
        this.state.prfRefus = false;
        if (!this.state.enrolPass) {
            this.state.error = _t("Entrez votre phrase de passe pour confirmer.");
            return;
        }
        this.state.busy = true;
        let octets = null;
        try {
            const v = this.state.vault;
            octets = await deriveKeyBytes(this.state.enrolPass, v.salt, v.iterations);
            const controle = await keyFromBytes(octets);
            try {
                await decryptText(controle, v.verifier, v.verifier_iv);
            } catch {
                this.state.error = _t("Phrase de passe incorrecte.");
                return;
            }
            const scelle = await enrollPasskey(
                this.env.services.user?.name || "coffre",
                _t("Coffre de tokens OTP"),
                octets
            );
            await this.orm.call("bf.otp.vault", "add_credential", [
                this.state.enrolName || _t("Cet appareil"),
                scelle.credential_id, scelle.prf_salt,
                scelle.wrapped_secret, scelle.wrapped_iv,
            ]);
            this.state.vault = await this.orm.call("bf.otp.vault", "get_my_vault", []);
            this.state.enrolPass = "";
            this.state.enrolName = "";
            this.notification.add(
                _t("Clé d'accès enregistrée. Conservez votre phrase : elle reste le seul recours si vous perdez cet appareil."),
                { type: "success", sticky: true }
            );
        } catch (e) {
            // ⚠️ Un refus PRF n'est pas un échec à réessayer : la clé d'accès
            // a été créée et ne servira jamais. On sort du registre des
            // messages d'erreur pour expliquer, parce que la personne doit
            // faire deux choses — retirer la clé morte, et en prendre une
            // autre — et qu'aucune ligne rouge ne porte ça.
            if (e instanceof PrfNonRendu) {
                this.state.prfRefus = true;
            } else {
                this.state.error = e.message || _t("Enrôlement impossible.");
            }
        } finally {
            wipe(octets);
            this.state.busy = false;
        }
    }

    /** Ouvre le panneau des clés d'accès en repartant d'une ardoise propre. */
    onOpenKeys() {
        this.state.error = "";
        this.state.prfRefus = false;
        this.state.showKeys = true;
    }

    async onRemovePasskey(c) {
        await this.orm.call("bf.otp.vault", "remove_credential", [c.id]);
        this.state.vault = await this.orm.call("bf.otp.vault", "get_my_vault", []);
        this.notification.add(_t("Clé d'accès retirée."), { type: "info" });
    }

    async onCreateVault(ev) {
        ev.preventDefault();
        const phrase = this.passphraseRef.el?.value || "";
        const confirm = this.confirmRef.el?.value || "";
        if (phrase.length < 12) {
            this.state.error = _t("Choisissez une phrase d'au moins douze caractères.");
            return;
        }
        if (phrase !== confirm) {
            this.state.error = _t("Les deux phrases ne correspondent pas.");
            return;
        }
        this.state.busy = true;
        this.state.error = "";
        try {
            const mat = await buildVaultMaterial(phrase, ITERATIONS);
            await this.orm.call("bf.otp.vault", "create_my_vault", [
                mat.salt, mat.iterations, mat.verifier, mat.verifier_iv,
            ]);
            this.state.vault = await this.orm.call("bf.otp.vault", "get_my_vault", []);
            this._key = mat.key;
            this.state.tokens = [];
            this._ouvrir();
            this.notification.add(
                _t("Coffre créé. Notez votre phrase ailleurs : personne ne peut la retrouver, et sans elle les tokens sont perdus."),
                { type: "warning", sticky: true }
            );
        } finally {
            this.state.busy = false;
        }
    }

    // -- jetons --------------------------------------------------------------

    async _chargerJetons() {
        const lignes = await this.orm.call("bf.otp.token", "load_my_tokens", []);
        const jetons = [];
        for (const l of lignes) {
            let secret = null;
            let broken = false;
            try {
                secret = await decryptText(this._key, l.secret_cipher, l.secret_iv);
            } catch {
                // Un jeton qui ne déchiffre pas ne doit pas faire tomber les
                // autres : il s'affiche cassé, ce qui est une information.
                broken = true;
            }
            jetons.push({ ...l, _secret: secret, broken, code: "", left: 0 });
        }
        this.state.tokens = jetons;
        await this._recalculer();
    }

    _demarrerHorloge() {
        if (this._tick) {
            browser.clearInterval(this._tick);
        }
        this._tick = browser.setInterval(() => this._recalculer(), 1000);
    }

    async _recalculer() {
        const now = Date.now() / 1000;
        for (const t of this.state.tokens) {
            if (t.broken || !t._secret) {
                t.code = "······";
                t.left = 0;
                continue;
            }
            try {
                const bytes = base32Decode(t._secret);
                t.code = t.otp_type === "hotp"
                    ? await hotp(bytes, t.counter, t.digits, t.algorithm)
                    : await totp(bytes, now, t.period, t.digits, t.algorithm);
                t.left = t.otp_type === "hotp" ? t.period : secondsLeft(now, t.period);
            } catch {
                t.broken = true;
                t.code = "······";
            }
        }
    }

    _texteDe(t) {
        return [
            t.issuer, t.name, t.group_name,
            t.partner_id && t.partner_id[1],
            t.project_id && t.project_id[1],
        ].filter(Boolean).join(" ").toLowerCase();
    }

    get visibleTokens() {
        const q = (this.state.search || "").trim().toLowerCase();
        const liste = this.state.tokens.filter(
            (t) => !q || this._texteDe(t).includes(q)
        );
        // Les favoris d'abord, toujours. Ensuite l'ordre demandé : par dernière
        // utilisation (le défaut, parce qu'on ne se sert vraiment que d'une
        // dizaine de jetons sur cent quarante) ou par nom.
        return liste.sort((a, b) => {
            if (!!b.favorite !== !!a.favorite) {
                return b.favorite ? 1 : -1;
            }
            if (this.state.sortMode === "recent") {
                const va = a.last_used || "";
                const vb = b.last_used || "";
                if (va !== vb) {
                    return vb.localeCompare(va);
                }
            }
            return (a.issuer || a.name).localeCompare(b.issuer || b.name);
        });
    }

    /**
     * Les émetteurs qui portent plus d'un jeton, et eux seuls.
     *
     * 🔴 Le compte se fait sur TOUT le coffre, jamais sur la vue filtrée :
     * compté sur ce qui est visible, un mot tapé dans la recherche ferait
     * fondre un groupe de six à un, et les en-têtes sauteraient à chaque
     * frappe.
     *
     * ⚠️ Et seulement au-delà de un. Regrouper un émetteur qui n'a qu'un jeton
     * fabrique un en-tête par ligne : sur ce coffre, 112 émetteurs pour 144
     * jetons, ça remplacerait une liste par une liste deux fois plus haute.
     */
    get emetteursMultiples() {
        const n = new Map();
        for (const t of this.state.tokens) {
            const e = (t.issuer || "").trim();
            if (e) {
                n.set(e, (n.get(e) || 0) + 1);
            }
        }
        return new Set([...n.entries()].filter(([, c]) => c > 1).map(([e]) => e));
    }

    /**
     * Le regroupement affiché.
     *
     * ⚠️ Les favoris sortent de leur groupe et forment le leur, en tête : un
     * favori qu'il faut aller chercher dans son groupe n'est plus un favori.
     * En dessous, on regroupe par l'étiquette libre si elle existe, sinon par
     * client — ce qui range un coffre importé sans qu'on ait rien à saisir.
     *
     * ⚠️ L'émetteur vient en DERNIER recours, après les trois champs qu'on a
     * pu remplir : il ne doit jamais défaire le rangement de quelqu'un qui,
     * lui, a saisi ses regroupements.
     */
    get groupes() {
        const favoris = [];
        const multiples = this.emetteursMultiples;
        const m = new Map();
        for (const t of this.visibleTokens) {
            if (t.favorite) {
                favoris.push(t);
                continue;
            }
            const emetteur = (t.issuer || "").trim();
            const g = t.group_name
                || (t.partner_id && t.partner_id[1])
                || (t.project_id && t.project_id[1])
                || (multiples.has(emetteur) ? emetteur : "")
                || _t("Sans regroupement");
            if (!m.has(g)) {
                m.set(g, []);
            }
            m.get(g).push(t);
        }
        const reste = [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
        return favoris.length ? [[_t("Favoris"), favoris], ...reste] : reste;
    }

    async toggleFavorite(t) {
        t.favorite = await this.orm.call("bf.otp.token", "toggle_favorite", [t.id]);
    }

    toggleSort() {
        this.state.sortMode = this.state.sortMode === "recent" ? "name" : "recent";
    }

    // -- rattachement client / projet ---------------------------------------

    /**
     * Cherche une cible et RETIENT quel champ on remplit.
     *
     * ⚠️ Le champ visé se garde ici et non dans le gabarit : le déduire du
     * contenu des deux entrées au moment du clic donnait une règle illisible
     * et fausse dès qu'on modifiait un jeton qui portait déjà un client.
     *
     * Taper à nouveau DÉTACHE la cible précédente : sans ça, on croirait avoir
     * changé de client alors que l'ancien identifiant serait resté.
     */
    async chercherCible(modele, terme) {
        const champ = modele === "res.partner" ? "partner_id" : "project_id";
        this.state.lookupField = champ;
        this.state.form[champ] = false;
        if (!terme || terme.length < 2) {
            this.state.lookup = [];
            return;
        }
        this.state.lookup = await this.orm.call(
            "bf.otp.token", "name_search_targets", [modele, terme]
        );
    }

    choisirCible(ligne) {
        const champ = this.state.lookupField;
        if (!champ) {
            return;
        }
        this.state.form[champ] = ligne[0];
        this.state.form[champ.replace("_id", "_label")] = ligne[1];
        this.state.lookup = [];
        this.state.lookupField = "";
    }

    effacerCible(champ) {
        this.state.form[champ] = false;
        this.state.form[champ.replace("_id", "_label")] = "";
        this.state.lookup = [];
        this.state.lookupField = "";
    }

    isRevealed(t) {
        return !t.sensitive || !!this.state.revealed[t.id];
    }

    toggleReveal(t) {
        this.state.revealed[t.id] = !this.state.revealed[t.id];
    }

    displayCode(t) {
        if (!this.isRevealed(t)) {
            return "••• •••";
        }
        const c = t.code || "";
        // Un code se lit par groupes de trois, comme le montrent les vraies apps.
        return c.length === 6 ? `${c.slice(0, 3)} ${c.slice(3)}` : c;
    }

    async copyCode(t) {
        if (!this.isRevealed(t)) {
            this.toggleReveal(t);
            return;
        }
        await browser.navigator.clipboard.writeText(t.code);
        this.notification.add(_t("Code copié."), { type: "success" });
        // La date d'usage se pose au serveur, mais on la met tout de suite en
        // mémoire : sans ça le tri « les plus récents » ne bougerait qu'au
        // prochain chargement, et le geste paraîtrait sans effet.
        t.last_used = new Date().toISOString().slice(0, 19).replace("T", " ");
        this.orm.call("bf.otp.token", "touch_token", [t.id]).catch(() => {});
        if (t.otp_type === "hotp") {
            t.counter += 1;
            await this.orm.call("bf.otp.token", "bump_counter", [t.id, t.counter]);
            await this._recalculer();
        }
    }

    // -- ajout et modification ----------------------------------------------

    openForm(token = null) {
        this.state.form = token
            ? {
                  ...this._formVierge(), ...token, secret: "", id: token.id,
                  partner_id: token.partner_id ? token.partner_id[0] : false,
                  partner_label: token.partner_id ? token.partner_id[1] : "",
                  project_id: token.project_id ? token.project_id[0] : false,
                  project_label: token.project_id ? token.project_id[1] : "",
              }
            : this._formVierge();
        this.state.lookup = [];
        this.state.showForm = true;
        this.state.error = "";
    }

    onUriPasted() {
        const uri = (this.state.form.uri || "").trim();
        if (!uri.startsWith("otpauth://")) {
            return;
        }
        try {
            const p = parseOtpauth(uri);
            Object.assign(this.state.form, p, { uri: "" });
            this.notification.add(_t("Adresse lue."), { type: "success" });
        } catch (e) {
            this.state.error = e.message;
        }
    }

    async saveToken(ev) {
        ev.preventDefault();
        const f = this.state.form;
        this.state.error = "";
        if (!f.name) {
            this.state.error = _t("Le compte est obligatoire.");
            return;
        }
        const values = {
            name: f.name, issuer: f.issuer, otp_type: f.otp_type,
            algorithm: f.algorithm, digits: parseInt(f.digits, 10),
            period: parseInt(f.period, 10), counter: parseInt(f.counter, 10) || 0,
            group_name: f.group_name, sensitive: !!f.sensitive,
            partner_id: f.partner_id || false,
            project_id: f.project_id || false,
        };
        if (f.secret) {
            try {
                base32Decode(f.secret);
            } catch (e) {
                this.state.error = e.message;
                return;
            }
            const { cipher, iv } = await encryptText(this._key, f.secret.replace(/[\s-]/g, "").toUpperCase());
            values.secret_cipher = cipher;
            values.secret_iv = iv;
        } else if (!f.id) {
            this.state.error = _t("La graine est obligatoire pour un token neuf.");
            return;
        }
        this.state.busy = true;
        try {
            await this.orm.call("bf.otp.token", "save_token", [values, f.id]);
            this.state.showForm = false;
            await this._chargerJetons();
        } finally {
            this.state.busy = false;
        }
    }

    async removeToken(t) {
        this.dialog.add(
            ConfirmationDialog,
            {
                title: _t("Supprimer ce token"),
                body: _t(
                    "« %s » sera supprimé de ce coffre. Si vous n'avez pas la graine ailleurs, le deuxième facteur de ce compte devient irrécupérable.",
                    `${t.issuer ? t.issuer + " · " : ""}${t.name}`
                ),
                confirmLabel: _t("Supprimer"),
                confirm: async () => {
                    await this.orm.call("bf.otp.token", "delete_token", [t.id]);
                    await this._chargerJetons();
                },
                cancel: () => {},
            }
        );
    }

    // -- import --------------------------------------------------------------

    /**
     * Lit ce qu'on lui colle, quel que soit d'où ça vient.
     *
     * Trois provenances, reconnues au contenu plutôt qu'à un menu : personne ne
     * sait nommer le format de son propre export, et se tromper de case donne
     * un refus incompréhensible.
     *
     * 1. Une adresse `otpauth-migration://` : l'export de Google Authenticator,
     *    lu par `otp_migration.js`. ⚠️ Elle porte les graines EN CLAIR.
     * 2. Un export Symbifox, chiffré par sa propre phrase.
     * 3. Un export du gestionnaire OTP de Nextcloud, chiffré ou non.
     *
     * Dans les trois cas, le déchiffrement et le rechiffrement se font DANS
     * CETTE PAGE : le serveur ne voit passer que du chiffré.
     */
    async runImport(ev) {
        ev.preventDefault();
        this.state.error = "";
        this.state.importNote = "";
        const texte = (this.state.importText || "").trim();
        if (!texte) {
            this.state.error = _t("Collez d'abord le contenu de votre export.");
            return;
        }
        this.state.busy = true;
        try {
            let brutes = [];
            let refuses = [];
            let note = "";

            if (estUneMigration(texte)) {
                const r = lireMigration(texte);
                brutes = r.comptes;
                refuses = r.refuses;
                if (r.lot) {
                    note = _t(
                        "Cet export est découpé : c'est le code %(index)s d'une série de %(taille)s. Importez aussi les autres, sinon il vous manquera des tokens.",
                        { index: r.lot.index, taille: r.lot.taille }
                    );
                }
            } else {
                let data;
                try {
                    data = JSON.parse(texte);
                } catch {
                    this.state.error = _t(
                        "Ce n'est ni du JSON valide ni une adresse otpauth-migration://."
                    );
                    return;
                }
                if (estUnExportSymbifox(data)) {
                    if (!this.state.importPassword) {
                        this.state.error = _t(
                            "Cet export Symbifox est chiffré : entrez la phrase choisie au moment de l'export."
                        );
                        return;
                    }
                    let lu;
                    try {
                        lu = await lireExport(data, this.state.importPassword);
                    } catch (e) {
                        this.state.error = e instanceof PhraseIncorrecte
                            ? _t("Ce n'est pas la phrase de cet export.")
                            : e.message;
                        return;
                    }
                    brutes = lu.entrees;
                    refuses = lu.refuses;
                } else {
                    const r = await this._lireExportNextcloud(data);
                    if (!r) {
                        return;
                    }
                    brutes = r.comptes;
                    refuses = r.refuses;
                }
            }

            const entries = [];
            for (const c of brutes) {
                const graine = (c.secret || "").replace(/[\s-]/g, "").toUpperCase();
                try {
                    base32Decode(graine);
                } catch {
                    refuses.push(c.name || "?");
                    continue;
                }
                const { cipher, iv } = await encryptText(this._key, graine);
                entries.push({
                    name: c.name || _t("Sans nom"),
                    issuer: c.issuer || "",
                    otp_type: (c.otp_type || "totp").toLowerCase() === "hotp" ? "hotp" : "totp",
                    algorithm: (c.algorithm || "SHA1").toUpperCase(),
                    digits: c.digits || 6,
                    period: c.period || 30,
                    counter: c.counter || 0,
                    group_name: c.group_name || "",
                    sensitive: !!c.sensitive,
                    favorite: !!c.favorite,
                    partner: c.partner || "",
                    project: c.project || "",
                    secret_cipher: cipher,
                    secret_iv: iv,
                });
            }
            if (!entries.length) {
                this.state.error = _t("Aucun token lisible là-dedans.");
                return;
            }
            const rattaches = await this._resoudreRattachements(entries);
            const res = await this.orm.call("bf.otp.token", "import_tokens", [entries]);
            this.state.showImport = false;
            this.state.importText = "";
            this.state.importPassword = "";
            await this._chargerJetons();

            let msg = _t("%s token(s) importé(s).", res.created);
            if (res.skipped) {
                msg += " " + _t("%s déjà présent(s), ignoré(s).", res.skipped);
            }
            if (rattaches) {
                msg += " " + _t("%s rattachement(s) rétabli(s).", rattaches);
            }
            if (refuses.length) {
                msg += " " + _t("%s illisible(s) : %s.", refuses.length, refuses.slice(0, 5).join(", "));
            }
            if (note) {
                msg += " " + note;
            }
            this.notification.add(msg, {
                type: (refuses.length || note) ? "warning" : "success",
                sticky: !!(refuses.length || note),
            });
        } catch (e) {
            this.state.error = e.message || _t("Import impossible.");
        } finally {
            this.state.busy = false;
        }
    }

    /**
     * La branche Nextcloud de l'import, sortie de `runImport` pour que les
     * trois provenances se lisent au même niveau.
     */
    async _lireExportNextcloud(data) {
        const comptes = data.accounts || data;
        if (!Array.isArray(comptes)) {
            this.state.error = _t(
                "Ce fichier ne contient pas de liste « accounts », et ce n'est pas un export Symbifox."
            );
            return null;
        }
        const chiffreALaSource = !!data.iv;
        let cleSource = null;
        if (chiffreALaSource) {
            if (!this.state.importPassword) {
                this.state.error = _t(
                    "Cet export est chiffré : il faut la phrase de passe du coffre Nextcloud d'origine."
                );
                return null;
            }
            cleSource = await this._cleNextcloud(this.state.importPassword, data.iv);
        }
        const sortie = [];
        const refuses = [];
        for (const c of comptes) {
            let graine = c.secret;
            if (chiffreALaSource) {
                graine = await this._dechiffrerNextcloud(cleSource, c.secret, data.iv);
                if (graine === null) {
                    refuses.push(c.name || "?");
                    continue;
                }
            }
            sortie.push({
                name: c.name, issuer: c.issuer, secret: graine,
                otp_type: (c.type || "totp"), algorithm: c.algorithm,
                digits: c.digits, period: c.period, counter: c.counter,
            });
        }
        return { comptes: sortie, refuses };
    }

    /**
     * Rétablit les rattachements d'un export Symbifox, par le NOM.
     *
     * ⚠️ Seulement quand le nom désigne UN SEUL client ou projet de cette
     * instance. Un import qui devine mal rattacherait des tokens au mauvais
     * client, ce qui est pire que de ne rien rattacher : la faute serait
     * invisible et se propagerait dans les rapports de fin de mandat.
     *
     * Une requête par nom DISTINCT, pas par token : cent quarante tokens ne
     * portent qu'une vingtaine de clients.
     */
    async _resoudreRattachements(entries) {
        const paires = [["partner", "partner_id", "res.partner"],
                        ["project", "project_id", "project.project"]];
        let poses = 0;
        for (const [champTexte, champId, modele] of paires) {
            const noms = [...new Set(entries.map((e) => e[champTexte]).filter(Boolean))];
            const table = new Map();
            for (const nom of noms) {
                try {
                    const trouves = await this.orm.call(
                        "bf.otp.token", "name_search_targets", [modele, nom, 5]
                    );
                    const exacts = trouves.filter((l) => l[1] === nom);
                    if (exacts.length === 1) {
                        table.set(nom, exacts[0][0]);
                    }
                } catch {
                    // Un nom qui ne se cherche pas ne doit pas faire tomber
                    // l'import : le token entre, sans rattachement.
                }
            }
            for (const e of entries) {
                const cible = table.get(e[champTexte]);
                if (cible) {
                    e[champId] = cible;
                    poses += 1;
                }
                delete e[champTexte];
            }
        }
        return poses;
    }

    // -- export --------------------------------------------------------------

    /**
     * Écrit le coffre dans un fichier chiffré, et le fait télécharger.
     *
     * ⚠️ La phrase demandée ici n'est PAS celle du coffre, et l'écran le dit :
     * le fichier part ailleurs et vit plus longtemps que la session. Il n'existe
     * volontairement aucune option « en clair ».
     */
    async onExport(ev) {
        ev.preventDefault();
        this.state.error = "";
        const phrase = this.state.exportPass || "";
        if (phrase.length < 12) {
            this.state.error = _t("Choisissez une phrase d'au moins douze caractères pour ce fichier.");
            return;
        }
        if (phrase !== this.state.exportConfirm) {
            this.state.error = _t("Les deux phrases ne correspondent pas.");
            return;
        }
        this.state.busy = true;
        try {
            const { fichier, exportes, ignores } = await construireExport(
                this.state.tokens, phrase, ITERATIONS
            );
            if (!exportes) {
                this.state.error = _t("Aucun token lisible à exporter.");
                return;
            }
            const nom = `symbifox-tokens-${new Date().toISOString().slice(0, 10)}.json`;
            const blob = new Blob([JSON.stringify(fichier, null, 2)],
                                  { type: "application/json" });
            const url = URL.createObjectURL(blob);
            const lien = document.createElement("a");
            lien.href = url;
            lien.download = nom;
            lien.click();
            URL.revokeObjectURL(url);

            this.state.showExport = false;
            this.state.exportPass = "";
            this.state.exportConfirm = "";
            let msg = _t("%(nombre)s token(s) exporté(s) dans %(fichier)s.",
                         { nombre: exportes, fichier: nom });
            if (ignores) {
                msg += " " + _t("%s illisible(s) dans ce coffre, non exporté(s).", ignores);
            }
            msg += " " + _t("Sans la phrase de ce fichier, personne ne pourra le relire.");
            this.notification.add(msg, { type: "success", sticky: true });
        } catch (e) {
            this.state.error = e.message || _t("Export impossible.");
        } finally {
            this.state.busy = false;
        }
    }

    // -- codes de relève -----------------------------------------------------

    get recoveries() {
        return (this.state.vault && this.state.vault.recoveries) || [];
    }

    openRecovery() {
        this.state.error = "";
        this.state.recovCode = "";
        this.state.recovName = "";
        this.state.recovPass = "";
        this.state.showRecovery = true;
    }

    /**
     * Fabrique un code de relève. Redemande la phrase, comme l'enrôlement d'une
     * clé d'accès et pour la même raison : ajouter une porte se confirme par ce
     * qu'on SAIT.
     */
    async onCreateRecovery(ev) {
        ev.preventDefault();
        this.state.error = "";
        if (!this.state.recovPass) {
            this.state.error = _t("Entrez votre phrase de passe pour confirmer.");
            return;
        }
        this.state.busy = true;
        let octets = null;
        try {
            const v = this.state.vault;
            octets = await deriveKeyBytes(this.state.recovPass, v.salt, v.iterations);
            const controle = await keyFromBytes(octets);
            try {
                await decryptText(controle, v.verifier, v.verifier_iv);
            } catch {
                this.state.error = _t("Phrase de passe incorrecte.");
                return;
            }
            const code = genererCode();
            const scelle = await scellerPourCode(code, octets, v.iterations);
            await this.orm.call("bf.otp.vault", "add_recovery", [
                this.state.recovName || _t("Code de relève"),
                scelle.salt, scelle.iterations,
                scelle.wrapped_secret, scelle.wrapped_iv,
            ]);
            this.state.vault = await this.orm.call("bf.otp.vault", "get_my_vault", []);
            this.state.recovPass = "";
            this.state.recovName = "";
            // ⚠️ Le code s'affiche ICI et nulle part ailleurs. Il n'a pas été
            // envoyé, il n'est pas relisible, et fermer cet écran le perd.
            this.state.recovCode = code;
        } catch (e) {
            this.state.error = e.message || _t("Création impossible.");
        } finally {
            wipe(octets);
            this.state.busy = false;
        }
    }

    async onCopyRecoveryCode() {
        await browser.navigator.clipboard.writeText(this.state.recovCode);
        this.notification.add(
            _t("Code copié. Collez-le maintenant à l'endroit prévu : il ne se réaffichera pas."),
            { type: "warning", sticky: true }
        );
    }

    onImprimerCode() {
        window.print();
    }

    async onRemoveRecovery(r) {
        this.dialog.add(
            ConfirmationDialog,
            {
                title: _t("Révoquer ce code de relève"),
                body: _t(
                    "« %s » n'ouvrira plus ce coffre. L'enveloppe ou la fiche qui le porte devient inutile, et il faudra la détruire.",
                    r.name
                ),
                confirmLabel: _t("Révoquer"),
                confirm: async () => {
                    await this.orm.call("bf.otp.vault", "remove_recovery", [r.id]);
                    this.state.vault = await this.orm.call("bf.otp.vault", "get_my_vault", []);
                    this.notification.add(_t("Code de relève révoqué."), { type: "info" });
                },
                cancel: () => {},
            }
        );
    }

    /** Ouvre le coffre avec un code de relève, quand la phrase est perdue. */
    async onUnlockWithRecovery(ev) {
        ev.preventDefault();
        this.state.busy = true;
        this.state.error = "";
        let octets = null;
        try {
            const res = await ouvrirAvecCode(this.state.recovInput, this.recoveries);
            if (!res) {
                this.state.error = _t("Ce code n'ouvre pas ce coffre.");
                return;
            }
            octets = res.keyBytes;
            this._key = await keyFromBytes(octets);
            await this._chargerJetons();
            this.state.recovInput = "";
            this._ouvrir();
            this.orm.call("bf.otp.vault", "touch_recovery", [res.row_id]).catch(() => {});
            this.notification.add(
                _t("Coffre ouvert par un code de relève. Ce code a servi : si ce n'était pas prévu, révoquez-le."),
                { type: "warning", sticky: true }
            );
        } catch (e) {
            this.state.error = e.message || _t("Ouverture impossible.");
        } finally {
            wipe(octets);
            this.state.busy = false;
        }
    }

    // -- corbeille -----------------------------------------------------------

    async openTrash() {
        this.state.error = "";
        this.state.trash = await this.orm.call("bf.otp.token", "load_my_trash", []);
        this.state.showTrash = true;
    }

    async restoreToken(t) {
        await this.orm.call("bf.otp.token", "restore_token", [t.id]);
        this.state.trash = await this.orm.call("bf.otp.token", "load_my_trash", []);
        await this._chargerJetons();
        this.notification.add(_t("Token remis dans le coffre."), { type: "success" });
    }

    /**
     * Détruit un token pour de bon.
     *
     * 🔴 C'est le SECOND geste, et le seul irréversible du module. Le premier
     * (la corbeille) est réversible exprès : jusqu'à la 18.0.10.0.0 il ne
     * l'était pas, et un clic de travers effaçait un deuxième facteur dont
     * personne n'avait la graine ailleurs.
     */
    async purgeToken(t) {
        this.dialog.add(
            ConfirmationDialog,
            {
                title: _t("Détruire ce token"),
                body: _t(
                    "« %s » sera détruit. Aucune phrase de passe, aucun code de relève et aucune sauvegarde ne le rendra.",
                    `${t.issuer ? t.issuer + " · " : ""}${t.name}`
                ),
                confirmLabel: _t("Détruire"),
                confirm: async () => {
                    await this.orm.call("bf.otp.token", "purge_token", [t.id]);
                    this.state.trash = await this.orm.call("bf.otp.token", "load_my_trash", []);
                    this.notification.add(_t("Token détruit."), { type: "info" });
                },
                cancel: () => {},
            }
        );
    }

    async emptyTrash() {
        const combien = this.state.trash.length;
        this.dialog.add(
            ConfirmationDialog,
            {
                title: _t("Vider la corbeille"),
                body: _t(
                    "%s token(s) seront détruits. Rien ne les rendra. Si vous n'en êtes pas certain, exportez d'abord votre coffre.",
                    combien
                ),
                confirmLabel: _t("Tout détruire"),
                confirm: async () => {
                    const n = await this.orm.call("bf.otp.token", "empty_trash", []);
                    this.state.trash = [];
                    this.notification.add(_t("%s token(s) détruit(s).", n), { type: "info" });
                },
                cancel: () => {},
            }
        );
    }


    /**
     * Reproduit la dérivation du gestionnaire OTP de Nextcloud.
     *
     * ⚠️ Ce n'est PAS notre schéma et il ne faut pas s'en inspirer : là-bas la
     * clé est un simple SHA-256 de la phrase, sans étirement, et le vecteur est
     * le même pour tous les comptes d'un usager. On le reproduit uniquement
     * pour pouvoir lire un export existant, une fois, à l'import.
     */
    async _cleNextcloud(passphrase, ivHex) {
        const enc = new TextEncoder();
        const digest = await crypto.subtle.digest("SHA-256", enc.encode(passphrase));
        // Là-bas, `hash("sha256", …)` rend l'HEXADÉCIMAL, et `hex2bin` en fait
        // 32 octets : la clé est donc bien le condensat brut.
        return crypto.subtle.importKey("raw", digest, { name: "AES-CBC" }, false, ["decrypt"]);
    }

    async _dechiffrerNextcloud(key, cipherB64, ivHex) {
        try {
            const iv = new Uint8Array(ivHex.match(/../g).map((h) => parseInt(h, 16)));
            const bytes = Uint8Array.from(atob(cipherB64), (c) => c.charCodeAt(0));
            const clear = await crypto.subtle.decrypt({ name: "AES-CBC", iv }, key, bytes);
            return new TextDecoder().decode(clear);
        } catch {
            return null;
        }
    }
}

registry.category("actions").add("bf_otp.app", BfOtpApp);

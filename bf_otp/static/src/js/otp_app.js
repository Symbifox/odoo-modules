/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useState, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { browser } from "@web/core/browser/browser";
import {
    cryptoAvailable, buildVaultMaterial, unlockVault, encryptText, decryptText,
    deriveKeyBytes, keyFromBytes, wipe,
} from "./otp_crypto";
import {
    webauthnAvailable, enrollPasskey, unlockWithPasskey,
} from "./otp_webauthn";
import { base32Decode, totp, hotp, secondsLeft, parseOtpauth } from "./otp_totp";

/** Minutes d'inactivité après lesquelles le coffre se referme tout seul.
 *  Un coffre ouvert sur un écran laissé sans surveillance est un coffre ouvert
 *  pour qui passe : la clé vit en mémoire, elle doit en sortir. */
const VERROU_AUTO_MS = 5 * 60 * 1000;
const ITERATIONS = 600000;

export class BfOtpApp extends Component {
    static template = "bf_otp.App";
    static props = {};

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
            enrolPass: "",
            enrolName: "",
            form: this._formVierge(),
            importText: "",
            importPassword: "",
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
    chipStyle(t) {
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
            if (this.state.showForm || this.state.showImport) {
                this.state.showForm = false;
                this.state.showImport = false;
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
        if (this.state.showForm || this.state.showImport) {
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
        this.state.showForm = false;
        this.state.showImport = false;
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
                    "Utilise ta phrase de passe."
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
                    "Cette clé d'accès n'ouvre plus ce coffre. Utilise ta phrase de passe."
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
        if (!this.state.enrolPass) {
            this.state.error = _t("Entre ta phrase de passe pour confirmer.");
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
                _t("Coffre de jetons OTP"),
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
                _t("Clé d'accès enregistrée. Garde ta phrase : elle reste le seul recours si tu perds cet appareil."),
                { type: "success", sticky: true }
            );
        } catch (e) {
            this.state.error = e.message || _t("Enrôlement impossible.");
        } finally {
            wipe(octets);
            this.state.busy = false;
        }
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
            this.state.error = _t("Choisis une phrase d'au moins douze caractères.");
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
                _t("Coffre créé. Note ta phrase ailleurs : personne ne peut la retrouver, et sans elle les jetons sont perdus."),
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
     * Le regroupement affiché.
     *
     * ⚠️ Les favoris sortent de leur groupe et forment le leur, en tête : un
     * favori qu'il faut aller chercher dans son groupe n'est plus un favori.
     * En dessous, on regroupe par l'étiquette libre si elle existe, sinon par
     * client — ce qui range un coffre importé sans qu'on ait rien à saisir.
     */
    get groupes() {
        const favoris = [];
        const m = new Map();
        for (const t of this.visibleTokens) {
            if (t.favorite) {
                favoris.push(t);
                continue;
            }
            const g = t.group_name
                || (t.partner_id && t.partner_id[1])
                || (t.project_id && t.project_id[1])
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
            this.state.error = _t("La graine est obligatoire pour un jeton neuf.");
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
            (await import("@web/core/confirmation_dialog/confirmation_dialog")).ConfirmationDialog,
            {
                title: _t("Supprimer ce jeton"),
                body: _t(
                    "« %s » sera supprimé de ce coffre. Si tu n'as pas la graine ailleurs, le deuxième facteur de ce compte devient irrécupérable.",
                    `${t.issuer ? t.issuer + " — " : ""}${t.name}`
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
     * Lit un export du gestionnaire OTP de Nextcloud.
     *
     * Deux formes possibles. Sans `iv`, les graines sont EN CLAIR dans le
     * fichier ; avec `iv`, elles sont chiffrées par la phrase du coffre
     * d'origine, que la personne doit fournir ici. Dans les deux cas le
     * déchiffrement et le rechiffrement se font DANS CETTE PAGE : le serveur ne
     * voit passer que du chiffré.
     */
    async runImport(ev) {
        ev.preventDefault();
        this.state.error = "";
        let data;
        try {
            data = JSON.parse(this.state.importText);
        } catch {
            this.state.error = _t("Ce n'est pas du JSON valide.");
            return;
        }
        const comptes = data.accounts || data;
        if (!Array.isArray(comptes)) {
            this.state.error = _t("Ce fichier ne contient pas de liste « accounts ».");
            return;
        }
        this.state.busy = true;
        try {
            const chiffreALaSource = !!data.iv;
            let cleSource = null;
            if (chiffreALaSource) {
                if (!this.state.importPassword) {
                    this.state.error = _t(
                        "Cet export est chiffré : il faut la phrase de passe du coffre Nextcloud d'origine."
                    );
                    return;
                }
                cleSource = await this._cleNextcloud(this.state.importPassword, data.iv);
            }
            const entries = [];
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
                graine = (graine || "").replace(/[\s-]/g, "").toUpperCase();
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
                    otp_type: (c.type || "totp").toLowerCase() === "hotp" ? "hotp" : "totp",
                    algorithm: (c.algorithm || "SHA1").toUpperCase(),
                    digits: c.digits || 6,
                    period: c.period || 30,
                    counter: c.counter || 0,
                    secret_cipher: cipher,
                    secret_iv: iv,
                });
            }
            if (!entries.length) {
                this.state.error = _t("Aucun jeton lisible dans ce fichier.");
                return;
            }
            const res = await this.orm.call("bf.otp.token", "import_tokens", [entries]);
            this.state.showImport = false;
            this.state.importText = "";
            this.state.importPassword = "";
            await this._chargerJetons();
            let msg = _t("%s jeton(s) importé(s).", res.created);
            if (res.skipped) {
                msg += " " + _t("%s déjà présent(s), ignoré(s).", res.skipped);
            }
            if (refuses.length) {
                msg += " " + _t("%s illisible(s) : %s.", refuses.length, refuses.slice(0, 5).join(", "));
            }
            this.notification.add(msg, { type: refuses.length ? "warning" : "success", sticky: !!refuses.length });
        } finally {
            this.state.busy = false;
        }
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

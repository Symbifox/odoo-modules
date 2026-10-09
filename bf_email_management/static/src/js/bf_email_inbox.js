/** @odoo-module **/
/*
 * Boîte de réception Odoo, en action cliente OWL.
 *
 * Même mise en page que le navigateur IMAP — dossiers à gauche, liste en haut,
 * aperçu en bas — mais la source est ``bf.email`` au lieu du serveur IMAP :
 * les « dossiers » sont des états (Boîte, Non lus, À répondre, Sans dossier,
 * Reportés, Envoyés, Traités, par catégorie) et non des boîtes aux lettres.
 *
 * Clavier : j/k/↑/↓ naviguer · r répondre · shift+r répondre à tous ·
 *           f transférer · e Traité · y router · h reporter · t activité ·
 *           o ouvrir le dossier · s ou / rechercher · échap annuler.
 *
 * Tout l'accès aux données passe par les méthodes inbox_* de bf.email, qui
 * restent dans l'environnement de l'usager : aucune ligne d'un collègue ne
 * peut apparaître ici.
 */

import { registry } from "@web/core/registry";
import {
    Component,
    useState,
    onWillStart,
    onMounted,
    onPatched,
    onWillUnmount,
    useRef,
    toRaw,
} from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { useHotkey } from "@web/core/hotkeys/hotkey_hook";
import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
import { user } from "@web/core/user";
import {
    loadSettings,
    persistSettings,
    formatRelativeDate,
    senderCell,
    buildPreviewSrcdoc,
    flattenTree,
    paneStyles,
    startPaneDrag,
    columnWidths,
    selectRange,
    loadRail,
    saveRail,
    claimKeys,
    releaseKeys,
    ownsKeys,
} from "./bf_email_ui_common";

// Dossiers acceptant un dépôt de ligne, et l'action que ça déclenche.
const DROP_ACTIONS = {
    handled: "handle",
    inbox: "unhandle",
    snoozed: "snooze",
};

// Dossier des envois programmés. Sa source n'est pas ``bf.email`` : la liste
// et l'aperçu changent d'appel serveur quand il est ouvert.
const DRAFTS_FOLDER = "drafts";

// Lots d'anciens courriels retenus à l'ingestion. Pas une source de
// lignes : l'écran y montre les lots et leurs deux boutons.
const HELD_FOLDER = "held";

// Le dossier où s'ouvrent les courriels d'un contact : reçus,
// envoyés et traités ensemble, comme l'historique qu'on vient chercher.
const CONTACT_FOLDER = "all";

// En deçà, le panneau du systray part avec le volet replié, tant que la
// personne n'a pas choisi elle-même.
const PANEL_RAIL_BELOW_PX = 900;

// Ce que l'onglet a déjà vu, pour l'afficher tout de suite à la
// réouverture (panneau du systray, retour sur un dossier), puis le revalider
// par l'ORM. En mémoire seulement : rien sur le disque, rien après la
// fermeture de l'onglet. Clé par usager : une session qui change d'usager ne
// lit jamais la boîte d'un autre. Ni corps ni aperçu : ouvrir un courriel le
// marque lu au serveur, ce geste-là reste un appel.
const DEJA_VU = { uid: null, folders: null, pages: new Map() };

function dejaVu() {
    if (DEJA_VU.uid !== user.userId) {
        DEJA_VU.uid = user.userId;
        DEJA_VU.folders = null;
        DEJA_VU.pages.clear();
    }
    return DEJA_VU;
}

// Plafond d'un « Envoyer vers Gen » groupé, le même que la route serveur
// (`SEND_TO_GEN_MAX` de bf_claude_chat).
const GEN_SEND_MAX = 10;

// Ce qui se défait, et par quoi. Voir `undoLast` pour ce qui n'y est pas.
const UNDO_INVERSE = {
    handle: "unhandle",
    unhandle: "handle",
    snooze: "unsnooze",
    mute: "unmute",
    unmute: "mute",
    trash: "unhandle",
    // Posé par `_afterReport`, à la fermeture de la fenêtre
    // « Signaler », et non par `_dispatch` : l'ouvrir ne signale rien encore.
    spam: "unhandle",
};

export class BfEmailInbox extends Component {
    static template = "bf_email_management.Inbox";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        // Dernière case cochée : point de départ d'un shift+clic. Hors du
        // `useState` volontairement — personne ne l'affiche, et la rendre
        // réactive redessinerait la liste à chaque clic pour rien.
        this._selectionAnchor = null;

        this.searchInputRef = useRef("searchInput");
        this.listBottomRef = useRef("listBottom");
        this.selectAllRef = useRef("selectAll");
        this.rootRef = useRef("root");

        const initialSettings = loadSettings();
        this.state = useState({
            folders: [],
            currentFolder: "inbox",
            messages: [],
            total: 0,
            offset: 0,
            pageSize: initialSettings.pageSize,
            selectedId: null,
            // Objet simple pour la réactivité OWL : clés = ids, valeurs = true.
            selectedIds: {},
            preview: null,
            // : la grille des raccourcis, ouverte par « ? ».
            showShortcuts: false,
            // Lecture par conversation. Retenue avec les autres préférences
            // d'affichage : c'est une habitude, pas un état de session.
            grouped: !!initialSettings.grouped,
            genAvailable: false,
            genText: null,
            genKind: null,
            genLoading: false,
            // « 🪄 Envoyer vers Gen » : offert si Gen est installé
            // et ouvert à cet usager, indépendamment de `genAvailable`.
            genChatAvailable: false,
            genSending: false,
            subscriptions: null,
            loadingFolders: true,
            loadingMessages: false,
            loadingMoreMessages: false,
            loadingPreview: false,
            acting: false,
            syncing: false,
            searchQuery: "",
            // La fiche contact dont on lit les courriels, posée par le
            // bouton « Courriels » de la fiche ({id, name}), ou rien.
            contact: this._initialContact(),
            // « inbox » ouvert d'entrée : ses enfants sont les boîtes par
            // compte, et une boîte qu'il faut déplier pour voir
            // n'existe pas. « categories » reste replié, il l'a toujours été.
            expandedFolders: { categories: false, inbox: true },
            dragId: null,
            dropTarget: null,
            settings: initialSettings,
            settingsOpen: false,
            columnsOpen: false,
            // Glisser du séparateur liste / aperçu.
            dragging: false,
            // Les lots qui attendent une décision.
            heldLots: [],
            heldMaxAge: 30,
            // Repli d'office du panneau étroit, tant que la personne
            // n'a rien choisi ; et les choix mémorisés, par écran.
            autoCollapsed: false,
            rail: loadRail(),
        });

        onWillStart(async () => {
            // Les deux appels partent ensemble ; quatre appels en file
            // coûtaient 1,3 à 1,7 s avant le premier affichage. Si l'onglet a
            // déjà vu la boîte, on l'affiche tout de suite et on revalide
            // derrière.
            const cache = dejaVu();
            // Ouverte sur un contact, la boîte part de « Tous les
            // courriels » (reçus, envoyés, traités) et ne lit jamais le cache.
            const dossier = this.state.contact ? CONTACT_FOLDER : "inbox";
            const dejaAffichable = !this.state.contact
                && !!(cache.folders && cache.pages.has(this._pageKey("inbox")));
            const chargement = Promise.all([
                this.loadFolders(),
                this.loadMessages(dossier, 0),
            ]);
            if (!dejaAffichable) {
                await chargement;
            }
            // On demande UNE fois si Gen est là, hors du chemin
            // critique : ses boutons paraissent à la réponse.
            this._loadGenAvailability();
        });

        this.busService = useService("bus_service");
        this._refreshTimer = null;

        onMounted(() => {
            // Référence gardée pour pouvoir se désabonner : sinon chaque
            // ouverture de la boîte laisse un abonné derrière elle.
            this._onBusTick = () => this._refreshSoon();
            this.busService.subscribe("bf_email/changed", this._onBusTick);
            this.busService.start();
            this._observer = new IntersectionObserver((entries) => {
                if (entries.some((e) => e.isIntersecting)
                        && this.canLoadMore
                        && !this.state.loadingMoreMessages
                        && !this.state.loadingMessages) {
                    this.loadMoreMessages();
                }
            }, { rootMargin: "200px" });
            if (this.listBottomRef.el) {
                this._observer.observe(this.listBottomRef.el);
            }
            document.addEventListener("keydown", this._onSlashKey);
            claimKeys(this);
            // Le panneau du systray se tire de 40 à 100 % de
            // l'écran ; on regarde sa largeur réelle, pas celle de l'écran.
            if (this.props.inPanel && this.rootRef.el && window.ResizeObserver) {
                this._widthObserver = new ResizeObserver((entries) => {
                    const width = entries[0] && entries[0].contentRect.width;
                    if (width) {
                        this.state.autoCollapsed = width < PANEL_RAIL_BELOW_PX;
                    }
                });
                this._widthObserver.observe(this.rootRef.el);
            }
        });

        // `indeterminate` ne s'exprime pas en attribut : sans ce crochet, une
        // sélection partielle s'afficherait comme « rien de coché », ce qui
        // est le contraire de ce qu'elle est.
        onPatched(() => {
            if (this.selectAllRef.el) {
                this.selectAllRef.el.indeterminate = this.someLoadedSelected;
            }
        });

        onWillUnmount(() => {
            // Sans ça, un tick reçu juste avant le démontage rappellerait
            // refreshInPlace sur un composant détruit.
            if (this._onBusTick) {
                this.busService.unsubscribe("bf_email/changed", this._onBusTick);
            }
            if (this._refreshTimer) clearTimeout(this._refreshTimer);
            if (this._observer) this._observer.disconnect();
            if (this._widthObserver) this._widthObserver.disconnect();
            releaseKeys(this);
            if (this._searchTimer) clearTimeout(this._searchTimer);
            document.removeEventListener("keydown", this._onSlashKey);
        });

        // --- Raccourcis, même vocabulaire que le navigateur IMAP ---
        useHotkey("arrowdown", () => this.selectNext(), { bypassEditableProtection: false });
        useHotkey("arrowup", () => this.selectPrev(), { bypassEditableProtection: false });
        useHotkey("j", () => this.selectNext());
        useHotkey("k", () => this.selectPrev());
        useHotkey("r", () => this.runAction("reply"));
        useHotkey("shift+r", () => this.runAction("reply_all"));
        useHotkey("f", () => this.runAction("forward"));
        useHotkey("e", () => this.markHandled());
        useHotkey("y", () => this.runAction("reroute"));
        useHotkey("h", () => this.runAction("snooze"));
        useHotkey("t", () => this.runAction("activity"));
        // : `m` comme dans Gmail. La bascule lit l'état du message
        // affiché plutôt que de poser deux touches, parce qu'un fil qu'on
        // vient de faire taire est exactement celui qu'on voudra réveiller.
        useHotkey("m", () => this.toggleMute());
        useHotkey("z", () => this.undoLast());
        useHotkey("shift+u", () => this.markFolderRead());
        useHotkey("o", () => this.openSourceRecord());
        useHotkey("c", () => this.compose());
        useHotkey("s", () => this.focusSearch());
        useHotkey("escape", () => this.onEscape(), { bypassEditableProtection: true });

        // Odoo n'autorise pas "/" dans sa liste blanche de raccourcis ; on le
        // câble nativement pour la mémoire musculaire Gmail/Thunderbird.
        this._onSlashKey = (ev) => {
            if (!ownsKeys(this)) return;
            // `[` replie les dossiers, `!` signale un
            // pourriel. Hors de la liste blanche d'Odoo, comme `/` et `?` ;
            // jamais dans un champ, jamais derrière une fenêtre ouverte.
            if ((ev.key === "[" || ev.key === "!")
                    && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
                const cible = ev.target;
                const saisie = cible && (cible.tagName === "INPUT"
                    || cible.tagName === "TEXTAREA" || cible.tagName === "SELECT"
                    || cible.isContentEditable);
                if (saisie || document.querySelector(".modal.show, .o_dialog")) return;
                ev.preventDefault();
                if (ev.key === "[") {
                    this.toggleFoldersPane();
                } else {
                    this.reportSpam();
                }
                return;
            }
            if (ev.key === "?") {
                const cible = ev.target;
                const saisie = cible && (cible.tagName === "INPUT"
                    || cible.tagName === "TEXTAREA" || cible.isContentEditable);
                if (saisie) return;
                ev.preventDefault();
                this.toggleShortcutHelp();
                return;
            }
            if (ev.key !== "/") return;
            const t = ev.target;
            const editable = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
            if (editable) return;
            ev.preventDefault();
            this.focusSearch();
        };
    }

    // ------------------------------------------------------------------
    // Composer / synchroniser
    // ------------------------------------------------------------------
    /**
     * Nouveau courriel, rattaché à aucun dossier. Le serveur crée une ligne
     * qui sert de fil à elle-même ; à la fermeture du composeur elle est soit
     * adoptée (un message a été posté), soit effacée (composeur annulé).
     */
    async compose() {
        if (this.state.acting) return;
        this.state.acting = true;
        try {
            const action = await this.orm.call("bf.email", "inbox_compose", []);
            const shellId = action && action.context
                ? action.context.bf_email_compose_shell_id
                : null;
            await this.action.doAction(action, {
                onClose: async () => {
                    if (shellId) {
                        try {
                            await this.orm.call(
                                "bf.email", "inbox_close_compose", [],
                                { shell_id: shellId }
                            );
                        } catch (err) {
                            // Le ménage de la coquille ne doit jamais masquer
                            // le fait que le courriel, lui, est parti.
                            console.warn("bf_email_inbox: adoption échouée", err);
                        }
                    }
                    await this.refreshCurrent();
                },
            });
        } catch (err) {
            this.notification.add(
                _t("Composition impossible : ") + (err.message || err),
                { type: "danger" }
            );
        } finally {
            this.state.acting = false;
        }
    }

    /**
     * Même travail que « Synchroniser maintenant » de la vue liste : on tire
     * les chatters et l'IMAP, puis on recharge dossiers et liste sur place.
     * L'action serveur, elle, recharge tout le client web — ce qui jetterait
     * l'aperçu ouvert.
     */
    async syncNow() {
        if (this.state.syncing) return;
        this.state.syncing = true;
        try {
            const res = await this.orm.call("bf.email", "inbox_sync_now", []);
            await this.refreshCurrent();
            this.notification.add(res.message || "", {
                title: res.title,
                type: res.type === "success" ? "success" : "info",
            });
        } catch (err) {
            this.notification.add(
                _t("Synchronisation impossible : ") + (err.message || err),
                { type: "danger" }
            );
        } finally {
            this.state.syncing = false;
        }
    }

    // ------------------------------------------------------------------
    // Dossiers
    // ------------------------------------------------------------------
    async _loadGenAvailability() {
        const [gen, chat] = await Promise.all([
            this.orm.call("bf.email", "inbox_gen_available", []).catch(() => false),
            this.orm.call("bf.email", "inbox_gen_chat_available", []).catch(() => false),
        ]);
        this.state.genAvailable = !!gen;
        this.state.genChatAvailable = !!chat;
    }

    /**
     * Le contact passé par le bouton de la fiche. Le panneau du
     * systray le donne en propriété, la pleine page dans les paramètres de
     * l'action.
     */
    _initialContact() {
        const contact = this.props.contact || this.props.action?.params?.contact;
        return contact && contact.id ? { id: contact.id, name: contact.name || "" } : null;
    }

    /**
     * Ce que la recherche envoie au serveur : le filtre de contact, s'il y en
     * a un, puis ce que la personne a tapé. Le contact passe par l'opérateur
     * `contact:` de la grammaire : liste, fils et défilement le suivent sans
     * le savoir. Les brouillons ne sont pas des courriels et l'ignorent.
     */
    _serverQuery(folder) {
        const parts = [];
        if (this.state.contact && folder !== DRAFTS_FOLDER) {
            parts.push(`contact:#${this.state.contact.id}`);
        }
        if (this.state.searchQuery) {
            parts.push(this.state.searchQuery);
        }
        return parts.join(" ") || null;
    }

    /** Retire le filtre de contact ; « Tous les courriels » rend la main à la boîte. */
    clearContact() {
        this.state.contact = null;
        // Le cadre qui nomme le contact (titre du panneau) l'apprend aussi.
        this.props.onContactCleared?.();
        const dossier = this.state.currentFolder === CONTACT_FOLDER ? "inbox" : this.state.currentFolder;
        this.loadMessages(dossier, 0);
    }

    /** Clé du cache de page : le dossier et la lecture par conversation. */
    _pageKey(folder) {
        return `${folder}|${this.state.grouped ? 1 : 0}`;
    }

    async loadFolders() {
        const cache = dejaVu();
        if (!this.state.folders.length && cache.folders) {
            this.state.folders = [...cache.folders];
        }
        this.state.loadingFolders = true;
        try {
            this.state.folders = await this.orm.call(
                "bf.email", "inbox_get_folders", []
            );
            // Copie brute : le cache ne partage pas l'état réactif d'un écran.
            cache.folders = [...toRaw(this.state.folders)];
        } catch (err) {
            this.notification.add(
                _t("Impossible de charger les dossiers : ") + (err.message || err),
                { type: "danger" }
            );
        } finally {
            this.state.loadingFolders = false;
        }
    }

    get folderTree() {
        return flattenTree(this.state.folders, this.state.expandedFolders);
    }

    get currentFolderLabel() {
        const f = this.state.folders.find((x) => x.key === this.state.currentFolder);
        return f ? f.label : this.state.currentFolder;
    }

    toggleFolder(key) {
        this.state.expandedFolders[key] = !this.state.expandedFolders[key];
    }

    // ------------------------------------------------------------------
    // Volet des dossiers replié en rail
    // ------------------------------------------------------------------
    /** Chaque écran garde son choix : plein écran et panneau du systray. */
    get railScreen() {
        return this.props.inPanel ? "panel" : "inbox";
    }

    get foldersCollapsed() {
        const choisi = this.state.rail[this.railScreen];
        if (choisi === true || choisi === false) {
            return choisi;
        }
        return !!(this.props.inPanel && this.state.autoCollapsed);
    }

    /** Le rail : les dossiers de premier niveau qu'on peut ouvrir. */
    get railFolders() {
        return this.state.folders.filter((f) => !f.parent && f.selectable);
    }

    toggleFoldersPane() {
        this.state.rail = saveRail(this.railScreen, !this.foldersCollapsed);
    }

    onFolderClick(folder) {
        if (!folder.selectable) {
            this.toggleFolder(folder.key);
            return;
        }
        this.state.searchQuery = "";
        this.loadMessages(folder.key, 0);
    }

    // ------------------------------------------------------------------
    // Liste
    // ------------------------------------------------------------------
    /** Le dossier ouvert est-il celui des envois programmés ? */
    get isDraftFolder() {
        return this.state.currentFolder === DRAFTS_FOLDER;
    }

    /** Le dossier ouvert est-il « À décider » ? */
    get isHeldFolder() {
        return this.state.currentFolder === HELD_FOLDER;
    }

    async loadHeld() {
        this.state.loadingMessages = true;
        try {
            const res = await this.orm.call(
                "bf.email.held", "held_summary", []);
            // La forme d'avant (une liste) reste lue : un onglet ouvert pendant
            // la montée parle encore à l'ancien serveur un instant.
            this.state.heldLots = Array.isArray(res) ? res : (res.lots || []);
            if (!Array.isArray(res) && res.max_age_days) {
                this.state.heldMaxAge = res.max_age_days;
            }
        } catch (err) {
            this.state.heldLots = [];
            this.notification.add(
                _t("Chargement impossible : ") + (err.message || err),
                { type: "danger" });
        } finally {
            this.state.loadingMessages = false;
        }
    }

    async decideHeld(lot, decision) {
        if (this.state.acting) return;
        this.state.acting = true;
        try {
            const n = await this.orm.call(
                "bf.email.held", "held_decide",
                [lot.account_id, lot.folder, decision]);
            this.notification.add(
                decision === "add"
                    ? _t("%s courriel(s) en cours d'ajout, sans avis.", n)
                    : _t("%s courriel(s) ignoré(s).", n),
                { type: "success" });
        } catch (err) {
            this.notification.add(
                _t("Échec : ") + (err.message || err), { type: "danger" });
        } finally {
            this.state.acting = false;
        }
        await this.loadHeld();
        await this.loadFolders();
        if (!this.state.heldLots.length) {
            this.loadMessages("inbox", 0);
        }
    }

    async loadMessages(folder, offset = 0) {
        if (folder === HELD_FOLDER) {
            this.state.currentFolder = folder;
            this.state.messages = [];
            this.state.total = 0;
            this.state.selectedId = null;
            this.state.preview = null;
            this.state.selectedIds = {};
            await this.loadHeld();
            return;
        }
        this.state.loadingMessages = true;
        this.state.currentFolder = folder;
        this.state.offset = offset;
        this.state.selectedId = null;
        this.state.preview = null;
        this.state.selectedIds = {};
        this._selectionAnchor = null;
        // La dernière page vue de ce dossier s'affiche tout de suite ;
        // la réponse du serveur la remplace. Jamais pendant une recherche, ni
        // sous un filtre de contact : la clé ne porte que le dossier,
        // et la page d'un contact passerait pour celle de toute la boîte.
        const cache = dejaVu();
        const cle = this._pageKey(folder);
        const cachable = offset === 0 && !this.state.searchQuery && !this.state.contact;
        if (cachable && cache.pages.has(cle)) {
            const vu = cache.pages.get(cle);
            this.state.messages = [...vu.messages];
            this.state.total = vu.total;
        }
        // Seul le DERNIER chargement écrit la liste (dossier changé, recherche
        // tapée pendant l'appel).
        const jeton = (this._loadSeq = (this._loadSeq || 0) + 1);
        try {
            const result = await this._fetchPage(folder, offset);
            if (jeton !== this._loadSeq) {
                return;
            }
            this.state.messages = result.messages || [];
            this.state.total = result.total || 0;
            // Une ligne cliquée dans la page du cache et absente de la
            // réponse ne garde ni sélection ni aperçu.
            if (this.state.selectedId
                    && !this.state.messages.some((m) => m.id === this.state.selectedId)) {
                this.state.selectedId = null;
                this.state.preview = null;
            }
            if (cachable) {
                cache.pages.set(cle, {
                    messages: [...toRaw(this.state.messages)],
                    total: this.state.total,
                });
            }
        } catch (err) {
            if (jeton !== this._loadSeq) {
                return;
            }
            this.state.messages = [];
            this.state.total = 0;
            this.notification.add(
                _t("Chargement impossible : ") + (err.message || err),
                { type: "danger" }
            );
        } finally {
            if (jeton === this._loadSeq) {
                this.state.loadingMessages = false;
            }
        }
    }

    /**
     * Une page, quelle qu'en soit la source. Les deux dossiers ont le même
     * contrat de sortie ({messages, total}), donc pagination, recherche et
     * défilement infini n'ont pas à savoir lequel est ouvert.
     */
    async _fetchPage(folder, offset, limit = null) {
        const size = limit || this.state.pageSize;
        if (folder === HELD_FOLDER) {
            return { messages: [], total: 0 };
        }
        if (folder === DRAFTS_FOLDER) {
            return this.orm.call("bf.email", "inbox_get_drafts", [], {
                offset,
                limit: size,
                search: this._serverQuery(folder),
            });
        }
        if (this.state.grouped) {
            // : le même contrat de sortie ({messages, total}) que le
            // mode ordinaire, pour que pagination, recherche et défilement
            // infini n'aient pas à savoir lequel est ouvert. C'est la même
            // règle que le dossier Brouillons suit déjà.
            const res = await this.orm.call("bf.email", "inbox_get_threads", [], {
                folder,
                offset,
                limit: size,
                search: this._serverQuery(folder),
            });
            return { messages: res.threads || [], total: res.total || 0 };
        }
        return this.orm.call("bf.email", "inbox_get_messages", [], {
            folder,
            offset,
            limit: size,
            search: this._serverQuery(folder),
        });
    }

    /**
     * Rafraîchissement de fond, déclenché par le bus.
     *
     * Volontairement PAS `loadMessages` : celle-ci remet l'offset à zéro et
     * efface la sélection, l'aperçu et les cases cochées. Un courriel qui
     * arrive pendant qu'on lit ne doit pas fermer ce qu'on lit ni faire
     * remonter la liste sous le curseur.
     *
     * Recharge la tranche déjà affichée — défilement infini compris — et ne
     * lâche la sélection que si la ligne a réellement quitté le dossier.
     */
    async refreshInPlace() {
        if (this.state.loadingMessages || this.state.loadingMoreMessages
                || this.state.acting || this.state.dragId
                || this.state.syncing || this._inflight > 0) {
            // Une action est en vol : réessayer plus tard plutôt que de
            // recharger par-dessus.
            this._refreshSoon();
            return;
        }
        const folder = this.state.currentFolder;
        const query = this._serverQuery(folder);
        const span = Math.max(this.state.messages.length, this.state.pageSize);
        const keptId = this.state.selectedId;
        try {
            const result = await this._fetchPage(folder, this.state.offset, span);
            // L'usager a changé de dossier, de recherche ou de filtre de
            // contact entre-temps : la réponse ne décrit plus ce qui
            // est affiché, et `_rememberPage` la rangerait sous le mauvais nom.
            if (folder !== this.state.currentFolder || query !== this._serverQuery(folder)) {
                return;
            }
            this.state.messages = result.messages || [];
            this.state.total = result.total || 0;
            this._rememberPage();
            if (keptId && !this.state.messages.some((m) => m.id === keptId)) {
                // La ligne a quitté le dossier : garder l'aperçu ouvert
                // pointerait sur quelque chose qui n'est plus là.
                this.state.selectedId = null;
                this.state.preview = null;
            }
            await this.loadFolders();
        } catch (err) {
            // Un rafraîchissement de fond ne dérange personne avec un toast.
            console.warn("bf_email_inbox: refresh failed", err);
        }
    }

    /**
     * Une passe d'ingestion appelle create() par message : une livraison de
     * cinquante courriels produit cinquante ticks. On n'en garde qu'un.
     */
    _refreshSoon() {
        if (this._refreshTimer) {
            clearTimeout(this._refreshTimer);
        }
        this._refreshTimer = setTimeout(() => {
            this._refreshTimer = null;
            this.refreshInPlace();
        }, 500);
    }

    async loadMoreMessages() {
        if (this.state.loadingMoreMessages || !this.canLoadMore) return;
        this.state.loadingMoreMessages = true;
        const nextOffset = this.state.offset + this.state.messages.length;
        const folder = this.state.currentFolder;
        const query = this._serverQuery(folder);
        try {
            const result = await this._fetchPage(folder, nextOffset);
            if (folder !== this.state.currentFolder || query !== this._serverQuery(folder)) {
                return;  // la liste a changé de dossier ou de filtre
            }
            // Un « Traité » en vol décale l'offset d'une ligne : ne jamais
            // pousser un id déjà là (clé en double dans t-foreach).
            const deja = new Set(this.state.messages.map((m) => m.id));
            this.state.messages.push(
                ...(result.messages || []).filter((m) => !deja.has(m.id)));
        } catch (err) {
            this.notification.add(
                _t("Chargement supplémentaire échoué : ") + (err.message || err),
                { type: "danger" }
            );
        } finally {
            this.state.loadingMoreMessages = false;
        }
    }

    get canLoadMore() {
        return this.state.offset + this.state.messages.length < this.state.total;
    }

    /** La recherche est servie par le serveur : pas de filtrage local. */
    get visibleMessages() {
        return this.state.messages;
    }

    selectMessage(id) {
        if (!id || this.state.selectedId === id) return;
        this.state.selectedId = id;
        this._fetchPreview(id);
    }

    /**
     * Demande au serveur de rendre le corps avec ses images distantes.
     * Un geste par message : rien n'est retenu, parce que « j'ai fait
     * confiance à celui-là » ne veut pas dire « je fais confiance aux
     * suivants »..
     */
    /**
     * Défait la dernière action défaisable (`z`, comme Gmail).
     *
     * ⚠️ Ce qui n'est PAS dans la table est volontairement absent : un
     * re-routage a déplacé un message dans le chatter d'une autre fiche, avec
     * une note à la clé, et « annuler » ne rendrait pas la fiche d'origine à
     * son état. Mieux vaut ne rien promettre que promettre à moitié.
     */
    async undoLast() {
        // Un « Traité » encore en vol se termine d'abord ; défaire
        // avant sa réponse ramènerait une ligne que le serveur range ensuite.
        if (this._enVol && this._enVol.size) {
            await Promise.allSettled([...this._enVol]);
        }
        if (!this._undoable) {
            this.notification.add(_t("Rien à annuler."), { type: "info" });
            return;
        }
        const { action, ids } = this._undoable;
        this._undoable = null;
        await this._dispatch(action, ids, {
            clearSelection: false,
            notify: _t("Action annulée."),
        });
        await this.refreshCurrent();
    }

    /** La grille des raccourcis, que rien n'affichait jusqu'ici. */
    toggleShortcutHelp() {
        this.state.showShortcuts = !this.state.showShortcuts;
    }

    async markFolderRead() {
        if (this.isDraftFolder || this.isHeldFolder || this.state.acting) return;
        this.state.acting = true;
        try {
            const res = await this.orm.call(
                "bf.email", "inbox_mark_folder_read", [],
                // Ce que la liste montre, pas tout le dossier.
                { folder: this.state.currentFolder,
                  search: this._serverQuery(this.state.currentFolder) }
            );
            this.notification.add(
                res.remaining
                    ? _t("%s marqués comme lus, %s restants.", res.marked, res.remaining)
                    : _t("%s marqués comme lus.", res.marked),
                { type: "success" }
            );
            // `refreshCurrent` relit déjà les dossiers.
            await this.refreshCurrent();
        } catch (err) {
            this.notification.add(_t("Échec : ") + (err.message || err),
                                  { type: "danger" });
        } finally {
            this.state.acting = false;
        }
    }

    /** Bascule le pli par conversation, et retient le choix. */
    async toggleGrouped() {
        this.state.grouped = !this.state.grouped;
        const next = { ...this.state.settings, grouped: this.state.grouped };
        this.state.settings = next;
        persistSettings(next);
        await this.refreshCurrent();
    }

    async loadSubscriptions() {
        this.state.acting = true;
        try {
            this.state.subscriptions = await this.orm.call(
                "bf.email", "inbox_subscriptions", []);
        } catch (err) {
            this.notification.add(_t("Échec : ") + (err.message || err),
                                  { type: "danger" });
        } finally {
            this.state.acting = false;
        }
    }

    async unsubscribeSender(lastId) {
        await this._dispatch("unsubscribe", [lastId], {
            clearSelection: false,
        });
        await this.loadSubscriptions();
    }

    /** Résumé ou réponse proposée. Rendu à l'écran, rien n'est écrit. */
    async askGen(kind) {
        const id = this.state.selectedId;
        if (!id || this.state.genLoading) return;
        this.state.genLoading = true;
        this.state.genText = null;
        this.state.genKind = kind;
        try {
            const res = await this.orm.call(
                "bf.email", "inbox_gen", [], { kind, email_id: id });
            this.state.genText = res.text;
        } catch (err) {
            this.notification.add(_t("Gen : ") + (err.message || err),
                                  { type: "danger" });
            this.state.genKind = null;
        } finally {
            this.state.genLoading = false;
        }
    }

    closeGen() {
        this.state.genText = null;
        this.state.genKind = null;
    }

    /**
     * « 🪄 Envoyer vers Gen ». Une conversation par courriel, rattachée
     * à lui ; la consigne « Mets-moi en contexte » part en tour d'arrière-plan,
     * le même que « Envoyer à Gen » du téléphone. La personne reste dans sa
     * boîte et le courriel n'en sort pas.
     * Un courriel qui a déjà sa conversation active n'en reçoit pas une
     * seconde : l'avis propose de l'ouvrir.
     */
    async sendToGen(ids) {
        const cibles = [...new Set((ids || []).filter(Boolean))];
        if (!cibles.length || this.state.genSending) {
            return;
        }
        if (cibles.length > GEN_SEND_MAX) {
            this.notification.add(
                _t("Au plus %s courriels à la fois vers Gen.", GEN_SEND_MAX),
                { type: "warning" });
            return;
        }
        this.state.genSending = true;
        try {
            const res = await rpc("/claude-chat/send-to-gen", {
                model: "bf.email", res_ids: cibles,
            });
            if (res.error) {
                const raisons = {
                    disabled: _t("Gen est éteint sur cette instance."),
                    unavailable: _t("Gen ne répond pas en ce moment. Rien n'a été envoyé."),
                };
                this.notification.add(
                    raisons[res.error] || _t("Gen n'a pas pu recevoir ces courriels."),
                    { type: "danger" });
                return;
            }
            this._notifySentToGen(res.results || []);
        } catch (err) {
            this.notification.add(_t("Gen : ") + (err.message || err),
                                  { type: "danger" });
        } finally {
            this.state.genSending = false;
        }
    }

    _notifySentToGen(results) {
        const envoyes = results.filter((r) => r.session_id && !r.existing);
        const deja = results.filter((r) => r.existing);
        const refuses = results.filter((r) => r.error);
        const lignes = [];
        if (envoyes.length === 1) {
            lignes.push(_t("Envoyé à Gen : %s", envoyes[0].name));
        } else if (envoyes.length > 1) {
            lignes.push(_t("%s courriels envoyés à Gen, un topo chacun.", envoyes.length));
        }
        if (deja.length === 1) {
            lignes.push(_t("Déjà chez Gen : %s", deja[0].name));
        } else if (deja.length > 1) {
            lignes.push(_t("%s courriels avaient déjà leur conversation.", deja.length));
        }
        if (refuses.length) {
            lignes.push(_t("%s refusé(s) : introuvable ou trop de demandes en une minute.",
                           refuses.length));
        }
        const cible = envoyes.at(-1) || deja.at(-1);
        // Dix secondes plutôt que les quatre d'Odoo : le temps de lire l'avis
        // ET d'atteindre « Ouvrir », qui le referme.
        const fermer = this.notification.add(lignes.join(" "), {
            type: refuses.length ? "warning" : "success",
            autocloseDelay: 10000,
            buttons: cible ? [{
                name: _t("Ouvrir"),
                primary: true,
                onClick: () => {
                    fermer();
                    this.openGenSession(cible.session_id);
                },
            }] : [],
        });
    }

    openGenSession(sessionId) {
        this.action.doAction({
            type: "ir.actions.client",
            tag: "claude_chat",
            params: { gen_session: sessionId },
        });
    }

    async toggleMute() {
        const preview = this.state.preview;
        if (!preview || !preview.id) {
            return;
        }
        await this.runAction(preview.is_muted ? "unmute" : "mute");
    }

    async loadRemoteImages() {
        const id = this.state.selectedId;
        if (id) {
            await this._fetchPreview(id, true);
        }
    }

    async _fetchPreview(id, loadImages = false) {
        this.state.loadingPreview = true;
        // Un résumé appartient au message qu'on vient de quitter.
        this.state.genText = null;
        this.state.genKind = null;
        this.state.preview = null;
        try {
            if (this.isDraftFolder) {
                this.state.preview = await this.orm.call(
                    "bf.email", "inbox_get_draft_body", [], { draft_id: id }
                );
                return;
            }
            const preview = await this.orm.call(
                "bf.email", "inbox_get_body", [],
                { email_id: id, load_images: !!loadImages }
            );
            this.state.preview = preview;
            const row = this.state.messages.find((m) => m.id === id);
            if (row && preview.seen) {
                // Le serveur vient de basculer « nouveau » en « lu » : on
                // enlève le gras tout de suite plutôt qu'au prochain chargement.
                row.seen = true;
                row.status = preview.status;
                // Regroupé avec le tick du bus qui suit la lecture.
                this._refreshSoon();
            }
        } catch (err) {
            this.notification.add(
                _t("Aperçu impossible : ") + (err.message || err),
                { type: "danger" }
            );
        } finally {
            this.state.loadingPreview = false;
        }
    }

    selectNext() {
        const list = this.visibleMessages;
        if (!list.length) return;
        if (!this.state.selectedId) {
            this.selectMessage(list[0].id);
            return;
        }
        const i = list.findIndex((m) => m.id === this.state.selectedId);
        if (i >= 0 && i < list.length - 1) {
            this.selectMessage(list[i + 1].id);
        }
    }

    selectPrev() {
        const list = this.visibleMessages;
        if (!list.length) return;
        if (!this.state.selectedId) {
            this.selectMessage(list[0].id);
            return;
        }
        const i = list.findIndex((m) => m.id === this.state.selectedId);
        if (i > 0) {
            this.selectMessage(list[i - 1].id);
        }
    }

    // ------------------------------------------------------------------
    // Recherche
    // ------------------------------------------------------------------
    focusSearch() {
        if (this.searchInputRef.el) {
            this.searchInputRef.el.focus();
            this.searchInputRef.el.select();
        }
    }

    clearSearch() {
        if (!this.state.searchQuery) {
            if (this.searchInputRef.el) this.searchInputRef.el.blur();
            return;
        }
        this.state.searchQuery = "";
        if (this.searchInputRef.el) this.searchInputRef.el.blur();
        this.loadMessages(this.state.currentFolder, 0);
    }

    onSearchInput(ev) {
        this.state.searchQuery = ev.target.value;
        // La requête part au serveur : on attend une pause de frappe plutôt
        // que d'en lancer une par caractère.
        if (this._searchTimer) clearTimeout(this._searchTimer);
        this._searchTimer = setTimeout(() => {
            this.loadMessages(this.state.currentFolder, 0);
        }, 300);
    }

    onEscape() {
        if (this.state.columnsOpen) {
            this.state.columnsOpen = false;
        } else if (this.state.settingsOpen) {
            this.closeSettings();
        } else if (this.selectedCount > 0) {
            this.clearSelection();
        } else {
            this.clearSearch();
        }
    }

    // ------------------------------------------------------------------
    // Sélection multiple
    // ------------------------------------------------------------------
    /**
     * Une case, ou toute une plage au shift+clic.
     *
     * ``preventDefault`` n'est pas décoratif : sans lui, le navigateur bascule
     * la case AVANT que le composant ne redessine. Quand l'état calculé se
     * trouve être celui d'avant (cocher une case déjà cochée dans la plage),
     * OWL ne repeint pas ce nœud — l'attribut n'a pas bougé — et la case
     * reste décochée à l'écran tout en comptant dans la sélection. On laisse
     * donc l'état être la seule source de vérité de la case.
     */
    toggleSelection(id, ev) {
        if (ev) {
            ev.stopPropagation();
            ev.preventDefault();
        }
        const ids = this.visibleMessages.map((m) => m.id);
        if (ev && ev.shiftKey && this._selectionAnchor !== null
                && this._selectionAnchor !== id
                && selectRange(this.state.selectedIds, ids,
                               this._selectionAnchor, id)) {
            // L'ancre suit la dernière extrémité : un second shift+clic
            // prolonge la plage au lieu de repartir du tout début.
            this._selectionAnchor = id;
            return;
        }
        if (this.state.selectedIds[id]) {
            delete this.state.selectedIds[id];
        } else {
            this.state.selectedIds[id] = true;
        }
        this._selectionAnchor = id;
    }

    clearSelection() {
        this.state.selectedIds = {};
        this._selectionAnchor = null;
    }

    /**
     * « Tout sélectionner » porte sur les lignes CHARGÉES, pas sur le dossier :
     * la liste se remplit au défilement, et prétendre sélectionner trois mille
     * courriels dont cent sont en mémoire serait un mensonge qui se paierait au
     * premier clic sur « Traité ». Le compteur de la barre d'actions le dit.
     */
    get allLoadedSelected() {
        const rows = this.visibleMessages;
        return rows.length > 0 && rows.every((m) => this.state.selectedIds[m.id]);
    }

    get someLoadedSelected() {
        return this.selectedCount > 0 && !this.allLoadedSelected;
    }

    toggleSelectAll() {
        if (this.allLoadedSelected) {
            this.clearSelection();
            return;
        }
        const next = {};
        for (const m of this.visibleMessages) {
            next[m.id] = true;
        }
        this.state.selectedIds = next;
        this._selectionAnchor = null;
    }

    get selectAllTitle() {
        if (this.allLoadedSelected) {
            return _t("Tout désélectionner");
        }
        return _t("Sélectionner les %s ligne(s) chargée(s)", this.visibleMessages.length);
    }

    /** Vrai quand le dossier contient plus que ce qui est chargé. */
    get hasMoreThanLoaded() {
        return this.state.total > this.state.messages.length;
    }

    get selectedCount() {
        return Object.keys(this.state.selectedIds).length;
    }

    get selectedIdList() {
        return Object.keys(this.state.selectedIds).map((k) => parseInt(k, 10));
    }

    /** Cibles de l'action : la sélection si elle existe, sinon l'aperçu. */
    get actionTargets() {
        if (this.selectedCount > 0) return this.selectedIdList;
        return this.state.selectedId ? [this.state.selectedId] : [];
    }

    // ------------------------------------------------------------------
    // Glisser-déposer sur un dossier
    // ------------------------------------------------------------------
    onRowDragStart(ev, id) {
        ev.dataTransfer.setData("application/x-bf-email-id", String(id));
        ev.dataTransfer.effectAllowed = "move";
        this.state.dragId = id;
    }

    onRowDragEnd() {
        this.state.dragId = null;
        this.state.dropTarget = null;
    }

    onFolderDragOver(ev, folderKey) {
        if (!(folderKey in DROP_ACTIONS) || folderKey === this.state.currentFolder) {
            return;
        }
        ev.preventDefault();
        ev.dataTransfer.dropEffect = "move";
        this.state.dropTarget = folderKey;
    }

    onFolderDragLeave(folderKey) {
        if (this.state.dropTarget === folderKey) {
            this.state.dropTarget = null;
        }
    }

    async onFolderDrop(ev, folderKey) {
        this.state.dropTarget = null;
        const action = DROP_ACTIONS[folderKey];
        if (!action) return;
        ev.preventDefault();
        const raw = ev.dataTransfer.getData("application/x-bf-email-id");
        const id = parseInt(raw, 10) || this.state.dragId;
        this.state.dragId = null;
        if (!id) return;
        // Un dépôt agit sur la ligne déposée, jamais sur la sélection : c'est
        // ce que le geste désigne.
        await this._dispatch(action, [id], { removeIds: [id] });
    }

    // ------------------------------------------------------------------
    // Actions
    // ------------------------------------------------------------------
    /**
     * Appelle ``inbox_run_action`` et exécute l'action Odoo qui en revient.
     * ``removeIds`` liste les lignes à retirer de la liste chargée une fois
     * l'appel réussi (Traité dans la boîte, Remettre en boîte dans Traités…).
     */
    async _dispatch(action, ids, opts = {}) {
        if (!ids || !ids.length) return null;
        // `optimistic` retire les lignes AVANT la réponse et ne
        // bloque pas l'action suivante ; un refus du serveur les remet à leur
        // place. Les autres gestes gardent l'attente d'avant.
        const optimiste = !!opts.optimistic;
        if (this.state.acting && !optimiste) return null;
        let retirees = null;
        let appel = null;
        if (optimiste) {
            retirees = this._detachRows(opts.removeIds || []);
            this._inflight = (this._inflight || 0) + 1;
            // Relecture adverse : l'annulation vise CE geste dès son envoi ;
            // posée à la réponse, un `z` tapé pendant le vol défaisait le
            // geste d'avant.
            const inverse = UNDO_INVERSE[action];
            if (inverse) {
                this._undoable = { action: inverse, ids: [...ids] };
            }
        } else {
            this.state.acting = true;
        }
        try {
            appel = this.orm.call(
                "bf.email", "inbox_run_action", [],
                { action, email_ids: ids }
            );
            if (optimiste) {
                this._enVol = (this._enVol || new Set());
                this._enVol.add(appel);
            }
            const result = await appel;
            // : ce qui se défait, et comment. Une seule action gardée,
            // la dernière : une pile profonde donnerait l'illusion qu'on peut
            // revenir loin, alors que le serveur, lui, a déjà bougé.
            const inverse = UNDO_INVERSE[action];
            if (inverse && !optimiste) {
                this._undoable = { action: inverse, ids: [...ids] };
            }
            if (!optimiste) {
                for (const id of opts.removeIds || []) {
                    this._removeAndJump(id);
                }
            }
            if (opts.clearSelection !== false) {
                this.clearSelection();
            }
            if (opts.notify) {
                this.notification.add(opts.notify, { type: "success" });
            }
            // Un seul rafraîchissement par action. Le tick du bus
            // suit dans la demi-seconde et relit déjà page et dossiers ; les
            // relire ici en plus faisait deux lectures pour un geste.
            this._refreshSoon();
            if (result) {
                this.action.doAction(result, {
                    onClose: () => this.refreshCurrent(),
                });
            }
            return result;
        } catch (err) {
            if (retirees) {
                this._reattachRows(retirees);
            }
            const texte = (err.data && err.data.message) || err.message || err;
            this.notification.add(
                (opts.errorPrefix || _t("Échec : ")) + texte,
                { type: "danger" }
            );
            return null;
        } finally {
            if (optimiste) {
                this._inflight -= 1;
                if (this._enVol && appel) {
                    this._enVol.delete(appel);
                }
            } else {
                this.state.acting = false;
            }
        }
    }

    /** Retire des lignes de la liste et rend de quoi les remettre. */
    _detachRows(ids) {
        // Les indices d'ORIGINE, relevés avant tout retrait : deux lignes
        // voisines reviennent dans leur ordre.
        const parties = [];
        for (const id of ids) {
            const i = this.state.messages.findIndex((m) => m.id === id);
            if (i >= 0) {
                parties.push({ index: i, message: this.state.messages[i] });
            }
        }
        for (const id of ids) {
            this._removeAndJump(id);
        }
        this._rememberPage();
        return { folder: this.state.currentFolder, parties };
    }

    _reattachRows(retirees) {
        // Seulement dans le dossier d'où elles sont parties.
        if (!retirees || retirees.folder !== this.state.currentFolder) {
            return;
        }
        for (const { index, message } of [...retirees.parties].sort((a, b) => a.index - b.index)) {
            if (this.state.messages.some((m) => m.id === message.id)) continue;
            this.state.messages.splice(Math.min(index, this.state.messages.length), 0, message);
            this.state.total += 1;
        }
        this._rememberPage();
    }

    /** La page affichée devient la dernière vue de ce dossier. */
    _rememberPage() {
        if (this.state.offset !== 0 || this.state.searchQuery || this.state.contact
                || this.isHeldFolder) {
            return;
        }
        dejaVu().pages.set(this._pageKey(this.state.currentFolder), {
            messages: [...toRaw(this.state.messages)],
            total: this.state.total,
        });
    }

    /** Action sur la cible courante (sélection, sinon aperçu). */
    async runAction(action) {
        // Un envoi programmé n'est pas une ligne bf.email : ses identifiants
        // appartiennent à un autre modèle et les passer à `inbox_run_action`
        // agirait sur le courriel qui porte le même numéro par hasard.
        if (this.isDraftFolder) {
            this.notification.add(
                _t("Cette action ne s'applique pas à un brouillon."),
                { type: "warning" }
            );
            return;
        }
        if (this.isHeldFolder) return;
        const ids = this.actionTargets;
        if (!ids.length) {
            this.notification.add(
                _t("Sélectionne d'abord un courriel."), { type: "warning" }
            );
            return;
        }
        // Répondre / transférer / activité ne valent que pour une ligne :
        // on prend l'aperçu quand plusieurs cases sont cochées.
        const single = ["reply", "reply_all", "forward", "activity",
                        "open_record", "open_form", "conversation",
                        "download_eml"];
        const targets = single.includes(action) && ids.length > 1
            ? [this.state.selectedId || ids[0]]
            : ids;
        await this._dispatch(action, targets, { clearSelection: false });
    }

    /**
     * « Pourriel » : ouvre la fenêtre « Signaler ». Rien n'est fait
     * avant sa confirmation, d'où l'annulation posée à la fermeture.
     */
    async reportSpam() {
        if (this.isDraftFolder || this.isHeldFolder || this.state.acting) return;
        const ids = this.actionTargets;
        if (!ids.length) {
            this.notification.add(
                _t("Sélectionne d'abord un courriel."), { type: "warning" });
            return;
        }
        let action;
        try {
            action = await this.orm.call(
                "bf.email", "inbox_run_action", [], { action: "spam", email_ids: ids });
        } catch (err) {
            // Le refus d'une ligne classée est une UserError : son texte est
            // dans `data.message`, `message` ne dit que « Odoo Server Error ».
            const texte = (err.data && err.data.message) || err.message || err;
            this.notification.add(_t("Échec : ") + texte, { type: "danger" });
            return;
        }
        if (!action) return;
        await this.action.doAction(action, {
            onClose: (infos) => this._afterReport(infos),
        });
    }

    _afterReport(infos) {
        const signales = (infos && infos.bf_email_reported) || [];
        if (!signales.length) {
            return;
        }
        this._undoable = { action: UNDO_INVERSE.spam, ids: [...signales] };
        for (const id of signales) {
            this._removeAndJump(id);
        }
        this.clearSelection();
        if (infos.message) {
            this.notification.add(infos.message, { type: "success" });
        }
        this._refreshSoon();
    }

    async markHandled() {
        if (this.isDraftFolder) return;
        // Le bouton ne se désactive plus pendant le vol ; un
        // double-clic traiterait le courriel suivant, devenu l'aperçu.
        const maintenant = Date.now();
        if (maintenant - (this._dernierTraite || 0) < 350) return;
        this._dernierTraite = maintenant;
        const ids = this.actionTargets;
        if (!ids.length) return;
        // Sortir de la boîte ne retire la ligne de la liste que dans les
        // dossiers d'où le traitement la fait disparaître.
        // « Traité » vaut aussi « Pas de relance », la ligne quitte
        // donc « Relance à faire » comme la boîte.
        const removes = ["inbox", "unread", "to_reply", "unrouted", "awaiting"]
            .includes(this.state.currentFolder) ? ids : [];
        await this._dispatch("handle", ids, {
            // La ligne part tout de suite, l'écriture IMAP suit au
            // serveur ; une autre touche peut partir pendant ce temps.
            optimistic: removes.length > 0,
            removeIds: removes,
            notify: ids.length > 1
                ? _t("%s courriels traités.", ids.length)
                : _t("Courriel traité."),
            errorPrefix: _t("Échec « Traité » : "),
        });
        if (!removes.length) await this.refreshCurrent();
    }

    /**
     * « Pas de relance » : le fil sort de « Relance à faire » sans
     * sortir de la boîte. Vaut pour le message attendu : écrire de nouveau
     * dans le fil peut le ramener.
     */
    async dismissAwaiting() {
        if (this.isDraftFolder) return;
        const ids = this.actionTargets;
        if (!ids.length) return;
        const removes = this.state.currentFolder === "awaiting" ? ids : [];
        // `_dispatch` rend null sur un échec ou un refus : rien n'a été écrit,
        // l'aperçu ne doit pas faire comme si.
        const resultat = await this._dispatch("no_followup", ids, {
            removeIds: removes,
            notify: ids.length > 1
                ? _t("%s fils sortis de « Relance à faire ».", ids.length)
                : _t("Pas de relance pour ce fil."),
            errorPrefix: _t("Échec « Pas de relance » : "),
        });
        if (resultat === null) return;
        if (this.state.preview && ids.includes(this.state.preview.id)) {
            this.state.preview.is_awaiting_reply = false;
        }
        if (!removes.length) await this.refreshCurrent();
    }

    async markUnhandled() {
        if (this.isDraftFolder) return;
        const ids = this.actionTargets;
        if (!ids.length) return;
        const removes = ["handled", "snoozed"].includes(this.state.currentFolder)
            ? ids : [];
        await this._dispatch("unhandle", ids, {
            removeIds: removes,
            notify: _t("Remis en boîte de réception."),
            errorPrefix: _t("Échec « Remettre en boîte » : "),
        });
        if (!removes.length) await this.refreshCurrent();
    }

    async markHandledForRow(id, ev) {
        if (ev) ev.stopPropagation();
        if (this.isDraftFolder) return;
        const removes = ["inbox", "unread", "to_reply", "unrouted"]
            .includes(this.state.currentFolder) ? [id] : [];
        await this._dispatch("handle", [id], {
            removeIds: removes,
            clearSelection: false,
            errorPrefix: _t("Échec « Traité » : "),
        });
        if (!removes.length) await this.refreshCurrent();
    }

    async openSourceRecord() {
        const preview = this.state.preview;
        if (!preview || !preview.res_model || !preview.res_id) {
            this.notification.add(
                _t("Ce courriel n'est rattaché à aucun dossier."),
                { type: "warning" }
            );
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: preview.res_model,
            res_id: preview.res_id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    async quickReroute(targetModel) {
        if (this.isDraftFolder) return;
        const ids = this.actionTargets;
        if (!ids.length) {
            this.notification.add(
                _t("Sélectionne au moins un courriel à router."),
                { type: "warning" }
            );
            return;
        }
        if (this.state.acting) return;
        this.state.acting = true;
        try {
            const context = { default_bf_email_ids: [[6, 0, ids]] };
            if (targetModel) {
                context.default_target_model_hint = targetModel;
            }
            this.clearSelection();
            await this.action.doAction({
                type: "ir.actions.act_window",
                name: _t("Importer dans un chatter"),
                res_model: "bf.email.reroute",
                view_mode: "form",
                views: [[false, "form"]],
                target: "new",
                context,
            }, { onClose: () => this.refreshCurrent() });
        } catch (err) {
            this.notification.add(
                _t("Routage impossible : ") + (err.message || err),
                { type: "danger" }
            );
        } finally {
            this.state.acting = false;
        }
    }

    /**
     * Retire la ligne de la liste chargée et sélectionne celle qui prend sa
     * place — ou la précédente si c'était la dernière.
     */
    _removeAndJump(id) {
        if (this.state.selectedIds[id]) {
            delete this.state.selectedIds[id];
        }
        const i = this.state.messages.findIndex((m) => m.id === id);
        if (i < 0) return;
        this.state.messages.splice(i, 1);
        this.state.total = Math.max(0, this.state.total - 1);
        if (this.state.selectedId !== id) return;
        const next = this.state.messages[i] || this.state.messages[i - 1];
        this.state.selectedId = null;
        this.state.preview = null;
        if (next) this.selectMessage(next.id);
    }

    async refreshCurrent() {
        if (this.isHeldFolder) {
            await this.loadHeld();
            await this.loadFolders();
            return;
        }
        const keepId = this.state.selectedId;
        await this.loadMessages(this.state.currentFolder, 0);
        await this.loadFolders();
        if (keepId && this.state.messages.some((m) => m.id === keepId)) {
            this.selectMessage(keepId);
        }
    }

    /** Recompte les dossiers sans bloquer l'interface. */
    _refreshFolders() {
        this.loadFolders();
    }

    // ------------------------------------------------------------------
    // Brouillons (envois programmés)
    // ------------------------------------------------------------------
    /**
     * Envoyer / modifier / ouvrir la fiche / annuler un envoi programmé.
     * ``cancel`` demande confirmation : c'est la seule action de cet écran
     * qui détruit quelque chose qu'on ne peut pas reconstituer.
     */
    async runDraftAction(action) {
        if (!this.isDraftFolder || this.state.acting) return;
        const ids = this.actionTargets;
        if (!ids.length) {
            this.notification.add(
                _t("Sélectionne d'abord un brouillon."), { type: "warning" }
            );
            return;
        }
        const single = ["edit", "open_record"];
        const targets = single.includes(action) && ids.length > 1
            ? [this.state.selectedId || ids[0]]
            : ids;
        if (action === "cancel") {
            const msg = _t("Annuler définitivement %s envoi(s) programmé(s) ?", targets.length);
            if (!window.confirm(msg)) {
                return;
            }
        }
        this.state.acting = true;
        try {
            const result = await this.orm.call(
                "bf.email", "inbox_draft_action", [],
                { action, draft_ids: targets }
            );
            if (result && result.notification) {
                this.notification.add(result.notification.message || "", {
                    title: result.notification.title,
                    type: result.notification.type || "success",
                });
                this.clearSelection();
                await this.refreshCurrent();
                return;
            }
            if (result) {
                this.action.doAction(result, {
                    onClose: () => this.refreshCurrent(),
                });
            }
        } catch (err) {
            this.notification.add(
                _t("Action impossible : ") + (err.message || err),
                { type: "danger" }
            );
        } finally {
            this.state.acting = false;
        }
    }

    // ------------------------------------------------------------------
    // « Ajouter » — créer une fiche à partir du courriel
    // ------------------------------------------------------------------
    /**
     * Même menu que « Nouveau ▾ » de la fiche complète du courriel : la fiche
     * est créée et le courriel importé dans son chatter. Une seule ligne à la
     * fois — créer six tâches d'un coup n'est pas un geste qu'on fait par
     * accident, c'en est un qu'on regrette.
     */
    async createRecord(action) {
        if (this.isDraftFolder) return;
        const id = this.state.selectedId;
        if (!id) {
            this.notification.add(
                _t("Ouvre d'abord un courriel."), { type: "warning" }
            );
            return;
        }
        await this._dispatch(action, [id], { clearSelection: false });
    }

    /** Le menu n'offre que ce qui est installé sur cette base. */
    get canCreate() {
        const p = this.state.preview || {};
        return {
            lead: Boolean(p.has_crm),
            ticket: Boolean(p.has_helpdesk),
            expense: Boolean(p.has_expense),
        };
    }

    // ------------------------------------------------------------------
    // Router / Re-router
    // ------------------------------------------------------------------
    /**
     * « Router… » quand le courriel n'est classé nulle part, « Re-router… »
     * quand il l'est déjà. Le geste diffère : le premier ajoute le courriel à
     * un chatter, le second le DÉPLACE et le retire de là où il était. Un seul
     * libellé pour les deux laissait croire au second qu'il faisait le
     * premier.
     */
    get isRouted() {
        const ids = this.actionTargets;
        if (!ids.length) return false;
        if (this.selectedCount > 0) {
            const rows = this.state.messages.filter(
                (m) => this.state.selectedIds[m.id]
            );
            return rows.length > 0 && rows.every((m) => m.res_model);
        }
        return Boolean(this.state.preview && this.state.preview.res_model);
    }

    get rerouteLabel() {
        return this.isRouted ? _t("Re-router…") : _t("Router…");
    }

    get rerouteTitle() {
        if (!this.isRouted) {
            return _t("Classer ce courriel dans une tâche, un ticket, un contact… (raccourci Y)");
        }
        const current = this.state.preview && this.state.preview.record_name;
        return current
            ? _t("Déplacer ce courriel hors de « %s » vers un autre dossier (raccourci Y)", current)
            : _t("Déplacer ce courriel vers un autre dossier (raccourci Y)");
    }

    // ------------------------------------------------------------------
    // Colonnes de la liste
    // ------------------------------------------------------------------
    /**
     * Colonnes offertes au sélecteur. « Sujet » n'y est pas : c'est la seule
     * qu'on ne peut pas retirer — une liste de courriels sans objet n'est plus
     * une liste de courriels, et permettre de tout décocher fabriquerait un
     * écran vide dont on ne saurait plus sortir.
     *
     * Construit dans un getter et non en constante de module : `_t` traduit à
     * l'appel, et au chargement du fichier les traductions ne sont pas encore
     * en place.
     */
    get columnDefs() {
        return [
            { key: "date", label: _t("Date") },
            { key: "correspondent", label: _t("Correspondant") },
            { key: "folder", label: _t("Dossier") },
            { key: "category", label: _t("Catégorie") },
            { key: "preview", label: _t("Extrait") },
            { key: "state", label: _t("État") },
        ];
    }

    get cols() {
        return this.state.settings.columnsInbox;
    }

    /** Sert au `colspan` de la ligne « rien ici » : deux gouttières + le sujet. */
    get visibleColumnCount() {
        return this.columnDefs.filter((c) => this.cols[c.key]).length + 3;
    }

    /** Idem pour les brouillons, qui n'ont ni catégorie ni extrait. */
    get visibleDraftColumnCount() {
        const shown = ["date", "correspondent", "folder", "state"]
            .filter((k) => this.cols[k]).length;
        return shown + 3;
    }

    toggleColumnsMenu() {
        this.state.columnsOpen = !this.state.columnsOpen;
        if (this.state.columnsOpen) this.state.settingsOpen = false;
    }

    toggleColumn(key) {
        const next = {
            ...this.state.settings,
            columnsInbox: { ...this.cols, [key]: !this.cols[key] },
        };
        this.state.settings = next;
        persistSettings(next);
    }

    /**
     * Libellé lisible d'une catégorie. Les libellés traduits sont déjà calculés
     * par `inbox_get_folders` pour l'arbre de gauche (clés `category:<valeur>`) :
     * les relire ici évite un aller-retour serveur par ligne, et garantit que
     * la colonne et le dossier disent le même mot.
     */
    categoryLabel(value) {
        if (!value) return "";
        const folder = this.state.folders.find((f) => f.key === `category:${value}`);
        return folder ? folder.label : value;
    }

    // ------------------------------------------------------------------
    // Disposition liste / aperçu
    // ------------------------------------------------------------------
    get pane() {
        return paneStyles(this.state.settings);
    }

    get colWidths() {
        return columnWidths(this.state.settings);
    }

    setPaneLayout(layout) {
        if (this.state.settings.paneLayout === layout) return;
        const next = { ...this.state.settings, paneLayout: layout };
        this.state.settings = next;
        persistSettings(next);
    }

    onSplitterMouseDown(ev) {
        startPaneDrag(ev, this);
    }

    // ------------------------------------------------------------------
    // Ruban d'actions de l'aperçu
    // ------------------------------------------------------------------
    /**
     * Replié, le ruban devient une seule ligne d'icônes : sur un aperçu placé
     * sous la liste, les douze boutons libellés retombent sur deux ou trois
     * rangées et mangent une bonne part de la hauteur qui devrait servir à
     * lire le courriel. L'en-tête (objet, De, À, date, dossier, pièces
     * jointes) ne se replie pas — c'est le contexte du message, pas une
     * option — et aucune action ne disparaît : les infobulles et les
     * raccourcis clavier restent.
     */
    get ribbonCollapsed() {
        return !!this.state.settings.ribbonCollapsed;
    }

    get ribbonClass() {
        return this.ribbonCollapsed
            ? "mt-2 d-flex gap-1 align-items-center o_bf_email_ribbon o_bf_email_ribbon_compact"
            : "mt-2 d-flex flex-wrap gap-1 o_bf_email_ribbon";
    }

    get ribbonToggleTitle() {
        return this.ribbonCollapsed
            ? _t("Déplier le ruban d'actions")
            : _t("Replier le ruban d'actions en icônes");
    }

    toggleRibbon() {
        const next = {
            ...this.state.settings,
            ribbonCollapsed: !this.ribbonCollapsed,
        };
        this.state.settings = next;
        persistSettings(next);
    }


    // ------------------------------------------------------------------
    // Affichage
    // ------------------------------------------------------------------
    formatDate(iso) {
        return formatRelativeDate(iso, this.state.settings);
    }

    /** Cellule de liste : l'heure seule pour un courriel du jour. */
    formatListDate(iso) {
        return formatRelativeDate(iso, this.state.settings, { compact: true });
    }

    senderCell(m) {
        // Un avis automatique se lit comme un envoi : on montre à qui il est parti.
        const raw = m.direction === "in" ? m.from : m.to;
        return senderCell(m.correspondent, raw, this.state.settings);
    }

    get previewSrcdoc() {
        return buildPreviewSrcdoc(this.state.preview && this.state.preview.body_html);
    }

    attachmentUrl(att) {
        return `/web/content/${att.id}?download=true`;
    }

    // ------------------------------------------------------------------
    // Préférences (partagées avec le navigateur IMAP)
    // ------------------------------------------------------------------
    toggleSettings() {
        this.state.settingsOpen = !this.state.settingsOpen;
        // Les deux panneaux occupent le même coin : les laisser ouverts
        // ensemble en superpose un sur l'autre.
        if (this.state.settingsOpen) this.state.columnsOpen = false;
    }

    closeSettings() {
        this.state.settingsOpen = false;
    }

    onSettingChange(key, ev) {
        const value = ev.target.type === "checkbox"
            ? ev.target.checked
            : ev.target.value;
        const next = { ...this.state.settings, [key]: value };
        this.state.settings = next;
        persistSettings(next);
        if (key === "pageSize") {
            this.state.pageSize = parseInt(value, 10);
            this.loadMessages(this.state.currentFolder, 0);
        }
    }
}

registry.category("actions").add("bf_email_inbox", BfEmailInbox);

/**
 * Le bouton « Courriels » de la fiche contact.
 *
 * Une action-fonction, pas un composant : le gestionnaire d'actions ne
 * touche alors ni au fil d'Ariane ni à l'écran, et la fiche reste dessous.
 * Le panneau du systray (module `bf_email_systray`, qui dépend de celui-ci et
 * non l'inverse) répond à l'événement et marque `handled`. S'il n'est pas
 * installé, ou si la personne a choisi la pleine page, on y va, filtrée.
 */
export function openContactEmails(env, action) {
    const params = action.params || {};
    const contact = { id: params.contact_id, name: params.contact_name || "" };
    const detail = { contact, handled: false };
    env.bus.trigger("BF_EMAIL:OPEN_CONTACT", detail);
    if (detail.handled) {
        return;
    }
    return {
        type: "ir.actions.client",
        tag: "bf_email_inbox",
        name: _t("Courriels : %s", contact.name),
        params: { contact },
    };
}

registry.category("actions").add("bf_email_contact_emails", openContactEmails);

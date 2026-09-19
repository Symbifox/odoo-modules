/** @odoo-module **/
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { loadJS } from "@web/core/assets";
import { reactive } from "@odoo/owl";

// Service porteur de l'UA JsSIP. Il DOIT être un service (pas un composant) :
// l'enregistrement SIP doit survivre à la fermeture du panneau, sinon on cesse
// de recevoir les appels.
//
// JsSIP (~240 Ko) est chargé en PARESSEUX (loadJS) au premier démarrage, et
// seulement pour un membre du groupe : il n'alourdit pas le bundle de tout le monde.

export const softphoneService = {
    dependencies: ["orm", "notification"],
    start(env, { orm, notification }) {
        // État réactif partagé avec la barre système et le panneau.
        const state = reactive({
            available: false,   // membre du groupe + poste configuré
            status: "off",      // off | connecting | registered | failed
            inCall: false,
            callDir: null,      // incoming | outgoing
            peerName: "",       // afficheur résolu
            peerNumber: "",
            partnerId: false,
            muted: false,
            voicemail: 0,       // messages en attente (MWI)
            ringing: false,     // appel entrant non décroché
        });

        let JsSIP = null;
        let ua = null;
        let session = null;
        let iceServers = [];
        let ringCtx = null;
        let ringTimer = null;
        let callStartMs = 0;

        // ---------- signalement d'un appel entrant ----------
        // Le badge rouge du systray ne suffit pas : personne ne fixe la barre du
        // haut, et l'onglet est souvent en arrière-plan. On ajoute donc une
        // notification du navigateur et un titre qui clignote, tous deux
        // annulés dès que la sonnerie cesse.
        let liveNotification = null;
        let titleTimer = null;
        let originalTitle = null;

        function notifyIncoming(from) {
            try {
                if (window.Notification && Notification.permission === "granted") {
                    liveNotification = new Notification("Appel entrant", {
                        body: from ? "de " + from : "",
                        tag: "bf-softphone-incoming",
                        requireInteraction: true,
                    });
                    liveNotification.onclick = () => {
                        window.focus();
                        try { liveNotification.close(); } catch (e) { /* */ }
                    };
                }
            } catch (e) { /* notifications refusées : on garde le reste */ }
            try {
                if (originalTitle === null) originalTitle = document.title;
                let on = false;
                titleTimer = setInterval(() => {
                    on = !on;
                    document.title = on ? "\u260E Appel entrant" : originalTitle;
                }, 900);
            } catch (e) { /* */ }
        }

        function stopNotifying() {
            if (titleTimer) { clearInterval(titleTimer); titleTimer = null; }
            if (originalTitle !== null) { document.title = originalTitle; originalTitle = null; }
            if (liveNotification) {
                try { liveNotification.close(); } catch (e) { /* */ }
                liveNotification = null;
            }
        }

        // ---------- sonnerie (Web Audio, aucun asset) ----------
        // ⚠️ Un navigateur REFUSE de jouer un son tant que la page n'a pas reçu
        // un geste de l'utilisateur. Or un appel entrant arrive justement sans
        // geste : créer le contexte à ce moment-là donne un contexte SUSPENDU et
        // une sonnerie muette. On le débloque donc au PREMIER clic ou frappe
        // dans la page, une seule fois, puis on le garde vivant pour la suite.
        function unlockAudio() {
            try {
                if (!ringCtx) {
                    ringCtx = new (window.AudioContext || window.webkitAudioContext)();
                }
                if (ringCtx.state === "suspended") {
                    ringCtx.resume().catch(() => { /* repli visuel */ });
                }
            } catch (e) { /* pas d'audio : la bulle rouge qui secoue reste */ }
        }
        ["pointerdown", "keydown"].forEach((evt) =>
            document.addEventListener(evt, unlockAudio, { once: true, capture: true }));

        function ring(on) {
            try {
                if (on) {
                    if (ringTimer) return;
                    unlockAudio();
                    if (!ringCtx) return;
                    const beat = () => {
                        if (!ringCtx || ringCtx.state !== "running") return;
                        [0, 0.4].forEach((off) => {
                            const o = ringCtx.createOscillator();
                            const g = ringCtx.createGain();
                            o.frequency.value = off ? 523 : 440;
                            o.connect(g); g.connect(ringCtx.destination);
                            const t = ringCtx.currentTime + off;
                            g.gain.setValueAtTime(0.0001, t);
                            g.gain.exponentialRampToValueAtTime(0.22, t + 0.03);
                            g.gain.exponentialRampToValueAtTime(0.0001, t + 0.35);
                            o.start(t); o.stop(t + 0.36);
                        });
                        ringTimer = setTimeout(beat, 1600);
                    };
                    beat();
                } else {
                    stopNotifying();
                    if (ringTimer) clearTimeout(ringTimer);
                    ringTimer = null;
                    // ⚠️ On NE FERME PAS le contexte : le rouvrir hors d'un geste
                    // le ramènerait suspendu, et le 2e appel serait muet.
                    // Il ne coûte rien au repos.
                }
            } catch (e) { /* audio refusé : repli visuel via state.ringing */ }
        }

        // ---------- élément audio distant (créé une fois) ----------
        let remoteAudio = document.getElementById("bf_softphone_remote");
        if (!remoteAudio) {
            remoteAudio = document.createElement("audio");
            remoteAudio.id = "bf_softphone_remote";
            remoteAudio.autoplay = true;
            document.body.appendChild(remoteAudio);
        }

        const pcConfig = () => ({
            iceServers,
            iceTransportPolicy: "relay",
            bundlePolicy: "max-bundle",
            rtcpMuxPolicy: "require",
        });
        const media = { mediaConstraints: { audio: true, video: false } };

        // fastOffer : envoyer l'INVITE dès le 1er candidat relais, sinon JsSIP
        // attend la fin complète du rassemblement ICE (~20 s en relay-only).
        function fastOffer(s) {
            let sent = false, timer = null;
            const go = (ready) => {
                if (sent) return; sent = true;
                if (timer) clearTimeout(timer); timer = null;
                try { ready(); } catch (e) { /* */ }
            };
            s.on("icecandidate", ({ candidate, ready }) => {
                if (!timer) timer = setTimeout(() => go(ready), 4000);
                if (candidate && candidate.type === "relay") go(ready);
            });
        }

        function bindRemote() {
            if (session && session.connection) {
                session.connection.ontrack = (e) => {
                    remoteAudio.srcObject = e.streams[0];
                };
            }
        }

        async function resolveCaller(number) {
            try {
                const r = await orm.call("res.users", "softphone_resolve_caller", [number]);
                return r || { name: "", partner_id: false };
            } catch (e) {
                return { name: "", partner_id: false };
            }
        }

        function logCall(number, callType, durationSec) {
            orm.call("res.users", "softphone_log_call",
                [number, callType, durationSec]).catch(() => { /* non bloquant */ });
        }

        function wire(s, dir) {
            session = s;
            bindRemote();
            fastOffer(s);
            s.on("peerconnection", bindRemote);
            s.on("accepted", () => { bindRemote(); onEstablished(dir); });
            s.on("confirmed", () => { bindRemote(); onEstablished(dir); });
            s.on("ended", () => onEnded(dir));
            s.on("failed", (e) => onEnded(dir, true, e));
        }

        function onEstablished(dir) {
            ring(false);
            state.ringing = false;
            state.inCall = true;
            state.callDir = dir;
            if (!callStartMs) callStartMs = Date.now();
        }

        function onEnded(dir, failed, ev) {
            ring(false);
            const dur = callStartMs ? Math.round((Date.now() - callStartMs) / 1000) : 0;
            const number = state.peerNumber;
            // journalise : entrant décroché / sortant / manqué
            let ct = dir === "incoming" ? "incoming" : "outgoing";
            if (dir === "incoming" && !callStartMs) ct = "missed";
            if (number) logCall(number, ct, dur);
            // ⚠️ Un appel refusé par le PBX repartait EN SILENCE : le panneau
            // revenait au repos sans un mot, ce qui se lit comme « le téléphone
            // ne compose pas ». On dit toujours pourquoi, sauf si c'est nous qui
            // avons raccroché avant que ça décroche.
            const cause = (ev && ev.cause) || "";
            if (failed && dir === "outgoing" && cause !== "Canceled") {
                notification.add(
                    "Appel vers " + number + " non abouti" +
                    (cause ? " (" + cause + ")" : "") + ".",
                    { type: "warning" });
            }
            callStartMs = 0;
            session = null;
            state.inCall = false;
            state.ringing = false;
            state.callDir = null;
            state.peerName = "";
            state.peerNumber = "";
            state.partnerId = false;
            state.muted = false;
        }

        // ---------- UA ----------
        function buildUA(cfg) {
            const sock = new JsSIP.WebSocketInterface(cfg.ws_uri);
            ua = new JsSIP.UA({
                sockets: [sock],
                uri: cfg.sip_uri,
                password: cfg.password,
                register: true,
                register_expires: 120,
                session_timers: false,
                connection_recovery_min_interval: 2,
                connection_recovery_max_interval: 15,
            });
            ua.on("connecting", () => { state.status = "connecting"; });
            ua.on("connected", () => { state.status = "connecting"; });
            ua.on("disconnected", () => { state.status = "connecting"; });
            ua.on("registered", () => { state.status = "registered"; });
            ua.on("unregistered", () => { state.status = "connecting"; });
            ua.on("registrationFailed", (e) => {
                state.status = "failed";
                notification.add("Échec de connexion du téléphone : " +
                    ((e && e.cause) || "authentification"), { type: "danger" });
            });
            // MWI (badge messagerie vocale) — NON câblé en v1 : JsSIP 3.11.1 n'implémente
            // pas message-summary et répond 481 aux NOTIFY non sollicités. Un vrai badge
            // exigerait un abonnement SUBSCRIBE/NOTIFY (absent de JsSIP) ou un compteur
            // servi par Odoo. Reporté. state.voicemail reste 0 → le badge ne s'affiche pas.
            ua.on("newRTCSession", (e) => {
                if (session) { try { e.session.terminate(); } catch (x) { /* */ } return; }
                if (e.originator === "remote") {
                    const from = (e.request.from.uri && e.request.from.uri.user) ||
                        e.request.from.display_name || "";
                    state.peerNumber = from;
                    state.callDir = "incoming";
                    state.ringing = true;
                    ring(true);
                    notifyIncoming(from);
                    resolveCaller(from).then((r) => {
                        state.peerName = r.name || "";
                        state.partnerId = r.partner_id || false;
                    });
                    wire(e.session, "incoming");
                } else {
                    wire(e.session, "outgoing");
                }
            });
            ua.start();
        }

        // ---------- API publique du service ----------
        async function init() {
            if (ua) return true;
            state.available = await user.hasGroup("bf_softphone.group_softphone_user");
            if (!state.available) return false;
            let cfg;
            try {
                cfg = await orm.call("res.users", "get_softphone_config", []);
            } catch (e) {
                state.available = false;
                return false;
            }
            if (!cfg || !cfg.ws_uri) { state.available = false; return false; }
            iceServers = cfg.ice_servers || [];
            // chargement paresseux de JsSIP — première fois seulement
            if (!JsSIP) {
                await loadJS("/bf_softphone/static/src/lib/jssip.min.js");
                JsSIP = window.JsSIP;
            }
            state.status = "connecting";
            buildUA(cfg);
            return true;
        }

        // ⚠️ Le plan de numérotation du PBX ne connaît PAS le « + » : ses motifs
        // sont _1NXXNXXXXXX et _NXXNXXXXXX. Or les fiches contact, le journal
        // d'appels et les liens tel: portent tous de l'E.164 (« +1 514-555-0123 »).
        // Sans cette normalisation, tout ce qui ne venait pas du clavier partait
        // en INVITE vers « +1… » et Asterisk répondait 404 « extension not found
        // in context from-internal » — donc le téléphone ne composait rien.
        // Même règle que côté serveur (res_users._softphone_nanpa + lstrip("+")).
        function dialTarget(raw) {
            // Un numéro noté « 418-555-0100 Ext:121 », « (450) 555-0188 # 220 »
            // ou « 514-555-0143 (250) » ne se compose pas d'un bloc : sans cette
            // coupure, les chiffres du poste interne se collaient au numéro.
            // ⚠️ On ne coupe QUE si le début porte déjà un numéro complet, pour
            // ne jamais amputer un code court (*97, #, 1001).
            let cut = String(raw || "");
            const ext = cut.match(
                /^(.*?)(?:\s*(?:ext|extension|poste|x|#)[.:-]?\s*\d+|\s*\(\d+\))\s*$/i);
            if (ext && ext[1].replace(/\D/g, "").length >= 10) cut = ext[1];
            const s = cut.replace(/[^0-9*#+]/g, "");
            if (!s.startsWith("+")) return s;   // postes, codes *, 10 ou 11 chiffres
            const d = s.slice(1).replace(/\D/g, "");
            // Hors NANPA, l'international est bloqué en amont (par le trunk) :
            // autant le dire ici plutôt que de laisser le PBX refuser en silence.
            return /^1[2-9]\d{2}[2-9]\d{6}$/.test(d) ? d : "";
        }

        function callNumber(raw) {
            if (!ua) return;
            const n = dialTarget(raw);
            if (!n) {
                notification.add(
                    "Numéro non composable : seuls les numéros nord-américains " +
                    "le sont (« " + String(raw || "").trim() + " »).",
                    { type: "warning" });
                return;
            }
            state.peerNumber = n;
            state.callDir = "outgoing";
            resolveCaller(n).then((r) => {
                state.peerName = r.name || "";
                state.partnerId = r.partner_id || false;
            });
            const domain = ua.configuration.uri.host;
            ua.call("sip:" + n + "@" + domain, Object.assign({ pcConfig: pcConfig() }, media));
        }

        function answer() {
            if (session) session.answer(Object.assign({ pcConfig: pcConfig() }, media));
        }
        function hangup() { if (session) session.terminate(); }
        function toggleMute() {
            if (!session) return;
            if (session.isMuted().audio) { session.unmute({ audio: true }); state.muted = false; }
            else { session.mute({ audio: true }); state.muted = true; }
        }
        function sendDTMF(k) { if (session) { try { session.sendDTMF(k); } catch (e) { /* */ } } }

        return {
            state,
            init,
            callNumber,
            answer,
            hangup,
            toggleMute,
            sendDTMF,
            searchContacts: (term) =>
                orm.call("res.users", "softphone_search_contacts", [term]),
        };
    },
};

registry.category("services").add("bf_softphone", softphoneService);

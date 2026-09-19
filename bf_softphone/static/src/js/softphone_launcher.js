/** @odoo-module **/
import { Component, useState, useEffect, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";
import { SoftphonePanel } from "./softphone_panel";

/**
 * Bulle flottante en bas à droite, comme un bouton d'aide de site web.
 *
 * ⚠️ Volontairement dans `main_components` et NON dans le systray. Deux raisons :
 * un téléphone se prend en main, il ne se cherche pas dans une barre d'icônes ;
 * et surtout, la barre de navigation impose ses couleurs à tout ce qui vit
 * dedans (`bluefox_branding` y force `a, button, span { color:#fff !important }`),
 * ce qui repeignait le panneau en blanc sur blanc. À la racine du client web, le
 * panneau n'hérite plus de rien.
 */
class SoftphoneLauncher extends Component {
    static template = "bf_softphone.Launcher";
    static components = { SoftphonePanel };
    static props = [];

    setup() {
        this.phone = useService("bf_softphone");
        // ⚠️ useState(...) est OBLIGATOIRE ici. `phone.state` est un objet
        // `reactive()` du service : le lire directement donne les bonnes valeurs
        // au PREMIER rendu, mais n'ABONNE PAS le composant. Sans abonnement, le
        // passage « ça sonne » → « en communication » ne redessine rien : le
        // panneau restait sur l'écran d'appel entrant et le bouton Raccrocher
        // n'apparaissait jamais (signalé en ces termes : « je ne peux pas
        // raccrocher »). Les rendus qu'on observait venaient par accident d'un
        // AUTRE état réactif (this.ui / this.local) qui, lui, était abonné.
        this.state = useState(this.phone.state);
        this.ui = useState({ visible: false, open: false });

        onWillStart(async () => {
            this.ui.visible = await user.hasGroup("bf_softphone.group_softphone_user");
            if (this.ui.visible) {
                // Démarre l'enregistrement SIP dès le chargement du backend, pour
                // recevoir les appels même panneau fermé.
                await this.phone.init();
                // Demandée une seule fois, à froid : au moment où le téléphone
                // sonne, il est trop tard pour quémander une permission.
                try {
                    if (window.Notification && Notification.permission === "default") {
                        Notification.requestPermission();
                    }
                } catch (e) { /* navigateur sans notifications : tant pis */ }
            }
        });

        // ⚠️ Un appel entrant OUVRE le panneau. Sans ça, il n'existait qu'un
        // badge rouge de la taille d'un pois dans la barre du haut : le
        // téléphone sonnait pour de vrai, et personne ne le voyait — deux
        // appels laissés sonner trente secondes avant qu'on s'en aperçoive.
        useEffect(
            (ringing) => {
                if (ringing) this.ui.open = true;
            },
            () => [this.state.ringing],
        );
    }

    togglePanel() {
        this.ui.open = !this.ui.open;
    }

    /** Classe de la bulle : elle change d'allure quand ça sonne. */
    get bubbleClass() {
        if (this.state.ringing) return "o_bf_sp_bubble--ringing";
        if (this.state.inCall) return "o_bf_sp_bubble--incall";
        return "";
    }

    get statusClass() {
        return {
            registered: "text-success",
            connecting: "text-warning",
            failed: "text-danger",
            off: "text-muted",
        }[this.state.status] || "text-muted";
    }
}

registry.category("main_components").add("BfSoftphone", {
    Component: SoftphoneLauncher,
});

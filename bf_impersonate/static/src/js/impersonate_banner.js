/** @odoo-module **/
// Le bandeau de l'incarnation : toujours visible, au bureau comme au
// téléphone, avec le temps qui reste et le retour à son compte. À zéro, il
// rend la main de lui-même ; le serveur l'aurait fait à la requête suivante.
import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { session } from "@web/session";
import { announceUser } from "./user_menu";

export class ImpersonateBanner extends Component {
    static template = "bf_impersonate.Banner";
    static props = {};

    setup() {
        this.info = session.bf_impersonate;
        // Sans délai connu, pas de compte à rebours : le serveur rendra la main
        // de lui-même à l'échéance. Ne jamais déduire « zéro » d'une absence.
        const seconds = Number(this.info.seconds_left);
        this.timed = Number.isFinite(seconds) && seconds > 0;
        this.deadline = Date.now() + (this.timed ? seconds : 0) * 1000;
        this.state = useState({ left: this.timed ? seconds : 0, busy: false });
        onMounted(() => {
            document.body.classList.add("o_bf_impersonating");
            if (this.timed) {
                this.timer = browser.setInterval(() => this.tick(), 1000);
            }
        });
        onWillUnmount(() => {
            browser.clearInterval(this.timer);
            document.body.classList.remove("o_bf_impersonating");
        });
    }

    tick() {
        this.state.left = Math.max(0, Math.round((this.deadline - Date.now()) / 1000));
        if (this.state.left === 0) {
            this.back();
        }
    }

    get headline() {
        return _t("You are seeing Symbifox as %s", this.info.target_name);
    }

    get modeLabel() {
        return this.info.mode === "write" ? _t("read and write") : _t("read only");
    }

    get remaining() {
        if (!this.timed) {
            return "";
        }
        const minutes = Math.floor(this.state.left / 60);
        const seconds = String(this.state.left % 60).padStart(2, "0");
        return `${minutes}:${seconds}`;
    }

    async back() {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        browser.clearInterval(this.timer);
        try {
            await rpc("/bf_impersonate/stop", {});
        } finally {
            announceUser(this.info.from_uid);
            browser.location.href = "/odoo";
        }
    }
}

// Le client web d'une iframe (aperçu du site, par exemple) n'a ni bandeau à
// montrer, ni session à rendre : seule la fenêtre principale s'en charge.
if (session.bf_impersonate && window.top === window) {
    registry.category("main_components").add("bf_impersonate.Banner", {
        Component: ImpersonateBanner,
    });
}

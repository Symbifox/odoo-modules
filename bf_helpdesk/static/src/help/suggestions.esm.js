/* Suggestions d'articles d'aide pendant la saisie du sujet d'une demande.
 * Le DOM se monte avec textContent : un titre d'article n'est jamais
 * interprété comme du HTML. */
import publicWidget from "@web/legacy/js/public/public_widget";

const MIN_CHARS = 4;
const DELAY_MS = 350;

publicWidget.registry.BfHelpSuggestions = publicWidget.Widget.extend({
    selector: "input[data-bf-help-suggest]",
    events: {input: "_onInput"},

    start() {
        this._timer = null;
        this._last = "";
        this._box = document.getElementById("bf_help_suggestions");
        return this._super(...arguments);
    },

    _onInput() {
        clearTimeout(this._timer);
        this._timer = setTimeout(() => this._fetch(), DELAY_MS);
    },

    async _fetch() {
        const q = this.el.value.trim();
        if (q === this._last) {
            return;
        }
        this._last = q;
        if (!this._box) {
            return;
        }
        if (q.length < MIN_CHARS) {
            this._box.replaceChildren();
            return;
        }
        const params = new URLSearchParams({q});
        if (this.el.dataset.bfHelpTeam) {
            params.set("team", this.el.dataset.bfHelpTeam);
        }
        let items = [];
        try {
            const resp = await fetch(`/aide/suggestions?${params}`, {
                headers: {Accept: "application/json"},
            });
            items = resp.ok ? await resp.json() : [];
        } catch {
            items = [];
        }
        if (q !== this._last) {
            return;
        }
        this._render(items);
    },

    _render(items) {
        this._box.replaceChildren();
        if (!items.length) {
            return;
        }
        const card = document.createElement("div");
        card.className = "border rounded p-3 bg-light";
        const title = document.createElement("p");
        title.className = "fw-bold mb-2";
        title.textContent = "Ces articles pourraient vous aider :";
        const list = document.createElement("ul");
        list.className = "mb-0 ps-3";
        for (const item of items) {
            const li = document.createElement("li");
            const link = document.createElement("a");
            link.href = item.url;
            link.target = "_blank";
            link.rel = "noopener";
            link.textContent = item.title;
            li.append(link);
            if (item.summary) {
                const small = document.createElement("div");
                small.className = "small text-muted";
                small.textContent = item.summary;
                li.append(small);
            }
            list.append(li);
        }
        card.append(title, list);
        this._box.append(card);
    },
});

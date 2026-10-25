/* Business-card scan page — client half.
 *
 * Deliberately dependency-free and framework-free: the page must boot on a
 * mid-range phone over trade-show LTE, and every byte here is served before
 * anything useful happens.
 *
 * The one non-obvious thing it does is downscale the photo before upload. A
 * modern phone camera hands over 4-12 MB; a business card stays perfectly
 * legible at 1600 px on its long edge, which lands around 300 KB. That is the
 * difference between a two-second upload and a thirty-second one, and the
 * model reads the smaller image just as well.
 */
(function () {
    "use strict";

    var MAX_EDGE = 1600;     // px on the long edge, enough for card typography
    var JPEG_QUALITY = 0.85;
    var TIMEOUT_MS = 150000; // the server side may take up to ~100 s

    var state = {wizardId: null, mode: "create", filename: "card.jpg"};

    function $(id) { return document.getElementById(id); }

    /* The script is static and cannot be translated: every sentence it shows
     * travels with the page, in the reader's language, as a <span data-k="…">
     * inside #texts. `%s` stands for a number the script puts in. */
    var FALLBACK = {
        session: "Your Odoo session has expired. Reload the page.",
        server: "The server answered %s.",
        unexpected: "Unexpected server error.",
        timeout: "Reading took too long. Try again.",
        unreadable: "The file could not be read.",
        notimage: "This file is not a readable image.",
        confidence: "Reading confidence: %s%",
        saving: "Saving…",
        updated: "Contact updated",
        created: "Contact created",
    };

    function T(key, n) {
        var node = document.querySelector('#texts [data-k="' + key + '"]');
        var text = node ? node.textContent.trim() : FALLBACK[key];
        return n === undefined ? text : text.replace("%s", n);
    }

    function show(stepId) {
        ["step-capture", "step-working", "step-review", "step-done"]
            .forEach(function (id) { $(id).classList.toggle("hidden", id !== stepId); });
        window.scrollTo(0, 0);
    }

    function fail(message) {
        var box = $("error");
        box.textContent = message;
        box.classList.remove("hidden");
    }

    function clearError() { $("error").classList.add("hidden"); }

    /* ── Server ─────────────────────────────────────────────────── */

    function call(route, params) {
        var controller = new AbortController();
        var timer = setTimeout(function () { controller.abort(); }, TIMEOUT_MS);
        return fetch(route, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({jsonrpc: "2.0", method: "call", params: params}),
            signal: controller.signal,
        }).then(function (response) {
            clearTimeout(timer);
            if (response.status === 401 || response.status === 403) {
                throw new Error(T("session"));
            }
            if (!response.ok) {
                throw new Error(T("server", response.status));
            }
            return response.json();
        }).then(function (payload) {
            // An Odoo JSON route reports a crash in `error`, and a handled
            // problem in `result.error`. Both must reach the same message box.
            if (payload.error) {
                var data = payload.error.data || {};
                throw new Error(data.message || payload.error.message ||
                                T("unexpected"));
            }
            var result = payload.result || {};
            if (result.error) { throw new Error(result.error); }
            return result;
        }, function (err) {
            clearTimeout(timer);
            if (err.name === "AbortError") {
                throw new Error(T("timeout"));
            }
            throw err;
        });
    }

    /* ── Image handling ─────────────────────────────────────────── */

    function toBase64(blob) {
        return new Promise(function (resolve, reject) {
            var reader = new FileReader();
            reader.onload = function () {
                // strip the "data:...;base64," prefix the server does not want
                resolve(String(reader.result).split(",")[1]);
            };
            reader.onerror = function () { reject(new Error(T("unreadable"))); };
            reader.readAsDataURL(blob);
        });
    }

    function downscale(file) {
        // A PDF goes through untouched: the server accepts it and a canvas
        // cannot decode it anyway.
        if (file.type === "application/pdf") {
            return toBase64(file);
        }
        return new Promise(function (resolve, reject) {
            var url = URL.createObjectURL(file);
            var img = new Image();
            img.onload = function () {
                URL.revokeObjectURL(url);
                var scale = Math.min(1, MAX_EDGE / Math.max(img.width, img.height));
                var canvas = document.createElement("canvas");
                canvas.width = Math.round(img.width * scale);
                canvas.height = Math.round(img.height * scale);
                var ctx = canvas.getContext("2d");
                ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                canvas.toBlob(function (blob) {
                    if (!blob) {
                        // Some browsers refuse toBlob on very large canvases;
                        // sending the original is slower but still correct.
                        toBase64(file).then(resolve, reject);
                        return;
                    }
                    toBase64(blob).then(resolve, reject);
                }, "image/jpeg", JPEG_QUALITY);
            };
            img.onerror = function () {
                URL.revokeObjectURL(url);
                reject(new Error(T("notimage")));
            };
            img.src = url;
        });
    }

    /* ── Flow ───────────────────────────────────────────────────── */

    function onFile(file) {
        if (!file) { return; }
        clearError();
        state.filename = file.name || "card.jpg";
        show("step-working");

        var previewUrl = file.type === "application/pdf" ? null : URL.createObjectURL(file);
        if (previewUrl) { $("preview").src = previewUrl; }

        downscale(file).then(function (b64) {
            return call("/scan/extract", {image_b64: b64, filename: state.filename});
        }).then(function (result) {
            if (previewUrl) { URL.revokeObjectURL(previewUrl); }
            fillReview(result);
            show("step-review");
        }).catch(function (err) {
            if (previewUrl) { URL.revokeObjectURL(previewUrl); }
            show("step-capture");
            fail(err.message);
        });
    }

    function fillReview(result) {
        state.wizardId = result.wizard_id;
        state.mode = result.mode || "create";

        var form = $("review-form");
        Object.keys(result.fields || {}).forEach(function (name) {
            var input = form.elements[name];
            if (input) { input.value = result.fields[name] || ""; }
        });

        var banner = $("match-banner");
        if (result.match) {
            $("match-name").textContent = result.match.name +
                (result.match.email ? " — " + result.match.email : "");
            banner.classList.remove("hidden");
            var radio = form.ownerDocument.querySelector(
                'input[name="mode"][value="' + state.mode + '"]');
            if (radio) { radio.checked = true; }
        } else {
            banner.classList.add("hidden");
        }

        var chip = $("confidence");
        var score = result.confidence || 0;
        chip.textContent = T("confidence", score);
        chip.className = "chip " + (score >= 75 ? "good" : score >= 50 ? "fair" : "poor");
    }

    function save() {
        clearError();
        var form = $("review-form");
        var fields = {};
        Array.prototype.forEach.call(form.elements, function (el) {
            if (el.name) { fields[el.name] = el.value.trim(); }
        });
        var picked = document.querySelector('input[name="mode"]:checked');
        var mode = picked ? picked.value : state.mode;

        var button = $("save");
        var label = button.textContent;
        button.disabled = true;
        button.textContent = T("saving");

        call("/scan/save", {wizard_id: state.wizardId, fields: fields, mode: mode})
            .then(function (result) {
                $("done-title").textContent = T(result.updated ? "updated" : "created");
                $("done-name").textContent = result.name || "";
                $("done-link").href = result.url;
                show("step-done");
            })
            .catch(function (err) { fail(err.message); })
            .finally(function () {
                button.disabled = false;
                button.textContent = label;
            });
    }

    function restart() {
        clearError();
        state.wizardId = null;
        $("review-form").reset();
        $("shot").value = "";
        $("pick").value = "";
        show("step-capture");
    }

    /* ── Wiring ─────────────────────────────────────────────────── */

    document.addEventListener("DOMContentLoaded", function () {
        $("shot").addEventListener("change", function (e) { onFile(e.target.files[0]); });
        $("pick").addEventListener("change", function (e) { onFile(e.target.files[0]); });
        $("save").addEventListener("click", save);
        $("restart").addEventListener("click", restart);
        $("again").addEventListener("click", restart);

        if ("serviceWorker" in navigator) {
            // Scope is widened server-side by Service-Worker-Allowed, so the
            // worker controls /scan itself and not only /scan/*.
            navigator.serviceWorker.register("/scan/sw.js", {scope: "/scan"})
                .catch(function () { /* the page works uninstalled; stay quiet */ });
        }
    });
})();

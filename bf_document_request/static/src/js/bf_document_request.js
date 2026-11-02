/* Document request: the client's upload page (/d/<token>).
 *
 * One card per requested document. On a phone, `capture="environment"` opens
 * the rear camera directly; on a computer, the same button falls back to a
 * file picker. That is the browser's native behaviour: no library, no camera
 * permission to beg for.
 *
 * The file NEVER goes through Odoo: the script asks for a presigned URL, PUTs
 * straight to the object storage, then tells the server to go and check.
 * Odoo only sees metadata.
 *
 * Its texts come from the server, in the visitor's language (`cfg.strings`).
 *
 * No ES module: the base's public page loads its scripts with a plain
 * <script src>, and its CSP forbids any inline script.
 */
(function () {
    "use strict";

    var cfg = JSON.parse(document.getElementById("dr-config").textContent);
    var S = cfg.strings;
    var listEl = document.getElementById("dr-lines");
    var submitEl = document.getElementById("dr-submit");
    var errorEl = document.getElementById("dr-error");
    var doneEl = document.getElementById("dr-done");
    var busy = 0;

    function showError(message) {
        errorEl.textContent = message || S.error;
        errorEl.hidden = false;
        errorEl.scrollIntoView({behavior: "smooth", block: "center"});
    }

    function clearError() {
        errorEl.hidden = true;
    }

    /* JSON-RPC call: the dialect Odoo's type="json" routes expect; a bare
     * JSON POST would get a protocol error back. */
    function rpc(path, params) {
        return fetch("/d/" + cfg.token + path, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                jsonrpc: "2.0",
                method: "call",
                params: params || {},
            }),
        }).then(function (response) {
            return response.json();
        }).then(function (payload) {
            if (payload.error) {
                throw new Error(payload.error.message || S.error);
            }
            var result = payload.result || {};
            if (result.error) {
                throw new Error(result.message || S.error);
            }
            return result;
        });
    }

    function refreshSubmit() {
        var blocking = cfg.lines.some(function (line) {
            return line.required && line.state !== "received";
        });
        var any = cfg.lines.some(function (line) {
            return line.state === "received";
        });
        submitEl.disabled = busy > 0 || blocking || !any;
    }

    function render() {
        listEl.innerHTML = "";
        cfg.lines.forEach(function (line) {
            var item = document.createElement("li");
            item.className = "dr-line dr-" + line.state;

            var head = document.createElement("div");
            head.className = "dr-line-head";

            var title = document.createElement("span");
            title.className = "dr-line-name";
            title.textContent = line.name;
            head.appendChild(title);

            if (!line.required) {
                var optional = document.createElement("span");
                optional.className = "dr-badge dr-badge-optional";
                optional.textContent = S.optional;
                head.appendChild(optional);
            }
            if (line.state === "received") {
                var ok = document.createElement("span");
                ok.className = "dr-badge dr-badge-ok";
                ok.textContent = "✓ " + S.received;
                head.appendChild(ok);
            }
            item.appendChild(head);

            if (line.instructions) {
                var hint = document.createElement("p");
                hint.className = "dr-line-hint";
                hint.textContent = line.instructions;
                item.appendChild(hint);
            }

            if (line.filename && line.state === "received") {
                var name = document.createElement("p");
                name.className = "dr-line-file";
                name.textContent = line.filename;
                item.appendChild(name);
            }

            var status = document.createElement("p");
            status.className = "dr-line-status";
            status.hidden = true;
            item.appendChild(status);

            /* The input stays off screen rather than hidden: an input[hidden]
             * cannot be triggered by .click() on some mobile browsers. */
            var input = document.createElement("input");
            input.type = "file";
            input.accept = "image/*,application/pdf";
            input.capture = "environment";
            input.className = "dr-input";

            /* The second input has NO `capture`: on a phone, `capture` opens
             * the camera and nothing else. A PDF received by email (a
             * statement, a certificate) could therefore not be uploaded from
             * a phone. On a computer, both open the file picker. */
            var picker = document.createElement("input");
            picker.type = "file";
            picker.accept = "image/*,application/pdf";
            picker.className = "dr-input";

            var button = document.createElement("button");
            button.type = "button";
            button.className = "st-btn dr-capture";
            button.textContent = line.state === "received" ? S.retake : S.capture;
            button.addEventListener("click", function () {
                input.click();
            });

            var choose = document.createElement("button");
            choose.type = "button";
            choose.className = "st-btn dr-choose";
            choose.textContent = line.state === "received"
                ? (S.choose_again || S.choose) : S.choose;
            choose.addEventListener("click", function () {
                picker.click();
            });

            [input, picker].forEach(function (field) {
                field.addEventListener("change", function () {
                    if (field.files && field.files.length) {
                        upload(line, field.files[0], item, status, [button, choose]);
                    }
                });
            });

            item.appendChild(input);
            item.appendChild(picker);
            item.appendChild(button);
            item.appendChild(choose);
            listEl.appendChild(item);
        });
        refreshSubmit();
    }

    function setDisabled(buttons, value) {
        buttons.forEach(function (btn) {
            btn.disabled = value;
        });
    }

    function upload(line, file, item, status, buttons) {
        clearError();
        busy += 1;
        setDisabled(buttons, true);
        status.hidden = false;
        status.textContent = S.uploading;
        item.classList.add("dr-busy");

        rpc("/presign", {
            line_id: line.id,
            filename: file.name || "document.jpg",
            size: file.size,
        }).then(function (plan) {
            return fetch(plan.url, {
                method: "PUT",
                headers: plan.headers || {},
                body: file,
            }).then(function (response) {
                if (!response.ok) {
                    throw new Error(S.error);
                }
                return rpc("/uploaded", {line_id: line.id});
            });
        }).then(function () {
            line.state = "received";
            line.filename = file.name || "";
            render();
        }).catch(function (exc) {
            showError(exc.message);
            setDisabled(buttons, false);
            status.hidden = true;
            item.classList.remove("dr-busy");
        }).finally(function () {
            busy -= 1;
            refreshSubmit();
        });
    }

    submitEl.addEventListener("click", function () {
        clearError();
        submitEl.disabled = true;
        submitEl.textContent = S.submitting;
        rpc("/submit", {}).then(function () {
            listEl.hidden = true;
            submitEl.hidden = true;
            doneEl.hidden = false;
            doneEl.scrollIntoView({behavior: "smooth", block: "center"});
        }).catch(function (exc) {
            showError(exc.message);
            submitEl.textContent = S.submit;
            refreshSubmit();
        });
    });

    render();
})();

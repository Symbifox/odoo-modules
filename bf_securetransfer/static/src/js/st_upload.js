/* bf_securetransfer — public upload page.
 *
 * Plain JS (no OWL, no jQuery). Everything server-related (limits, API URLs,
 * localized strings, multipart geometry) is read from the #st-config JSON
 * block rendered by the page_upload QWeb template — no server constant is
 * hardcoded here. Client-side validations mirror the server limits for
 * instant feedback; the server remains the authority.
 *
 * Expected DOM (page_upload template):
 *   #st-config        <script type="application/json"> configuration block
 *   #st-upload-panel  wrapper hidden once the transfer is finalized
 *   #st-dropzone      drag-and-drop target (click opens the file picker)
 *   #st-file-input    <input type="file" multiple>
 *   #st-file-list     container for the JS-built file rows
 *   #st-progress      global progress bar container
 *   #st-error         alert region for validation/upload errors
 *   #st-sender-name   #st-sender-email          sender identity fields
 *   #st-recipients    #st-message               recipients (separated) + note
 *   #st-subject       one-line subject shown to the recipient (mail header)
 *   #st-expiry        <select> (options injected from limits.expiry_choices)
 *   #st-password      optional password field
 *   #st-password-row  wrapper hidden when the brand disallows passwords
 *   #st-burn          destroy-after-download checkbox (brand-gated)
 *   #st-recipient-otp require-a-recipient-code checkbox (ticked + disabled
 *                     when the instance requires it for every transfer)
 *   #st-max-downloads download budget, 0 = unlimited
 *   #st-notify        notify the sender on the first download
 *   input[name="website_url"]                   honeypot (hidden)
 *   #st-finalize      submit button
 *   #st-success       success panel (hidden) with #st-share-url + #st-copy
 *
 * Upload flow: create (on first file, once the sender email is set) →
 * per file presign → simple PUT (progress via XHR) or multipart
 * (initiate → sign per batch → 3 concurrent parts → complete). Retries use
 * a 1s/4s/15s backoff (max 5 attempts) and re-sign expired part URLs on 403.
 * Multipart uploads are resumable after a reload through localStorage +
 * the status endpoint (the user re-selects the same file).
 */
(function () {
    "use strict";

    var configEl = document.getElementById("st-config");
    if (!configEl) {
        return;
    }
    var cfg;
    try {
        cfg = JSON.parse(configEl.textContent);
    } catch (e) {
        return;
    }
    var S = cfg.strings || {};
    var LIMITS = cfg.limits || {};
    var LS_KEY = "bf_securetransfer_resume";
    var RETRY_DELAYS = [1000, 4000, 15000];
    var MAX_ATTEMPTS = 5;
    var EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

    var state = {
        uploadToken: null,
        createPromise: null,
        files: [],
        nextUid: 1,
        finalized: false,
        rpcId: 1,
        mode: "files",   // "files" (default) or "message" (message-only)
    };

    // ── Small utilities ───────────────────────────────────────────────────────

    function el(id) {
        return document.getElementById(id);
    }

    function fmt(template, values) {
        return String(template || "").replace(/%\((\w+)\)s/g, function (m, key) {
            return values && key in values ? values[key] : m;
        });
    }

    function humanSize(bytes) {
        var units = S.units || ["o", "Ko", "Mo", "Go", "To"];
        var value = Math.max(0, Number(bytes) || 0);
        var i = 0;
        while (value >= 1024 && i < units.length - 1) {
            value /= 1024;
            i++;
        }
        var text = i === 0 ? String(value) : value.toFixed(value >= 10 ? 0 : 1);
        return text + " " + units[i];
    }

    function sleep(ms) {
        return new Promise(function (resolve) {
            setTimeout(resolve, ms);
        });
    }

    function mkError(code, message) {
        var error = new Error(message || S.error_generic || "Error");
        error.code = code;
        return error;
    }

    function showError(message) {
        var box = el("st-error");
        if (box) {
            box.textContent = message || S.error_generic || "";
            box.hidden = !message;
        }
    }

    function clearError() {
        showError("");
    }

    // ── JSON-RPC transport ────────────────────────────────────────────────────

    function turl(action) {
        return cfg.api.transfer_base + encodeURIComponent(state.uploadToken) + "/" + action;
    }

    async function rpc(url, params) {
        var response;
        try {
            response = await fetch(url, {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    jsonrpc: "2.0",
                    method: "call",
                    params: params || {},
                    id: state.rpcId++,
                }),
            });
        } catch (e) {
            throw mkError("network", S.error_network);
        }
        if (!response.ok) {
            throw mkError("http", S.error_generic);
        }
        var data = await response.json();
        if (data.error) {
            throw mkError("server", S.error_generic);
        }
        var result = data.result;
        if (result && result.error) {
            throw mkError(result.error, result.message || S.error_generic);
        }
        return result;
    }

    // ── Raw uploads (XHR for progress events) ─────────────────────────────────

    // Presigned PUTs must go out with exactly the headers that were signed.
    // Content-Length is set by the browser from the blob; Host is forbidden.
    var SKIP_HEADERS = /^(content-length|host)$/i;

    function putBlob(url, blob, headers, onProgress) {
        return new Promise(function (resolve, reject) {
            var xhr = new XMLHttpRequest();
            xhr.open("PUT", url, true);
            Object.keys(headers || {}).forEach(function (name) {
                if (!SKIP_HEADERS.test(name)) {
                    try {
                        xhr.setRequestHeader(name, headers[name]);
                    } catch (e) {
                        /* forbidden header: browser sets it itself */
                    }
                }
            });
            xhr.upload.onprogress = function (event) {
                if (onProgress && event.lengthComputable) {
                    onProgress(event.loaded);
                }
            };
            xhr.onload = function () {
                if (xhr.status >= 200 && xhr.status < 300) {
                    resolve(xhr);
                } else {
                    var error = mkError("put", S.error_generic);
                    error.status = xhr.status;
                    reject(error);
                }
            };
            xhr.onerror = function () {
                reject(mkError("network", S.error_network));
            };
            xhr.send(blob);
        });
    }

    // ── Form field access ─────────────────────────────────────────────────────

    function fieldValue(id) {
        var input = el(id);
        return input ? (input.value || "").trim() : "";
    }

    function honeypotValue() {
        var input = document.querySelector(
            'input[name="' + (cfg.honeypot_field || "website_url") + '"]');
        return input ? input.value || "" : "";
    }

    function expiryValue() {
        var select = el("st-expiry");
        var choices = LIMITS.expiry_choices || [7];
        var value = select ? parseInt(select.value, 10) : NaN;
        if (choices.indexOf(value) !== -1) {
            return value;
        }
        return choices.indexOf(7) !== -1 ? 7 : choices[0];
    }

    // Returns the cleaned separated list, or null after showing an error.
    function recipientsValue() {
        var raw = fieldValue("st-recipients");
        if (!raw) {
            return "";
        }
        var parts = raw.split(/[,;\s]+/).filter(Boolean);
        if (parts.length > (cfg.max_recipients || 10)) {
            showError(fmt(S.recipients_too_many, { max: cfg.max_recipients || 10 }));
            return null;
        }
        for (var i = 0; i < parts.length; i++) {
            if (!EMAIL_RE.test(parts[i])) {
                showError(fmt(S.recipients_invalid, { email: parts[i] }));
                return null;
            }
        }
        return parts.join(", ");
    }

    function messageValue() {
        var message = fieldValue("st-message");
        if (message.length > (cfg.max_message_chars || 2000)) {
            showError(fmt(S.message_too_long, { max: cfg.max_message_chars || 2000 }));
            return null;
        }
        return message;
    }

    // Returns the subject, or null after showing an error. The server
    // re-validates and strips CR/LF (the value ends up in a mail header).
    function subjectValue() {
        var subject = fieldValue("st-subject");
        if (subject.length > (cfg.max_subject_chars || 120)) {
            showError(fmt(S.subject_too_long, { max: cfg.max_subject_chars || 120 }));
            return null;
        }
        return subject;
    }

    // ── Transfer creation ─────────────────────────────────────────────────────

    function ensureTransfer() {
        if (state.uploadToken) {
            return Promise.resolve(state.uploadToken);
        }
        if (state.createPromise) {
            return state.createPromise;
        }
        // No e-mail required to create the draft — it is validated at send.
        // This lets a file be dropped before the e-mail is typed.
        var params = {
            sender_name: fieldValue("st-sender-name"),
            sender_email: fieldValue("st-sender-email"),
            recipient_emails: fieldValue("st-recipients"),
            subject: fieldValue("st-subject"),
            message: fieldValue("st-message"),
            retention_days: expiryValue(),
        };
        params[cfg.honeypot_field || "website_url"] = honeypotValue();
        // Personal drop page: bind the draft to the drop brand (fixed
        // recipient). The server forces the destination — the client value is
        // advisory only.
        if (cfg.drop_slug) {
            params.drop_slug = cfg.drop_slug;
        }
        state.createPromise = rpc(cfg.api.create, params).then(function (result) {
            state.uploadToken = result.upload_token;
            saveResume();
            return state.uploadToken;
        }).catch(function (error) {
            state.createPromise = null;
            throw error;
        });
        return state.createPromise;
    }

    // ── File rows ─────────────────────────────────────────────────────────────

    function renderRow(entry) {
        var row = document.createElement("div");
        row.className = "st-file";
        row.dataset.uid = entry.uid;

        var info = document.createElement("div");
        info.className = "st-file-info";
        var name = document.createElement("span");
        name.className = "st-file-name";
        name.textContent = entry.name;
        var size = document.createElement("span");
        size.className = "st-file-size";
        size.textContent = humanSize(entry.size);
        info.appendChild(name);
        info.appendChild(size);

        var status = document.createElement("span");
        status.className = "st-file-status";

        var progress = document.createElement("div");
        progress.className = "st-progress st-progress-small";
        var bar = document.createElement("div");
        bar.className = "st-progress-bar";
        progress.appendChild(bar);

        var remove = document.createElement("button");
        remove.type = "button";
        remove.className = "st-file-remove";
        remove.textContent = "×";
        remove.title = S.remove || "Retirer";
        remove.setAttribute("aria-label", S.remove || "Retirer");
        remove.addEventListener("click", function () {
            removeEntry(entry);
        });

        row.appendChild(info);
        row.appendChild(progress);
        row.appendChild(status);
        row.appendChild(remove);

        var list = el("st-file-list");
        if (list) {
            list.appendChild(row);
        }
        entry.dom = { row: row, bar: bar, status: status, remove: remove };
        setRowStatus(entry, S.status_waiting);
    }

    function setRowStatus(entry, text) {
        if (entry.dom) {
            entry.dom.status.textContent = text || "";
        }
    }

    function setRowProgress(entry) {
        if (entry.dom) {
            var percent = entry.size
                ? Math.min(100, Math.round((entry.loaded / entry.size) * 100))
                : 0;
            entry.dom.bar.style.width = percent + "%";
        }
    }

    function activeEntries() {
        return state.files.filter(function (entry) {
            return entry.status !== "removed";
        });
    }

    function updateGlobalProgress() {
        var container = el("st-progress");
        if (!container) {
            return;
        }
        var bar = container.querySelector(".st-progress-bar");
        if (!bar) {
            bar = document.createElement("div");
            bar.className = "st-progress-bar";
            container.appendChild(bar);
        }
        var entries = activeEntries();
        var total = 0;
        var loaded = 0;
        entries.forEach(function (entry) {
            total += entry.size;
            loaded += Math.min(entry.loaded, entry.size);
        });
        container.hidden = !total;
        bar.style.width = (total ? Math.round((loaded / total) * 100) : 0) + "%";
    }

    function updateFinalizeState() {
        var button = el("st-finalize");
        if (!button) {
            return;
        }
        var ready;
        if (state.mode === "message") {
            // Message-only: ready as soon as a message is typed.
            var msg = el("st-message");
            ready = !!(msg && msg.value.trim());
        } else {
            var entries = activeEntries();
            ready = entries.length > 0 && entries.every(function (entry) {
                return entry.status === "done";
            });
        }
        button.disabled = !ready || state.finalized;
    }

    // ── Client-side validation (mirror of the server limits) ─────────────────

    function validateNewFile(file) {
        var entries = activeEntries();
        if (entries.length + 1 > (LIMITS.max_files || 25)) {
            return fmt(S.add_error_count, { max: LIMITS.max_files || 25 });
        }
        var dotIndex = file.name.lastIndexOf(".");
        var extension = dotIndex > 0
            ? file.name.slice(dotIndex + 1).toLowerCase() : "";
        if (!extension) {
            return fmt(S.add_error_ext_missing, { name: file.name });
        }
        if ((cfg.deny_extensions || []).indexOf(extension) !== -1) {
            return fmt(S.add_error_ext_denied, { name: file.name, ext: extension });
        }
        var maxBytes = LIMITS.max_bytes || 0;
        if (maxBytes && file.size > maxBytes) {
            return fmt(S.add_error_too_large,
                { name: file.name, max: humanSize(maxBytes) });
        }
        var total = file.size;
        entries.forEach(function (entry) {
            total += entry.size;
        });
        if (maxBytes && total > maxBytes) {
            return fmt(S.add_error_total, { max: humanSize(maxBytes) });
        }
        return null;
    }

    // ── Upload orchestration ──────────────────────────────────────────────────

    function addFiles(fileList) {
        clearError();
        Array.prototype.slice.call(fileList || []).forEach(function (file) {
            if (!file || !file.name) {
                return;
            }
            var problem = validateNewFile(file);
            if (problem) {
                showError(problem);
                return;
            }
            var entry = {
                uid: state.nextUid++,
                file: file,
                name: file.name,
                size: file.size,
                fileId: null,
                status: "pending",
                loaded: 0,
                doneParts: {},
                partLoaded: {},
                doneBytes: 0,
                etags: {},
                partSize: 0,
                partsTotal: 0,
                initiated: false,
            };
            state.files.push(entry);
            renderRow(entry);
            startUpload(entry);
        });
        updateGlobalProgress();
        updateFinalizeState();
    }

    async function startUpload(entry) {
        entry.status = "waiting";
        setRowStatus(entry, S.status_waiting);
        try {
            await ensureTransfer();
        } catch (error) {
            if (error.code === "email_required") {
                entry.status = "blocked";
                setRowStatus(entry, S.status_blocked);
                showError(S.email_required);
                var email = el("st-sender-email");
                if (email) {
                    email.focus();
                }
            } else {
                entry.status = "error";
                setRowStatus(entry, S.status_error);
                showError(error.message);
            }
            updateFinalizeState();
            return;
        }
        try {
            if (!entry.fileId) {
                var plan = await rpc(turl("presign"), {
                    filename: entry.name,
                    size: entry.size,
                });
                entry.fileId = plan.file_id;
                entry.plan = plan;
                saveResume();
            }
            entry.status = "uploading";
            setRowStatus(entry, S.status_uploading);
            if (entry.plan && entry.plan.mode === "simple") {
                await uploadSimple(entry);
            } else {
                if (entry.plan) {
                    entry.partSize = entry.plan.part_size;
                    entry.partsTotal = entry.plan.parts_total;
                }
                await uploadMultipart(entry);
            }
            entry.status = "done";
            entry.loaded = entry.size;
            setRowStatus(entry, S.status_done);
            setRowProgress(entry);
        } catch (error) {
            if (entry.status !== "removed") {
                entry.status = "error";
                setRowStatus(entry, S.status_error);
                showError(error.message);
            }
        }
        saveResume();
        updateGlobalProgress();
        updateFinalizeState();
    }

    async function uploadSimple(entry) {
        var url = entry.plan.url;
        var headers = entry.plan.headers || {};
        for (var attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
            if (entry.status === "removed") {
                throw mkError("removed", "");
            }
            try {
                // slice() drops the File's content type so the browser sends
                // no Content-Type header — only the signed headers travel.
                var blob = entry.file.slice(0, entry.size);
                await putBlob(url, blob, headers, function (loaded) {
                    entry.loaded = loaded;
                    setRowProgress(entry);
                    updateGlobalProgress();
                });
                return;
            } catch (error) {
                if (attempt >= MAX_ATTEMPTS) {
                    throw error;
                }
                if (error.status === 403) {
                    // Expired presign: register a fresh file record (there is
                    // no re-sign endpoint for simple uploads) and drop the
                    // stale one.
                    var staleId = entry.fileId;
                    var plan = await rpc(turl("presign"), {
                        filename: entry.name,
                        size: entry.size,
                    });
                    entry.fileId = plan.file_id;
                    entry.plan = plan;
                    url = plan.url;
                    headers = plan.headers || {};
                    saveResume();
                    try {
                        await rpc(turl("remove"), { file_id: staleId });
                    } catch (cleanupError) {
                        /* draft GC sweeps it */
                    }
                }
                setRowStatus(entry, fmt(S.retrying, { n: attempt, max: MAX_ATTEMPTS }));
                await sleep(RETRY_DELAYS[Math.min(attempt - 1, RETRY_DELAYS.length - 1)]);
            }
        }
    }

    async function signParts(entry, numbers) {
        var result = await rpc(turl("multipart/sign"), {
            file_id: entry.fileId,
            part_numbers: numbers,
        });
        return result.urls || {};
    }

    function refreshEntryProgress(entry) {
        var inflight = 0;
        Object.keys(entry.partLoaded).forEach(function (key) {
            inflight += entry.partLoaded[key];
        });
        entry.loaded = Math.min(entry.size, entry.doneBytes + inflight);
        setRowProgress(entry);
        updateGlobalProgress();
    }

    async function uploadPartWithRetry(entry, number, urls) {
        for (var attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
            if (entry.status === "removed") {
                throw mkError("removed", "");
            }
            try {
                var start = (number - 1) * entry.partSize;
                var end = Math.min(start + entry.partSize, entry.size);
                var blob = entry.file.slice(start, end);
                var xhr = await putBlob(urls[String(number)], blob, null,
                    function (loaded) {
                        entry.partLoaded[number] = loaded;
                        refreshEntryProgress(entry);
                    });
                var etag = xhr.getResponseHeader("ETag");
                if (etag) {
                    entry.etags[number] = etag;
                } else {
                    // The server rebuilds ETags from ListParts anyway, but a
                    // missing header means the bucket CORS lacks ExposeHeaders.
                    console.warn("bf_securetransfer: part ETag not readable " +
                        "(check bucket CORS ExposeHeaders: ETag)");
                }
                entry.doneParts[number] = true;
                delete entry.partLoaded[number];
                entry.doneBytes += blob.size;
                refreshEntryProgress(entry);
                saveResume();
                return;
            } catch (error) {
                delete entry.partLoaded[number];
                if (attempt >= MAX_ATTEMPTS) {
                    throw error;
                }
                if (error.status === 403) {
                    var fresh = await signParts(entry, [number]);
                    if (fresh[String(number)]) {
                        urls[String(number)] = fresh[String(number)];
                    }
                }
                setRowStatus(entry, fmt(S.retrying, { n: attempt, max: MAX_ATTEMPTS }));
                await sleep(RETRY_DELAYS[Math.min(attempt - 1, RETRY_DELAYS.length - 1)]);
            }
        }
    }

    async function runPool(items, limit, worker) {
        var index = 0;
        async function next() {
            while (index < items.length) {
                var item = items[index++];
                await worker(item);
            }
        }
        var runners = [];
        for (var i = 0; i < Math.min(limit, items.length); i++) {
            runners.push(next());
        }
        await Promise.all(runners);
    }

    async function uploadMultipart(entry) {
        // Gate on partsTotal, NOT on `initiated`: localStorage never persists
        // part_size/parts_total, so a cross-session resume arrives with
        // partsTotal=0 even though the MPU exists server-side. mpu_initiate is
        // idempotent (returns the existing upload_id + plan), so calling it
        // here both starts a fresh MPU and recovers a resumed one — without it
        // the todo loop below would be empty and mpu_complete would abort the
        // already-uploaded parts.
        if (!entry.partsTotal) {
            var init = await rpc(turl("multipart/initiate"), {
                file_id: entry.fileId,
            });
            entry.partSize = init.part_size;
            entry.partsTotal = init.parts_total;
            entry.initiated = true;
            saveResume();
        }
        var todo = [];
        for (var n = 1; n <= entry.partsTotal; n++) {
            if (!entry.doneParts[n]) {
                todo.push(n);
            }
        }
        var batchMax = (cfg.multipart && cfg.multipart.sign_batch_max) || 20;
        var concurrency = (cfg.multipart && cfg.multipart.concurrency) || 3;
        while (todo.length) {
            if (entry.status === "removed") {
                throw mkError("removed", "");
            }
            var batch = todo.splice(0, batchMax);
            var urls = await signParts(entry, batch);
            await runPool(batch, concurrency, function (number) {
                return uploadPartWithRetry(entry, number, urls);
            });
        }
        await rpc(turl("multipart/complete"), { file_id: entry.fileId });
    }

    async function removeEntry(entry) {
        entry.status = "removed";
        if (entry.dom) {
            entry.dom.row.remove();
        }
        if (entry.fileId && state.uploadToken) {
            try {
                await rpc(turl("remove"), { file_id: entry.fileId });
            } catch (error) {
                /* best-effort: the draft GC sweeps leftovers */
            }
        }
        saveResume();
        updateGlobalProgress();
        updateFinalizeState();
    }

    // ── Resume after reload ───────────────────────────────────────────────────

    function saveResume() {
        if (state.finalized || !state.uploadToken) {
            return;
        }
        try {
            localStorage.setItem(LS_KEY, JSON.stringify({
                uploadToken: state.uploadToken,
                savedAt: Date.now(),
                files: activeEntries().filter(function (entry) {
                    return entry.fileId;
                }).map(function (entry) {
                    return {
                        fileId: entry.fileId,
                        name: entry.name,
                        size: entry.size,
                        multipart: !!entry.initiated,
                        done: entry.status === "done",
                    };
                }),
            }));
        } catch (e) {
            /* storage full/blocked: resume simply unavailable */
        }
    }

    function clearResume() {
        try {
            localStorage.removeItem(LS_KEY);
        } catch (e) {
            /* ignore */
        }
    }

    function loadResume() {
        try {
            var raw = localStorage.getItem(LS_KEY);
            return raw ? JSON.parse(raw) : null;
        } catch (e) {
            return null;
        }
    }

    function initResume() {
        var saved = loadResume();
        if (!saved || !saved.uploadToken || !(saved.files || []).length) {
            return;
        }
        var incomplete = saved.files.filter(function (item) {
            return !item.done;
        });
        var list = el("st-file-list");
        if (!list) {
            return;
        }
        var banner = document.createElement("div");
        banner.className = "st-resume";
        var title = document.createElement("p");
        title.className = "st-resume-title";
        title.textContent = S.resume_title || "";
        banner.appendChild(title);

        state.uploadToken = saved.uploadToken;
        saved.files.forEach(function (item) {
            if (item.done) {
                // Already verified server-side at finalize; show it as done so
                // the user can still finalize the interrupted transfer.
                var doneEntry = {
                    uid: state.nextUid++,
                    file: null,
                    name: item.name,
                    size: item.size,
                    fileId: item.fileId,
                    status: "done",
                    loaded: item.size,
                    doneParts: {},
                    partLoaded: {},
                    doneBytes: item.size,
                    etags: {},
                    partSize: 0,
                    partsTotal: 0,
                    initiated: item.multipart,
                };
                state.files.push(doneEntry);
                renderRow(doneEntry);
                setRowStatus(doneEntry, S.status_done);
                setRowProgress(doneEntry);
            }
        });

        incomplete.forEach(function (item) {
            var line = document.createElement("div");
            line.className = "st-resume-line";
            var hint = document.createElement("span");
            hint.textContent = fmt(S.resume_hint, { name: item.name });
            var button = document.createElement("button");
            button.type = "button";
            button.className = "st-btn st-btn-secondary";
            button.textContent = S.resume || "Reprendre";
            button.addEventListener("click", function () {
                pickResumeFile(item, banner, line);
            });
            line.appendChild(hint);
            line.appendChild(button);
            banner.appendChild(line);
        });
        if (incomplete.length) {
            list.parentNode.insertBefore(banner, list);
        }
        updateGlobalProgress();
        updateFinalizeState();
    }

    function pickResumeFile(item, banner, line) {
        var picker = document.createElement("input");
        picker.type = "file";
        picker.style.display = "none";
        document.body.appendChild(picker);
        picker.addEventListener("change", function () {
            var file = picker.files && picker.files[0];
            picker.remove();
            if (!file) {
                return;
            }
            if (file.name !== item.name || file.size !== item.size) {
                showError(S.resume_mismatch);
                return;
            }
            clearError();
            line.remove();
            if (!banner.querySelector(".st-resume-line")) {
                banner.remove();
            }
            resumeEntry(item, file);
        });
        picker.click();
    }

    async function resumeEntry(item, file) {
        var entry = {
            uid: state.nextUid++,
            file: file,
            name: item.name,
            size: item.size,
            fileId: item.fileId,
            status: "uploading",
            loaded: 0,
            doneParts: {},
            partLoaded: {},
            doneBytes: 0,
            etags: {},
            partSize: 0,
            partsTotal: 0,
            initiated: item.multipart,
        };
        state.files.push(entry);
        renderRow(entry);
        setRowStatus(entry, S.status_uploading);
        try {
            if (item.multipart) {
                // Rebuild the done-part map from the bucket truth, then only
                // upload what is missing.
                var status = await rpc(turl("multipart/status"), {
                    file_id: entry.fileId,
                });
                (status.parts || []).forEach(function (part) {
                    entry.doneParts[part.n] = true;
                    entry.doneBytes += part.size || 0;
                    if (part.etag) {
                        entry.etags[part.n] = part.etag;
                    }
                });
                refreshEntryProgress(entry);
                await uploadMultipart(entry);
            } else {
                // Simple uploads are not partial: drop the stale record and
                // restart as a fresh file.
                var staleId = entry.fileId;
                entry.fileId = null;
                try {
                    await rpc(turl("remove"), { file_id: staleId });
                } catch (cleanupError) {
                    /* draft GC sweeps it */
                }
                var plan = await rpc(turl("presign"), {
                    filename: entry.name,
                    size: entry.size,
                });
                entry.fileId = plan.file_id;
                entry.plan = plan;
                if (plan.mode === "simple") {
                    await uploadSimple(entry);
                } else {
                    entry.partSize = plan.part_size;
                    entry.partsTotal = plan.parts_total;
                    await uploadMultipart(entry);
                }
            }
            entry.status = "done";
            entry.loaded = entry.size;
            setRowStatus(entry, S.status_done);
            setRowProgress(entry);
        } catch (error) {
            if (error.code === "not_found" || error.code === "not_draft") {
                clearResume();
                showError(S.resume_gone);
            } else {
                showError(error.message);
            }
            entry.status = "error";
            setRowStatus(entry, S.status_error);
        }
        saveResume();
        updateGlobalProgress();
        updateFinalizeState();
    }

    // ── Options panel + finalize ──────────────────────────────────────────────

    function populateExpiryChoices() {
        var select = el("st-expiry");
        if (!select) {
            return;
        }
        select.innerHTML = "";
        var choices = LIMITS.expiry_choices || [7];
        choices.forEach(function (days) {
            var option = document.createElement("option");
            option.value = String(days);
            option.textContent = days === 1
                ? (S.expiry_one_day || "1 jour")
                : fmt(S.expiry_days, { n: days });
            if (days === 7 || (choices.indexOf(7) === -1 && days === choices[0])) {
                option.selected = true;
            }
            select.appendChild(option);
        });
    }

    function applyPasswordVisibility() {
        var row = el("st-password-row");
        if (row && !LIMITS.allow_password) {
            row.hidden = true;
        }
    }

    function setMode(mode) {
        state.mode = mode;
        var filesPanel = el("st-mode-files");
        var msgPanel = el("st-mode-message");
        if (filesPanel) { filesPanel.hidden = mode !== "files"; }
        if (msgPanel) { msgPanel.hidden = mode !== "message"; }
        var tf = el("st-tab-files");
        var tm = el("st-tab-message");
        if (tf) { tf.classList.toggle("st-tab-active", mode === "files"); }
        if (tm) { tm.classList.toggle("st-tab-active", mode === "message"); }
        var label = el("st-message-label");
        if (label) {
            // Through S like every other string: hardcoding these turned an
            // English page back to French after two tab clicks.
            label.textContent = mode === "message"
                ? (S.message_label_required || "Message *")
                : (S.message_label_optional || "Message (optionnel)");
        }
        var btn = el("st-finalize");
        if (btn && !state.finalized) {
            if (mode === "message") {
                btn.textContent = S.send_message || "Envoyer le message";
            } else {
                // Drop page: the file goes straight to the owner — "send",
                // not "get the link".
                btn.textContent = cfg.drop_mode
                    ? (S.send_drop || S.send || "Envoyer")
                    : (S.send || "Envoyer");
            }
        }
        clearError();
        updateFinalizeState();
    }

    async function onFinalize() {
        clearError();
        var button = el("st-finalize");
        // E-mail is required to SEND (not to drop files).
        var senderEmail = fieldValue("st-sender-email");
        if (!EMAIL_RE.test(senderEmail)) {
            showError(S.email_required);
            return;
        }
        var entries = activeEntries();
        var message = messageValue();
        if (message === null) {
            return;
        }
        if (state.mode === "message") {
            // Message-only: no files needed, but the message is required.
            if (!message) {
                showError(S.message_required || "Saisissez un message.");
                return;
            }
        } else {
            if (!entries.length) {
                showError(S.no_files);
                return;
            }
            if (entries.some(function (entry) { return entry.status !== "done"; })) {
                showError(S.uploads_in_progress);
                return;
            }
        }
        var recipients = recipientsValue();
        if (recipients === null) {
            return;
        }
        var subject = subjectValue();
        if (subject === null) {
            return;
        }
        // Message-only mode never dropped a file, so the draft may not exist
        // yet — create it now (idempotent in files mode).
        try {
            await ensureTransfer();
        } catch (error) {
            showError(error.message);
            return;
        }
        var params = {
            sender_email: senderEmail,
            sender_name: fieldValue("st-sender-name"),
            recipient_emails: recipients,
            subject: subject,
            message: message,
            retention_days: expiryValue(),
        };
        var password = LIMITS.allow_password ? fieldValue("st-password") : "";
        if (password) {
            params.password = password;
        }
        var burn = el("st-burn");
        if (LIMITS.allow_burn && burn && burn.checked) {
            params.burn_after_download = true;
        }
        // Recipient code. Read even when the box is disabled (instance-wide
        // requirement): a disabled input submits nothing, and the gate the
        // sender was shown must be pinned on the transfer itself.
        var otp = el("st-recipient-otp");
        if (otp && otp.checked) {
            params.force_recipient_otp = true;
        }
        var budget = el("st-max-downloads");
        if (budget) {
            params.max_downloads = parseInt(budget.value, 10) || 0;
        }
        var notify = el("st-notify");
        if (notify) {
            params.notify_on_download = notify.checked;
        }
        if (button) {
            button.disabled = true;
            button.dataset.label = button.textContent;
            button.textContent = S.finalizing || button.textContent;
        }
        try {
            var result = await rpc(turl("finalize"), params);
            if (result && result.otp_required === "sender") {
                // Tenant requires the sender to confirm by e-mailed code.
                showOtpChallenge();
                return;
            }
            state.finalized = true;
            clearResume();
            showSuccess(result.share_url, result.expiry_date);
        } catch (error) {
            showError(error.message);
            if (button) {
                button.disabled = false;
                button.textContent = button.dataset.label || S.send || "";
            }
        }
    }

    function showOtpChallenge() {
        var panel = el("st-upload-panel");
        if (panel) { panel.hidden = true; }
        var otp = el("st-otp");
        if (!otp) { return; }
        otp.hidden = false;
        var input = el("st-otp-code");
        if (input) { input.focus(); }
        var confirm = el("st-otp-confirm");
        if (confirm && !confirm.dataset.bound) {
            confirm.dataset.bound = "1";
            confirm.addEventListener("click", submitOtp);
        }
        var resend = el("st-otp-resend");
        if (resend && !resend.dataset.bound) {
            resend.dataset.bound = "1";
            resend.addEventListener("click", async function () {
                // This button used to swallow every outcome: success and
                // failure looked identical (nothing happened), so a sender
                // past the rate limit just kept clicking a button they
                // believed was broken. Say what happened, either way.
                clearError();
                var original = resend.textContent;
                resend.disabled = true;
                try {
                    await rpc(turl("confirm/resend"), {});
                    resend.textContent = S.otp_resent
                        || "Un nouveau code vous a été envoyé.";
                } catch (e) {
                    showError(e.message || S.error_generic);
                    resend.textContent = original;
                } finally {
                    setTimeout(function () {
                        resend.disabled = false;
                        resend.textContent = original;
                    }, 4000);
                }
            });
        }
    }

    async function submitOtp() {
        clearError();
        var input = el("st-otp-code");
        var code = input ? input.value.trim() : "";
        try {
            var result = await rpc(turl("confirm"), { code: code });
            state.finalized = true;
            clearResume();
            var otp = el("st-otp");
            if (otp) { otp.hidden = true; }
            showSuccess(result.share_url, result.expiry_date);
        } catch (error) {
            showError(error.message);
        }
    }

    function showSuccess(shareUrl, expiryDate) {
        var panel = el("st-upload-panel");
        if (panel) {
            panel.hidden = true;
        }
        var success = el("st-success");
        if (success) {
            success.hidden = false;
        }
        var urlField = el("st-share-url");
        if (urlField) {
            urlField.value = shareUrl || "";
        }
        var expiry = el("st-expiry-note");
        if (expiry && expiryDate) {
            expiry.textContent = (expiry.dataset.template || "{date}")
                .replace("{date}", expiryDate);
        }
        var copyButton = el("st-copy");
        if (copyButton) {
            copyButton.addEventListener("click", function () {
                copyToClipboard(shareUrl || (urlField && urlField.value) || "",
                    copyButton);
            });
        }
    }

    function copyToClipboard(text, button) {
        function feedback() {
            var previous = button.textContent;
            button.textContent = S.copied || previous;
            setTimeout(function () {
                button.textContent = previous;
            }, 2000);
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(feedback, function () {
                legacyCopy(text);
                feedback();
            });
        } else {
            legacyCopy(text);
            feedback();
        }
    }

    function legacyCopy(text) {
        var area = document.createElement("textarea");
        area.value = text;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        try {
            document.execCommand("copy");
        } catch (e) {
            /* ignore */
        }
        area.remove();
    }

    // ── Wiring ────────────────────────────────────────────────────────────────

    function retryBlockedEntries() {
        var email = fieldValue("st-sender-email");
        if (!EMAIL_RE.test(email)) {
            return;
        }
        clearError();
        state.files.forEach(function (entry) {
            if (entry.status === "blocked") {
                startUpload(entry);
            }
        });
    }

    function init() {
        populateExpiryChoices();
        applyPasswordVisibility();
        updateFinalizeState();
        initResume();

        var dropzone = el("st-dropzone");
        var input = el("st-file-input");
        if (dropzone && input) {
            dropzone.addEventListener("click", function () {
                input.click();
            });
            dropzone.addEventListener("keydown", function (event) {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    input.click();
                }
            });
            ["dragenter", "dragover"].forEach(function (type) {
                dropzone.addEventListener(type, function (event) {
                    event.preventDefault();
                    dropzone.classList.add("st-dragover");
                });
            });
            ["dragleave", "drop"].forEach(function (type) {
                dropzone.addEventListener(type, function (event) {
                    event.preventDefault();
                    dropzone.classList.remove("st-dragover");
                });
            });
            dropzone.addEventListener("drop", function (event) {
                if (event.dataTransfer && event.dataTransfer.files) {
                    addFiles(event.dataTransfer.files);
                }
            });
            input.addEventListener("change", function () {
                addFiles(input.files);
                input.value = "";
            });
        }

        var email = el("st-sender-email");
        if (email) {
            email.addEventListener("change", retryBlockedEntries);
            email.addEventListener("blur", retryBlockedEntries);
        }

        var messageField = el("st-message");
        if (messageField) {
            // In message-only mode the send button depends on the message.
            messageField.addEventListener("input", updateFinalizeState);
        }

        var finalize = el("st-finalize");
        if (finalize) {
            finalize.addEventListener("click", function (event) {
                event.preventDefault();
                onFinalize();
            });
        }

        ["files", "message"].forEach(function (mode) {
            var tab = el("st-tab-" + mode);
            if (tab) {
                tab.addEventListener("click", function () { setMode(mode); });
            }
        });

        window.addEventListener("beforeunload", function (event) {
            var uploading = state.files.some(function (entry) {
                return entry.status === "uploading" || entry.status === "waiting";
            });
            if (uploading && !state.finalized) {
                event.preventDefault();
                event.returnValue = "";
            }
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();

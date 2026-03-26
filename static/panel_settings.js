(function () {
    const pageModules = window.MCWebPageModules || null;

    function mountPanelSettings() {
        const shell = window.MCWebShell || null;
        const http = window.MCWebHttp || null;
        const configEl = document.getElementById("panel-settings-config");
        let config = {};
        try {
            config = JSON.parse(configEl?.textContent || "{}") || {};
        } catch (_) {
            config = {};
        }

        const statusEl = document.getElementById("panel-settings-status");

        const requirePasswordInput = document.getElementById("panel-require-password");
        const newPasswordInput = document.getElementById("panel-new-password");
        const newPasswordConfirmInput = document.getElementById("panel-new-password-confirm");
        const newSuperadminPasswordInput = document.getElementById("panel-new-superadmin-password");
        const newSuperadminPasswordConfirmInput = document.getElementById("panel-new-superadmin-password-confirm");
        const saveSecurityBtn = document.getElementById("panel-save-security");

        const minecraftRootInput = document.getElementById("panel-minecraft-root");
        const backupDirInput = document.getElementById("panel-backup-dir");
        const displayTzSelect = document.getElementById("panel-display-tz");
        const createBackupDirInput = document.getElementById("panel-create-backup-dir");
        const savePathsBtn = document.getElementById("panel-save-paths");
        const saveTimezoneBtn = document.getElementById("panel-save-timezone");

        const refreshStatesBtn = document.getElementById("panel-refresh-states");
        const rebootBtn = document.getElementById("panel-reboot-app");

        const deviceMapBody = document.getElementById("panel-device-map-body");
        const addDeviceRowBtn = document.getElementById("panel-add-device-row");
        const saveDeviceMapBtn = document.getElementById("panel-save-device-map");
        const csvInput = document.getElementById("panel-device-csv");
        const csvDropzone = document.getElementById("panel-device-csv-dropzone");
        const csvModeSelect = document.getElementById("panel-device-import-mode");
        const uploadCsvBtn = document.getElementById("panel-upload-device-csv");
        let selectedCsvFile = null;

        const initialSecurityState = {
            requirePassword: !!requirePasswordInput?.checked,
        };
        const initialPathSettingsState = {
            minecraftRoot: String(minecraftRootInput?.value || ""),
            backupDir: String(backupDirInput?.value || ""),
            createBackupDir: !!createBackupDirInput?.checked,
        };
        const initialTimezoneState = {
            displayTz: String(displayTzSelect?.value || ""),
        };
        let deviceMapBaseline = "";

        function setStatus(message, kind) {
            if (!statusEl) return;
            statusEl.textContent = message || "";
            statusEl.classList.remove("ok", "error");
            if (kind === "ok") statusEl.classList.add("ok");
            if (kind === "error") statusEl.classList.add("error");
        }

        function withAdminPassword(task) {
            if (shell && typeof shell.requestPanelSettingsAccess === "function") {
                shell.requestPanelSettingsAccess({
                    forcePrompt: true,
                    onSuccess: (password) => {
                        const cleaned = String(password || "").trim();
                        if (!cleaned) {
                            setStatus("Enter the superadmin password to apply changes.", "error");
                            return;
                        }
                        task(cleaned);
                    },
                });
                return;
            }
            setStatus("Unable to open the admin password prompt.", "error");
        }

        function setSaveVisibility(button, isVisible) {
            if (!button) return;
            button.hidden = !isVisible;
        }

        function isSecurityDirty() {
            if (!requirePasswordInput) return false;
            if (!!requirePasswordInput.checked !== initialSecurityState.requirePassword) return true;
            if (String(newPasswordInput?.value || "").trim()) return true;
            if (String(newPasswordConfirmInput?.value || "").trim()) return true;
            if (String(newSuperadminPasswordInput?.value || "").trim()) return true;
            if (String(newSuperadminPasswordConfirmInput?.value || "").trim()) return true;
            return false;
        }

        function syncSecuritySaveVisibility() {
            setSaveVisibility(saveSecurityBtn, isSecurityDirty());
        }

        function isPathsDirty() {
            if (String(minecraftRootInput?.value || "") !== initialPathSettingsState.minecraftRoot) return true;
            if (String(backupDirInput?.value || "") !== initialPathSettingsState.backupDir) return true;
            if (!!createBackupDirInput?.checked !== initialPathSettingsState.createBackupDir) return true;
            return false;
        }

        function syncPathsSaveVisibility() {
            setSaveVisibility(savePathsBtn, isPathsDirty());
        }

        function isTimezoneDirty() {
            if (String(displayTzSelect?.value || "") !== initialTimezoneState.displayTz) return true;
            return false;
        }

        function syncTimezoneSaveVisibility() {
            setSaveVisibility(saveTimezoneBtn, isTimezoneDirty());
        }

        function parseAddressLines(value) {
            return Array.from(new Set(String(value || "")
                .split(/\r?\n|,/)
                .map((part) => String(part || "").trim())
                .filter(Boolean))).sort();
        }

        function normalizeMachine(machine) {
            return {
                machine_name: String(machine?.machine_name || machine?.name || "").trim(),
                addresses: Array.from(new Set((Array.isArray(machine?.addresses) ? machine.addresses : [])
                    .map((value) => String(value || "").trim())
                    .filter(Boolean))).sort(),
                last_seen: String(machine?.last_seen || "").trim() || "-",
                owner: String(machine?.owner || "").trim() || "-",
            };
        }

        function buildDeviceMachines(map) {
            const grouped = new Map();
            Object.entries(map || {}).forEach(([ip, name]) => {
                const cleanIp = String(ip || "").trim();
                const cleanName = String(name || "").trim();
                if (!cleanIp || !cleanName) return;
                const key = cleanName.toLowerCase();
                if (!grouped.has(key)) {
                    grouped.set(key, {
                        machine_name: cleanName,
                        addresses: [],
                        last_seen: "-",
                        owner: "-",
                    });
                }
                grouped.get(key).addresses.push(cleanIp);
            });
            return Array.from(grouped.values()).map(normalizeMachine).sort((a, b) => {
                return a.machine_name.localeCompare(b.machine_name) || a.addresses.join(",").localeCompare(b.addresses.join(","));
            });
        }

        function flattenDeviceMachines(items) {
            const rows = [];
            (Array.isArray(items) ? items : []).map(normalizeMachine).forEach((machine) => {
                machine.addresses.forEach((ip) => {
                    rows.push({ name: machine.machine_name, ip, owner: machine.owner === "-" ? "" : machine.owner });
                });
            });
            return rows;
        }

        function serializeDeviceRows(rows) {
            const normalized = (rows || []).map((row) => ({
                ip: String(row.ip || "").trim(),
                name: String(row.name || "").trim(),
                owner: String(row.owner || "").trim(),
            }));
            normalized.sort((a, b) => a.ip.localeCompare(b.ip) || a.name.localeCompare(b.name) || a.owner.localeCompare(b.owner));
            return JSON.stringify(normalized);
        }

        function isDeviceMapDirty() {
            return serializeDeviceRows(collectDeviceMapRows()) !== deviceMapBaseline;
        }

        function syncDeviceMapSaveVisibility() {
            setSaveVisibility(saveDeviceMapBtn, isDeviceMapDirty());
        }

        function resolveSelectedCsvFile() {
            return selectedCsvFile || csvInput?.files?.[0] || null;
        }

        function updateSummaryFromSnapshot(card, snapshot) {
            const nameEl = card.querySelector("[data-device-machine-name]");
            const lastSeenEl = card.querySelector("[data-device-last-seen]");
            const ownerEl = card.querySelector("[data-device-owner]");
            const addressesEl = card.querySelector("[data-device-addresses]");
            if (nameEl) nameEl.textContent = snapshot.machine_name || "Unnamed machine";
            if (lastSeenEl) lastSeenEl.textContent = snapshot.last_seen || "-";
            if (ownerEl) ownerEl.textContent = snapshot.owner || "-";
            if (addressesEl) {
                addressesEl.innerHTML = "";
                if (!snapshot.addresses.length) {
                    const empty = document.createElement("span");
                    empty.className = "device-machine-muted";
                    empty.textContent = "No addresses";
                    addressesEl.appendChild(empty);
                } else {
                    snapshot.addresses.forEach((address) => {
                        const pill = document.createElement("span");
                        pill.className = "device-machine-address-pill";
                        pill.textContent = address;
                        addressesEl.appendChild(pill);
                    });
                }
            }
        }

        function readCardSnapshot(card) {
            const base = normalizeMachine(JSON.parse(card.dataset.machineSnapshot || "{}"));
            const nameInput = card.querySelector("[data-device-edit-name]");
            const ownerInput = card.querySelector("[data-device-edit-owner]");
            const addressInput = card.querySelector("[data-device-edit-addresses]");
            return {
                machine_name: String(nameInput?.value || "").trim(),
                addresses: parseAddressLines(addressInput?.value || ""),
                last_seen: base.last_seen,
                owner: String(ownerInput?.value || "").trim() || "-",
            };
        }

        function setCardEditing(card, isEditing) {
            const editor = card.querySelector("[data-device-editor]");
            const editBtn = card.querySelector("[data-device-edit]");
            const deleteBtn = card.querySelector("[data-device-delete]");
            if (editor) editor.hidden = !isEditing;
            if (editBtn) editBtn.textContent = isEditing ? "Save" : "Edit";
            if (deleteBtn) {
                deleteBtn.textContent = isEditing ? "Cancel" : "Delete";
                deleteBtn.className = isEditing ? "btn-secondary" : "btn-stop";
            }
            card.dataset.editing = isEditing ? "true" : "false";
        }

        function addDeviceRow(machine, options = {}) {
            if (!deviceMapBody) return;
            const snapshot = normalizeMachine(machine);
            const card = document.createElement("article");
            card.className = "device-machine-card";
            card.setAttribute("role", "listitem");
            card.dataset.machineSnapshot = JSON.stringify(snapshot);
            card.dataset.isNew = options.isNew ? "true" : "false";
            card.dataset.editing = "false";
            card.innerHTML = `
                <div class="device-machine-summary">
                    <div class="device-machine-cell device-machine-name" data-device-machine-name></div>
                    <div class="device-machine-cell device-machine-addresses" data-device-addresses></div>
                    <div class="device-machine-cell device-machine-muted" data-device-last-seen></div>
                    <div class="device-machine-cell device-machine-muted" data-device-owner></div>
                    <div class="device-machine-cell device-machine-actions">
                        <button type="button" class="btn-backup" data-device-edit>Edit</button>
                        <button type="button" class="btn-stop" data-device-delete>Delete</button>
                    </div>
                </div>
                <div class="device-machine-editor" data-device-editor hidden>
                    <div class="device-machine-editor-grid">
                        <label>
                            <span class="settings-label">Machine name</span>
                            <input class="ui-card-input" type="text" data-device-edit-name>
                        </label>
                        <label>
                            <span class="settings-label">Owner</span>
                            <input class="ui-card-input" type="text" data-device-edit-owner>
                        </label>
                    </div>
                    <label>
                        <span class="settings-label">Addresses</span>
                        <textarea class="ui-card-input" data-device-edit-addresses placeholder="One IP per line or comma-separated"></textarea>
                    </label>
                    <p class="device-machine-editor-note">Save commits the row locally. Use Save Mappings to persist all machine changes.</p>
                </div>
            `;

            const nameInput = card.querySelector("[data-device-edit-name]");
            const ownerInput = card.querySelector("[data-device-edit-owner]");
            const addressInput = card.querySelector("[data-device-edit-addresses]");
            const editBtn = card.querySelector("[data-device-edit]");
            const deleteBtn = card.querySelector("[data-device-delete]");

            if (nameInput) nameInput.value = snapshot.machine_name;
            if (ownerInput) ownerInput.value = snapshot.owner;
            if (addressInput) addressInput.value = snapshot.addresses.join("\n");
            updateSummaryFromSnapshot(card, snapshot);

            editBtn?.addEventListener("click", () => {
                const isEditing = card.dataset.editing === "true";
                if (!isEditing) {
                    setCardEditing(card, true);
                    nameInput?.focus();
                    return;
                }
                const nextSnapshot = readCardSnapshot(card);
                card.dataset.machineSnapshot = JSON.stringify(nextSnapshot);
                updateSummaryFromSnapshot(card, nextSnapshot);
                setCardEditing(card, false);
                syncDeviceMapSaveVisibility();
            });

            deleteBtn?.addEventListener("click", () => {
                const isEditing = card.dataset.editing === "true";
                if (isEditing) {
                    if (card.dataset.isNew === "true") {
                        card.remove();
                    } else {
                        const original = normalizeMachine(JSON.parse(card.dataset.machineSnapshot || "{}"));
                        if (nameInput) nameInput.value = original.machine_name;
                        if (ownerInput) ownerInput.value = original.owner;
                        if (addressInput) addressInput.value = original.addresses.join("\n");
                        updateSummaryFromSnapshot(card, original);
                        setCardEditing(card, false);
                    }
                    syncDeviceMapSaveVisibility();
                    return;
                }
                card.remove();
                syncDeviceMapSaveVisibility();
            });

            [nameInput, ownerInput, addressInput].forEach((input) => {
                input?.addEventListener("input", syncDeviceMapSaveVisibility);
            });

            deviceMapBody.appendChild(card);

            if (options.isNew) {
                setCardEditing(card, true);
                nameInput?.focus();
            }
        }

        function renderDeviceMapRows(map, options = {}) {
            if (!deviceMapBody) return;
            const nextMachines = Array.isArray(config.deviceMachines) && config.deviceMachines.length
                ? config.deviceMachines.map(normalizeMachine)
                : buildDeviceMachines(map);
            if (options.updateBaseline !== false) {
                deviceMapBaseline = serializeDeviceRows(flattenDeviceMachines(nextMachines));
            }
            deviceMapBody.innerHTML = "";
            if (!nextMachines.length) {
                addDeviceRow({ machine_name: "", addresses: [], last_seen: "-", owner: "-" }, { isNew: true });
                syncDeviceMapSaveVisibility();
                return;
            }
            nextMachines.forEach((machine) => addDeviceRow(machine));
            syncDeviceMapSaveVisibility();
        }

        function collectDeviceMapRows() {
            const rows = [];
            if (!deviceMapBody) return rows;
            deviceMapBody.querySelectorAll(".device-machine-card").forEach((card) => {
                const snapshot = card.dataset.editing === "true"
                    ? readCardSnapshot(card)
                    : normalizeMachine(JSON.parse(card.dataset.machineSnapshot || "{}"));
                if (!snapshot.machine_name && !snapshot.addresses.length) return;
                snapshot.addresses.forEach((ip) => rows.push({ name: snapshot.machine_name, ip, owner: snapshot.owner === "-" ? "" : snapshot.owner }));
            });
            return rows;
        }

        function validateDeviceMapRows(rows) {
            const seenIps = new Set();
            for (const row of rows) {
                if (!row.name || !row.ip) {
                    return "Each machine needs a name and at least one IP address.";
                }
                if (seenIps.has(row.ip)) {
                    return `Duplicate IP detected: ${row.ip}`;
                }
                seenIps.add(row.ip);
            }
            return "";
        }

        async function postJson(path, payload) {
            if (!http || typeof http.postJson !== "function") return null;
            const csrfToken = String(config.csrfToken || "").trim();
            return http.postJson(path, payload, { csrfToken });
        }

        async function postForm(path, formData) {
            if (!http || typeof http.postForm !== "function") return null;
            const csrfToken = String(config.csrfToken || "").trim();
            return http.postForm(path, formData, { csrfToken });
        }

        function schedulePageReload(message) {
            setStatus(message || "Reloading page...", "ok");
            window.setTimeout(() => {
                window.location.reload();
            }, 1200);
        }

        function scheduleAppReload() {
            setStatus("Rebooting app...", "ok");
            window.setTimeout(() => {
                window.location.reload();
            }, 1500);
        }

        function saveSecurity() {
            withAdminPassword(async (password) => {
                const payload = {
                    sudo_password: password,
                    require_password: !!requirePasswordInput?.checked,
                    new_password: String(newPasswordInput?.value || ""),
                    new_password_confirm: String(newPasswordConfirmInput?.value || ""),
                    new_superadmin_password: String(newSuperadminPasswordInput?.value || ""),
                    new_superadmin_password_confirm: String(newSuperadminPasswordConfirmInput?.value || ""),
                };
                setStatus("Saving security settings...", "");
                const result = await postJson("/panel-settings/security", payload);
                if (!result || !result.response) {
                    setStatus("Failed to save security settings.", "error");
                    return;
                }
                const body = result.payload || {};
                if (!result.response.ok || body.ok === false) {
                    setStatus(body.message || "Failed to save security settings.", "error");
                    return;
                }
                schedulePageReload(body.message || "Security settings saved.");
            });
        }

        function savePathAndTimezoneSettings() {
            withAdminPassword(async (password) => {
                const payload = {
                    sudo_password: password,
                    display_tz: String(displayTzSelect?.value || ""),
                    minecraft_root_dir: String(minecraftRootInput?.value || ""),
                    backup_dir: String(backupDirInput?.value || ""),
                    create_backup_dir: !!createBackupDirInput?.checked,
                };
                setStatus("Saving path and timezone settings...", "");
                const result = await postJson("/panel-settings/paths", payload);
                if (!result || !result.response) {
                    setStatus("Failed to save settings.", "error");
                    return;
                }
                const body = result.payload || {};
                if (!result.response.ok || body.ok === false) {
                    setStatus(body.message || "Failed to save settings.", "error");
                    return;
                }
                schedulePageReload(body.message || "Settings saved.");
            });
        }

        function rebootApp() {
            withAdminPassword(async (password) => {
                setStatus("Rebooting app...", "");
                const result = await postJson("/panel-settings/reboot", { sudo_password: password });
                if (!result || !result.response) {
                    setStatus("Failed to reboot app.", "error");
                    return;
                }
                const body = result.payload || {};
                if (!result.response.ok || body.ok === false) {
                    setStatus(body.message || "Failed to reboot app.", "error");
                    return;
                }
                scheduleAppReload();
            });
        }

        function saveDeviceMap() {
            withAdminPassword(async (password) => {
                const rows = collectDeviceMapRows();
                const error = validateDeviceMapRows(rows);
                if (error) {
                    setStatus(error, "error");
                    return;
                }
                setStatus("Saving device map...", "");
                const result = await postJson("/panel-settings/device-map/save", { sudo_password: password, rows });
                if (!result || !result.response) {
                    setStatus("Failed to save device map.", "error");
                    return;
                }
                const body = result.payload || {};
                if (!result.response.ok || body.ok === false) {
                    setStatus(body.message || "Failed to save device map.", "error");
                    return;
                }
                config.deviceMap = body.device_map || {};
                config.deviceMachines = Array.isArray(body.device_machines) ? body.device_machines.map(normalizeMachine) : buildDeviceMachines(config.deviceMap);
                renderDeviceMapRows(config.deviceMap);
                setStatus(body.message || "Device map saved.", "ok");
            });
        }

        function promptConflictResolution(conflicts) {
            return new Promise((resolve) => {
                const modal = document.createElement("div");
                modal.className = "modal-overlay open";
                modal.setAttribute("aria-hidden", "false");
                modal.innerHTML = `
                    <div class="modal-card" role="dialog" aria-modal="true">
                        <h3 class="modal-title">Device Map Conflicts</h3>
                        <p class="modal-text">${conflicts.length} conflicts found while importing the CSV.</p>
                        <div class="modal-actions">
                            <button type="button" data-action="cancel" class="btn-secondary">Cancel</button>
                            <button type="button" data-action="use_existing" class="btn-secondary">Use Existing</button>
                            <button type="button" data-action="skip" class="btn-secondary">Skip Conflicts</button>
                            <button type="button" data-action="overwrite" class="btn-backup">Overwrite</button>
                        </div>
                    </div>
                `;
                document.body.appendChild(modal);
                function cleanup(action) {
                    modal.remove();
                    resolve(action === "cancel" ? null : action);
                }
                modal.addEventListener("click", (event) => {
                    if (event.target === modal) cleanup("cancel");
                });
                modal.querySelectorAll("button[data-action]").forEach((btn) => {
                    btn.addEventListener("click", () => cleanup(btn.getAttribute("data-action")));
                });
            });
        }

        function showDeviceMapImportPreview(changes) {
            const modal = document.createElement("div");
            modal.className = "modal-overlay open";
            modal.setAttribute("aria-hidden", "false");
            const listItems = changes.length
                ? changes.map((entry) => `<li>${entry}</li>`).join("")
                : "<li>No new mappings were added.</li>";
            const summary = changes.length
                ? `${changes.length} mapping(s) were added or updated.`
                : "No new mappings were added.";
            modal.innerHTML = `
                <div class="modal-card" role="dialog" aria-modal="true">
                    <h3 class="modal-title">CSV Import Preview</h3>
                    <p class="modal-text">${summary}</p>
                    <ul class="modal-list">${listItems}</ul>
                    <div class="modal-actions">
                        <button type="button" class="btn-backup">OK</button>
                    </div>
                </div>
            `;
            const close = () => modal.remove();
            modal.addEventListener("click", (event) => {
                if (event.target === modal) close();
            });
            const okBtn = modal.querySelector("button");
            if (okBtn) okBtn.addEventListener("click", close);
            document.body.appendChild(modal);
        }

        function uploadDeviceCsv() {
            withAdminPassword(async (password) => {
                const file = resolveSelectedCsvFile();
                if (!file) {
                    setStatus("Select a CSV file first.", "error");
                    return;
                }
                const previousMap = Object.assign({}, config.deviceMap || {});
                const mode = String(csvModeSelect?.value || "append");
                setStatus("Uploading CSV...", "");
                const formData = new FormData();
                formData.append("file", file);
                formData.append("mode", mode);
                formData.append("sudo_password", password);
                let result = await postForm("/panel-settings/device-map/import", formData);
                if (!result || !result.response) {
                    setStatus("Failed to import device map.", "error");
                    return;
                }
                let body = result.payload || {};
                if (result.response.status === 409 && body.error === "conflict") {
                    const resolution = await promptConflictResolution(body.conflicts || []);
                    if (!resolution) {
                        setStatus("Import cancelled.", "error");
                        return;
                    }
                    const retryData = new FormData();
                    retryData.append("file", file);
                    retryData.append("mode", mode);
                    retryData.append("resolution", resolution);
                    retryData.append("sudo_password", password);
                    result = await postForm("/panel-settings/device-map/import", retryData);
                    body = result && result.payload ? result.payload : {};
                }
                if (!result.response.ok || body.ok === false) {
                    setStatus(body.message || "Failed to import device map.", "error");
                    return;
                }
                const nextMap = body.device_map || {};
                const changes = Object.keys(nextMap).sort().reduce((acc, ip) => {
                    const prev = String(previousMap[ip] || "");
                    const next = String(nextMap[ip] || "");
                    if (!prev && next) {
                        acc.push(`${next} - ${ip}`);
                    } else if (prev && next && prev !== next) {
                        acc.push(`${next} - ${ip} (was ${prev})`);
                    }
                    return acc;
                }, []);
                config.deviceMap = nextMap;
                config.deviceMachines = Array.isArray(body.device_machines) ? body.device_machines.map(normalizeMachine) : buildDeviceMachines(nextMap);
                renderDeviceMapRows(config.deviceMap);
                setStatus(body.message || "Device map imported.", "ok");
                showDeviceMapImportPreview(changes);
            });
        }

        function refreshAllStates() {
            setStatus("Refreshing all panel states...", "ok");
            if (shell && typeof shell.refreshAllStates === "function") {
                shell.refreshAllStates();
                return;
            }
            window.location.reload();
        }

        if (saveSecurityBtn) saveSecurityBtn.addEventListener("click", saveSecurity);
        if (savePathsBtn) savePathsBtn.addEventListener("click", savePathAndTimezoneSettings);
        if (saveTimezoneBtn) saveTimezoneBtn.addEventListener("click", savePathAndTimezoneSettings);
        if (rebootBtn) rebootBtn.addEventListener("click", rebootApp);
        if (refreshStatesBtn) refreshStatesBtn.addEventListener("click", refreshAllStates);
        if (addDeviceRowBtn) {
            addDeviceRowBtn.addEventListener("click", () => {
                addDeviceRow({ machine_name: "", addresses: [], last_seen: "-", owner: "-" }, { isNew: true });
                syncDeviceMapSaveVisibility();
            });
        }
        if (saveDeviceMapBtn) saveDeviceMapBtn.addEventListener("click", saveDeviceMap);
        if (uploadCsvBtn) uploadCsvBtn.addEventListener("click", uploadDeviceCsv);

        if (requirePasswordInput) requirePasswordInput.addEventListener("change", syncSecuritySaveVisibility);
        if (newPasswordInput) newPasswordInput.addEventListener("input", syncSecuritySaveVisibility);
        if (newPasswordConfirmInput) newPasswordConfirmInput.addEventListener("input", syncSecuritySaveVisibility);
        if (newSuperadminPasswordInput) newSuperadminPasswordInput.addEventListener("input", syncSecuritySaveVisibility);
        if (newSuperadminPasswordConfirmInput) newSuperadminPasswordConfirmInput.addEventListener("input", syncSecuritySaveVisibility);

        if (minecraftRootInput) minecraftRootInput.addEventListener("input", syncPathsSaveVisibility);
        if (backupDirInput) backupDirInput.addEventListener("input", syncPathsSaveVisibility);
        if (createBackupDirInput) createBackupDirInput.addEventListener("change", syncPathsSaveVisibility);
        if (displayTzSelect) displayTzSelect.addEventListener("change", syncTimezoneSaveVisibility);

        if (csvDropzone && csvInput) {
            const clearDrag = () => csvDropzone.classList.remove("dragover");
            csvDropzone.addEventListener("dragover", (event) => {
                event.preventDefault();
                csvDropzone.classList.add("dragover");
            });
            csvDropzone.addEventListener("dragleave", () => clearDrag());
            csvDropzone.addEventListener("drop", (event) => {
                event.preventDefault();
                clearDrag();
                const file = event.dataTransfer?.files?.[0];
                if (!file) return;
                selectedCsvFile = file;
                try {
                    const dt = new DataTransfer();
                    dt.items.add(file);
                    csvInput.files = dt.files;
                } catch (_) {
                    // Some browsers block programmatic file assignment.
                }
                csvInput.dispatchEvent(new Event("change", { bubbles: true }));
            });
            csvInput.addEventListener("change", () => {
                selectedCsvFile = csvInput.files?.[0] || null;
            });
        }

        if (!Array.isArray(config.deviceMachines) || !config.deviceMachines.length) {
            config.deviceMachines = buildDeviceMachines(config.deviceMap || {});
        }
        renderDeviceMapRows(config.deviceMap || {});
        syncSecuritySaveVisibility();
        syncPathsSaveVisibility();
        syncTimezoneSaveVisibility();

        return function cleanup() {
            // No-op cleanup; DOM is replaced on navigation.
        };
    }

    if (pageModules && typeof pageModules.register === "function") {
        pageModules.register("panel_settings", { mount: mountPanelSettings });
    } else {
        mountPanelSettings();
    }
})();

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

        function serializeDeviceMap(map) {
            const entries = Object.entries(map || {}).map(([ip, name]) => ({
                ip: String(ip || "").trim(),
                name: String(name || "").trim(),
            }));
            entries.sort((a, b) => a.ip.localeCompare(b.ip) || a.name.localeCompare(b.name));
            return JSON.stringify(entries);
        }

        function serializeDeviceRows(rows) {
            const normalized = (rows || []).map((row) => ({
                ip: String(row.ip || "").trim(),
                name: String(row.name || "").trim(),
            }));
            normalized.sort((a, b) => a.ip.localeCompare(b.ip) || a.name.localeCompare(b.name));
            return JSON.stringify(normalized);
        }

        function isDeviceMapDirty() {
            const snapshot = serializeDeviceRows(collectDeviceMapRows());
            return snapshot !== deviceMapBaseline;
        }

        function syncDeviceMapSaveVisibility() {
            setSaveVisibility(saveDeviceMapBtn, isDeviceMapDirty());
        }

        function resolveSelectedCsvFile() {
            return selectedCsvFile || csvInput?.files?.[0] || null;
        }

        function renderDeviceMapRows(map, options = {}) {
            if (!deviceMapBody) return;
            if (options.updateBaseline !== false) {
                deviceMapBaseline = serializeDeviceMap(map);
            }
            deviceMapBody.innerHTML = "";
            const entries = Object.entries(map || {}).sort((a, b) => a[0].localeCompare(b[0]));
            if (!entries.length) {
                addDeviceRow("", "");
                syncDeviceMapSaveVisibility();
                return;
            }
            entries.forEach(([ip, name]) => addDeviceRow(name, ip));
            syncDeviceMapSaveVisibility();
        }

        function addDeviceRow(name, ip) {
            if (!deviceMapBody) return;
            const row = document.createElement("tr");
            const nameCell = document.createElement("td");
            const ipCell = document.createElement("td");
            const actionCell = document.createElement("td");

            const nameInput = document.createElement("input");
            nameInput.className = "ui-text-input";
            nameInput.type = "text";
            nameInput.value = name || "";

            const ipInput = document.createElement("input");
            ipInput.className = "ui-text-input";
            ipInput.type = "text";
            ipInput.value = ip || "";

            const removeBtn = document.createElement("button");
            removeBtn.type = "button";
            removeBtn.className = "btn-stop";
            removeBtn.textContent = "Remove";
            removeBtn.addEventListener("click", () => {
                row.remove();
                syncDeviceMapSaveVisibility();
            });

            nameCell.appendChild(nameInput);
            ipCell.appendChild(ipInput);
            actionCell.appendChild(removeBtn);

            row.appendChild(nameCell);
            row.appendChild(ipCell);
            row.appendChild(actionCell);
            deviceMapBody.appendChild(row);

            nameInput.addEventListener("input", syncDeviceMapSaveVisibility);
            ipInput.addEventListener("input", syncDeviceMapSaveVisibility);
        }

        function collectDeviceMapRows() {
            const rows = [];
            if (!deviceMapBody) return rows;
            deviceMapBody.querySelectorAll("tr").forEach((row) => {
                const inputs = row.querySelectorAll("input");
                const name = String(inputs[0]?.value || "").trim();
                const ip = String(inputs[1]?.value || "").trim();
                if (!name && !ip) return;
                rows.push({ name, ip });
            });
            return rows;
        }

        function validateDeviceMapRows(rows) {
            for (const row of rows) {
                if (!row.name || !row.ip) {
                    return "Each device row needs both a name and an IP address.";
                }
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

        function showDeviceMapImportPreview(changes, totalIncoming = 0) {
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
                        acc.push(`${next} — ${ip}`);
                    } else if (prev && next && prev !== next) {
                        acc.push(`${next} — ${ip} (was ${prev})`);
                    }
                    return acc;
                }, []);
                config.deviceMap = nextMap;
                renderDeviceMapRows(config.deviceMap);
                setStatus(body.message || "Device map imported.", "ok");
                showDeviceMapImportPreview(changes, body.incoming || 0);
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
                addDeviceRow("", "");
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

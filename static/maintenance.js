document.addEventListener("DOMContentLoaded", () => {
    if (typeof window.initMaintenanceShell === "function") {
        window.initMaintenanceShell();
    }

    // DOM wiring and mutable page state for maintenance interactions.
    const csrfToken = document.getElementById("maintenance-csrf-token")?.value || "";
    const bootstrap = document.getElementById("maintenance-bootstrap-data");
    const fileList = document.getElementById("cleanup-file-list");
    const manualDryRunInput = document.getElementById("manual-dry-run");
    const manualDestructiveConfirmWrap = document.getElementById("manual-destructive-confirm-wrap");
    const manualDestructiveConfirmInput = document.getElementById("manual-destructive-confirm");
    const runManualDeleteBtn = document.getElementById("run-manual-delete-btn");
    const ruleRunDryRunInput = document.getElementById("rule-run-dry-run");
    const ruleRunDestructiveConfirmWrap = document.getElementById("rule-run-destructive-confirm-wrap");
    const ruleRunDestructiveConfirmInput = document.getElementById("rule-run-destructive-confirm");
    const runRuleDeleteBtn = document.getElementById("run-rule-delete-btn");
    const actionTitle = document.getElementById("maintenance-action-title");
    const actionDescription = document.getElementById("maintenance-action-description");
    const actionToolbar = document.getElementById("maintenance-action-toolbar");
    const actionContent = document.querySelector(".maintenance-action-content");
    const maintenanceFileListPane = document.querySelector(".maintenance-file-list");
    const rulesSaveBtn = document.getElementById("rules-save-btn");
    const rulesEditToggleBtn = document.getElementById("rules-edit-toggle-btn");
    const viewRules = document.getElementById("maintenance-view-rules");
    const viewManual = document.getElementById("maintenance-view-manual");
    const viewHistory = document.getElementById("maintenance-view-history");
    const rulesCardList = document.getElementById("rules-card-list");
    const historyCardList = document.getElementById("history-card-list");
    const openRulesBtn = document.getElementById("maint-open-rules");
    const openHistoryBtn = document.getElementById("maint-open-history");
    const openManualBtn = document.getElementById("maint-open-manual");
    const scopeBackupsBtn = document.getElementById("maint-scope-backups");
    const scopeStaleBtn = document.getElementById("maint-scope-stale");
    const runAcknowledgeBtn = document.getElementById("ack-non-normal-btn");
    const errorModal = document.getElementById("maintenance-error-modal");
    const errorText = document.getElementById("maintenance-error-text");
    const errorDetails = document.getElementById("maintenance-error-details");
    const passwordModal = document.getElementById("maintenance-password-modal");
    const passwordInput = document.getElementById("maintenance-password-input");
    const passwordSubmit = document.getElementById("maintenance-password-submit");
    const passwordCancel = document.getElementById("maintenance-password-cancel");
    const passwordText = document.getElementById("maintenance-password-text");
    const dryRunModal = document.getElementById("maintenance-dry-run-modal");
    const dryRunSummary = document.getElementById("maintenance-dry-run-summary");
    const dryRunFiles = document.getElementById("maintenance-dry-run-files");
    const dryRunIssues = document.getElementById("maintenance-dry-run-issues");
    const dryRunOk = document.getElementById("maintenance-dry-run-ok");
    const acknowledgeButtonHome = runAcknowledgeBtn?.parentElement || null;

    const UI_ONLY_MODE = false;
    const SCOPE_LABELS = { backups: "Backups", stale_worlds: "Stale Worlds" };
    const SCOPE_CATEGORIES = {
        backups: new Set(["backup_zip"]),
        stale_worlds: new Set(["stale_world_dir", "old_world_zip"]),
    };
    let currentActionView = "rules";
    let currentScope = parseDataAttr("scope", "backups");
    let pendingProtectedAction = null;
    let rulesEditMode = false;
    let rulesDraft = null;
    const RULE_FIELD_UPDATERS = {
        "age.days": (draft, value) => { draft.age.days = Math.max(7, Number(value || 7)); },
        "space.used_trigger_percent": (draft, value) => { draft.space.used_trigger_percent = Math.max(50, Math.min(100, Number(value || 80))); },
        "count.session_backups_to_keep": (draft, value) => { draft.count.session_backups_to_keep = Math.max(3, Number(value || 3)); },
        "count.manual_backups_to_keep": (draft, value) => { draft.count.manual_backups_to_keep = Math.max(3, Number(value || 3)); },
        "count.prerestore_backups_to_keep": (draft, value) => { draft.count.prerestore_backups_to_keep = Math.max(3, Number(value || 3)); },
        "count.max_per_category": (draft, value) => {
            const n = Math.max(3, Number(value || 3));
            draft.count.max_per_category = n;
            draft.count.session_backups_to_keep = n;
            draft.count.manual_backups_to_keep = n;
            draft.count.prerestore_backups_to_keep = n;
        },
        "time_based.enabled": (draft, value) => { draft.time_based.enabled = !!value; },
        "time_based.time_of_backup": (draft, value) => { draft.time_based.time_of_backup = String(value || "03:00"); },
        "time_based.repeat_mode": (draft, value) => { draft.time_based.repeat_mode = String(value || "does_not_repeat"); },
        "time_based.weekly_day": (draft, value) => { draft.time_based.weekly_day = String(value || "Sunday"); },
        "time_based.monthly_date": (draft, value) => { draft.time_based.monthly_date = Math.max(1, Math.min(31, Number(value || 1))); },
        "time_based.every_n_days": (draft, value) => { draft.time_based.every_n_days = Math.max(1, Math.min(365, Number(value || 1))); },
    };

    function devActionAlert(label, details = "") {
        const suffix = details ? `\n${details}` : "";
        window.alert(`[DEV MODE] ${label}${suffix}`);
    }

    function formatDevPayload(payload) {
        try {
            return JSON.stringify(payload, null, 2);
        } catch (_) {
            return String(payload);
        }
    }

    function parseDataAttr(name, fallback) {
        try {
            return JSON.parse(bootstrap?.dataset?.[name] || "");
        } catch (_) {
            return fallback;
        }
    }

    let config = parseDataAttr("config", {});
    let preview = parseDataAttr("preview", { items: [] });
    let nonNormal = parseDataAttr("nonNormal", { missed_runs: [] });
    let storage = parseDataAttr("storage", {});
    let cleanupHistory = parseDataAttr("history", { runs: [] });
    let nextRunAt = parseDataAttr("nextRun", "-");
    let deviceMap = parseDataAttr("deviceMap", {});

    function showError(message, details) {
        if (!errorModal || !errorText || !errorDetails) return;
        errorText.textContent = message || "Operation failed.";
        errorDetails.textContent = details ? (typeof details === "string" ? details : JSON.stringify(details, null, 2)) : "";
        errorModal.setAttribute("aria-hidden", "false");
    }

    function closeError() {
        if (!errorModal) return;
        errorModal.setAttribute("aria-hidden", "true");
    }

    function closeDryRunModal() {
        if (!dryRunModal) return;
        dryRunModal.setAttribute("aria-hidden", "true");
    }

    function appendModalListItem(listEl, text) {
        if (!listEl) return;
        const li = document.createElement("li");
        li.textContent = text;
        listEl.appendChild(li);
    }

    function showDryRunModal(previewPayload, actionKey) {
        if (!dryRunModal || !dryRunSummary || !dryRunFiles || !dryRunIssues) return;
        const previewData = (previewPayload && typeof previewPayload === "object") ? previewPayload : {};
        const items = Array.isArray(previewData.items) ? previewData.items : [];
        const selectedRows = items.filter((item) => !!item?.selected_for_delete);
        const errors = Array.isArray(previewData.errors) ? previewData.errors : [];
        const selectedIneligible = Array.isArray(previewData.selected_ineligible) ? previewData.selected_ineligible : [];
        const actionLabel = actionKey === "manual-delete" ? "Manual cleanup dry run" : "Rule cleanup dry run";
        const requested = Number(previewData.requested_delete_count || 0);
        const capped = Number(previewData.capped_delete_count || 0);
        dryRunSummary.textContent = `${actionLabel}: ${selectedRows.length} file(s) would be deleted (requested ${requested}, capped ${capped}).`;

        dryRunFiles.innerHTML = "";
        if (selectedRows.length === 0) {
            appendModalListItem(dryRunFiles, "No files would be deleted.");
        } else {
            selectedRows.forEach((item) => {
                const reasons = Array.isArray(item?.reasons) && item.reasons.length > 0 ? item.reasons.join(", ") : "no reason";
                appendModalListItem(dryRunFiles, `${item?.name || item?.path || "unknown"} | ${item?.category || "-"} | ${reasons}`);
            });
        }

        dryRunIssues.innerHTML = "";
        if (errors.length === 0 && selectedIneligible.length === 0) {
            appendModalListItem(dryRunIssues, "No errors or issues reported.");
        } else {
            errors.forEach((entry) => appendModalListItem(dryRunIssues, `Error: ${String(entry)}`));
            selectedIneligible.forEach((entry) => appendModalListItem(dryRunIssues, `Ineligible selection: ${String(entry)}`));
        }

        dryRunModal.setAttribute("aria-hidden", "false");
    }

    function getRuleFieldWarning(target) {
        if (!(target instanceof HTMLInputElement) && !(target instanceof HTMLSelectElement)) return "";
        if (target.disabled || target.validity.valid) return "";
        const field = target.getAttribute("data-rule-field") || "";
        if (target.validity.valueMissing) return "Value is required.";
        if (target.validity.badInput) return "Enter a valid number.";
        if (target.validity.rangeUnderflow) {
            if (field === "age.days") return "Minimum is 7 days.";
            if (field.startsWith("count.")) return "Minimum is 3.";
            if (field === "space.used_trigger_percent") return "Minimum is 50%.";
            return "Value is below the minimum.";
        }
        if (target.validity.rangeOverflow) {
            if (field === "space.used_trigger_percent") return "Maximum is 100%.";
            return "Value is above the maximum.";
        }
        if (target.validity.stepMismatch) return "Use a whole number.";
        if (target.validity.typeMismatch || target.validity.patternMismatch) return "Enter a valid value.";
        return "Invalid value.";
    }

    function setRuleFieldWarning(target) {
        if (!(target instanceof HTMLInputElement) && !(target instanceof HTMLSelectElement)) return;
        const row = target.closest(".rule-inline-sentence");
        if (!row) return;
        const warning = getRuleFieldWarning(target);
        let warningEl = row.querySelector(".rule-inline-warning");
        if (!warningEl) {
            warningEl = document.createElement("span");
            warningEl.className = "rule-inline-warning";
            warningEl.setAttribute("aria-live", "polite");
            row.appendChild(warningEl);
        }
        warningEl.textContent = warning;
        warningEl.hidden = !warning;
        target.classList.toggle("rule-input-invalid", !!warning);
    }

    function hasRuleFieldValidationErrors() {
        if (!rulesCardList) return false;
        const fields = rulesCardList.querySelectorAll("input[data-rule-field], select[data-rule-field]");
        let hasErrors = false;
        fields.forEach((field) => {
            setRuleFieldWarning(field);
            if ((field instanceof HTMLInputElement || field instanceof HTMLSelectElement) && !field.disabled && !field.validity.valid) {
                hasErrors = true;
            }
        });
        return hasErrors;
    }

    function humanBytes(bytes) {
        const n = Number(bytes || 0);
        if (!Number.isFinite(n)) return "0 B";
        if (n < 1024) return `${n} B`;
        if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
        if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
        return `${(n / (1024 * 1024 * 1024)).toFixed(3)} GB`;
    }

    function formatAuditTimestamp(raw) {
        const text = String(raw || "").trim();
        if (!text || text === "-") return "-";
        const parsed = new Date(text);
        if (Number.isNaN(parsed.getTime())) return text;
        return parsed.toLocaleString(undefined, {
            year: "numeric",
            month: "short",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hour12: true,
            timeZoneName: "short",
        });
    }

    function formatAuditActor(rawActor) {
        const actor = String(rawActor || "").trim();
        if (!actor || actor === "-") return "-";
        const map = (deviceMap && typeof deviceMap === "object") ? deviceMap : {};
        const deviceName = String(map[actor] || "").trim();
        if (deviceName) return `${deviceName} (${actor})`;
        return actor;
    }

    function syncMaintenanceOverflowState() {
        const actionHasVScroll = !!actionContent && (actionContent.scrollHeight > actionContent.clientHeight + 1);
        const fileListHasVScroll = !!maintenanceFileListPane && (maintenanceFileListPane.scrollHeight > maintenanceFileListPane.clientHeight + 1);
        if (actionContent) actionContent.classList.toggle("has-vscroll", actionHasVScroll);
        if (maintenanceFileListPane) maintenanceFileListPane.classList.toggle("has-vscroll", fileListHasVScroll);
        document.body.classList.toggle("maintenance-both-vscroll", actionHasVScroll && fileListHasVScroll);
    }

    function getMissedRuns() {
        const runs = Array.isArray(nonNormal?.missed_runs) ? nonNormal.missed_runs : [];
        return runs.filter((entry) => {
            if (!entry || typeof entry !== "object") return true;
            const explicitScope = String(entry.scope || "").trim().toLowerCase();
            if (explicitScope === "backups" || explicitScope === "stale_worlds") {
                return explicitScope === currentScope;
            }
            const scheduleId = String(entry.schedule_id || "").trim().toLowerCase();
            if (scheduleId.startsWith("backups:")) return currentScope === "backups";
            if (scheduleId.startsWith("stale_worlds:")) return currentScope === "stale_worlds";
            return true;
        });
    }

    function getHistoryRuns() {
        const runs = cleanupHistory?.runs;
        if (!Array.isArray(runs)) return [];
        return runs.filter((entry) => {
            const scope = String(entry?.scope || "");
            return !scope || scope === currentScope;
        });
    }

    function scopeLabel() {
        return SCOPE_LABELS[currentScope] || "Backups";
    }

    function getScopeCategories() {
        return SCOPE_CATEGORIES[currentScope] || SCOPE_CATEGORIES.backups;
    }

    function isItemInCurrentScope(item) {
        return getScopeCategories().has(String(item?.category || ""));
    }

    function setPressedState(button, isPressed) {
        if (!button) return;
        button.classList.toggle("active", !!isPressed);
        button.setAttribute("aria-pressed", isPressed ? "true" : "false");
    }

    function syncScopeButtons() {
        setPressedState(scopeBackupsBtn, currentScope === "backups");
        setPressedState(scopeStaleBtn, currentScope === "stale_worlds");
    }

    function syncScopeActionLabels() {
        const isStaleScope = currentScope === "stale_worlds";
        if (runRuleDeleteBtn) runRuleDeleteBtn.textContent = isStaleScope ? "Run Stale-World Rule Cleanup Now" : "Run Rule-Based Delete Now";
        if (runManualDeleteBtn) runManualDeleteBtn.textContent = isStaleScope ? "Confirm Manual Stale-World Cleanup" : "Confirm Manual Cleanup";
    }

    function renderSummaryPanels() {
        syncScopeActionLabels();
        renderFileList();
        renderHistory();
        renderStats();
    }

    function reasonText(reasons) {
        if (!Array.isArray(reasons) || reasons.length === 0) return "eligible";
        return reasons.join(", ");
    }

    function summarizeByCategory(category) {
        const items = Array.isArray(preview?.items) ? preview.items : [];
        let count = 0;
        let total = 0;
        items.forEach((item) => {
            if (item?.category !== category) return;
            count += 1;
            total += Number(item.size || 0);
        });
        return { count, total };
    }

    function renderStats() {
        const freeBytes = Number(storage?.free_bytes || 0);
        const backup = summarizeByCategory("backup_zip");
        const staleDirs = summarizeByCategory("stale_world_dir");
        const staleZips = summarizeByCategory("old_world_zip");
        const staleTotal = {
            count: staleDirs.count + staleZips.count,
            total: staleDirs.total + staleZips.total,
        };
        const ruleCount = config?.rules ? Object.keys(config.rules).length : 0;
        const scheduleCount = Array.isArray(config?.schedules) ? config.schedules.length : 0;

        const storageRemaining = document.getElementById("maint-storage-remaining");
        const backupSummary = document.getElementById("maint-backup-summary");
        const staleSummary = document.getElementById("maint-stale-summary");
        const rulesEl = document.getElementById("maint-rule-count");
        const schedulesEl = document.getElementById("maint-schedule-count");
        const nextRunEl = document.getElementById("maint-next-run");

        if (storageRemaining) storageRemaining.textContent = humanBytes(freeBytes);
        if (backupSummary) backupSummary.textContent = `${backup.count} files | ${humanBytes(backup.total)}`;
        if (staleSummary) staleSummary.textContent = `${staleTotal.count} entries | ${humanBytes(staleTotal.total)}`;
        if (rulesEl) rulesEl.textContent = String(ruleCount);
        if (schedulesEl) schedulesEl.textContent = String(scheduleCount);
        if (nextRunEl) nextRunEl.textContent = formatAuditTimestamp(nextRunAt || "-");
    }

    function getEffectiveRules() {
        const rules = config?.rules || {};
        const countMax = Number(rules?.count?.max_per_category ?? 30);
        const effective = {
            age: {
                days: Number(rules?.age?.days ?? 7),
            },
            count: {
                max_per_category: Number(rules?.count?.max_per_category ?? countMax),
                session_backups_to_keep: Number(rules?.count?.session_backups_to_keep ?? countMax),
                manual_backups_to_keep: Number(rules?.count?.manual_backups_to_keep ?? countMax),
                prerestore_backups_to_keep: Number(rules?.count?.prerestore_backups_to_keep ?? countMax),
            },
            space: {
                used_trigger_percent: Number(rules?.space?.used_trigger_percent ?? 80),
                cooldown_seconds: Number(rules?.space?.cooldown_seconds ?? 600),
            },
            time_based: {
                enabled: Boolean(rules?.time_based?.enabled ?? true),
                time_of_backup: String(rules?.time_based?.time_of_backup || "03:00"),
                repeat_mode: String(rules?.time_based?.repeat_mode || "does_not_repeat"),
                weekly_day: String(rules?.time_based?.weekly_day || "Sunday"),
                monthly_date: Number(rules?.time_based?.monthly_date ?? 1),
                every_n_days: Number(rules?.time_based?.every_n_days ?? 1),
            },
        };
        if (rulesEditMode && rulesDraft) {
            effective.age = { ...effective.age, ...(rulesDraft.age || {}) };
            effective.space = { ...effective.space, ...(rulesDraft.space || {}) };
            effective.count = { ...effective.count, ...(rulesDraft.count || {}) };
            effective.time_based = { ...effective.time_based, ...(rulesDraft.time_based || {}) };
        }
        return effective;
    }

    function beginRulesEdit() {
        rulesDraft = getEffectiveRules();
        rulesEditMode = true;
        renderRuleCards();
        renderFileList();
        syncRulesHeaderButtons();
    }

    function cancelRulesEdit() {
        rulesEditMode = false;
        rulesDraft = null;
        renderRuleCards();
        renderFileList();
        syncRulesHeaderButtons();
    }

    async function saveRulesEdit(sudoPassword = "") {
        if (!rulesEditMode || !rulesDraft) return false;
        if (hasRuleFieldValidationErrors()) {
            showError("Some rule values are invalid.", "Fix the highlighted fields before saving.");
            const firstInvalid = rulesCardList?.querySelector("input.rule-input-invalid, select.rule-input-invalid");
            if (firstInvalid && typeof firstInvalid.focus === "function") firstInvalid.focus();
            return false;
        }
        const nextRules = getEffectiveRules();
        const maxCount = Math.max(
            Number(nextRules.count.session_backups_to_keep ?? 3),
            Number(nextRules.count.manual_backups_to_keep ?? 3),
            Number(nextRules.count.prerestore_backups_to_keep ?? 3),
            3,
        );
        const payload = await apiPost("/maintenance/api/save-rules", {
            scope: currentScope,
            sudo_password: String(sudoPassword || ""),
            rules: {
                age: {
                    days: Math.max(7, Number(nextRules.age.days ?? 7)),
                },
                count: {
                    max_per_category: Math.max(3, Number(maxCount || 3)),
                    session_backups_to_keep: Math.max(3, Number(nextRules.count.session_backups_to_keep ?? 3)),
                    manual_backups_to_keep: Math.max(3, Number(nextRules.count.manual_backups_to_keep ?? 3)),
                    prerestore_backups_to_keep: Math.max(3, Number(nextRules.count.prerestore_backups_to_keep ?? 3)),
                },
                space: {
                    used_trigger_percent: Math.max(50, Math.min(100, Number(nextRules.space.used_trigger_percent ?? 80))),
                    cooldown_seconds: Number(nextRules.space.cooldown_seconds ?? 600),
                    free_space_below_gb: 0,
                },
                time_based: {
                    enabled: !!nextRules.time_based.enabled,
                    time_of_backup: String(nextRules.time_based.time_of_backup || "03:00"),
                    repeat_mode: String(nextRules.time_based.repeat_mode || "does_not_repeat"),
                    weekly_day: String(nextRules.time_based.weekly_day || "Sunday"),
                    monthly_date: Number(nextRules.time_based.monthly_date ?? 1),
                    every_n_days: Number(nextRules.time_based.every_n_days ?? 1),
                },
                caps: {
                    max_delete_files_absolute: 5,
                    max_delete_percent_eligible: 10,
                    max_delete_min_if_non_empty: 1,
                },
            },
        });
        config = payload.config || config;
        preview = payload.preview || preview;
        renderHistory();
        renderStats();
        cancelRulesEdit();
        return true;
    }

    // Render the rules pane from current scope + edit state.
    function renderRuleCards() {
        if (!rulesCardList) return;
        rulesCardList.innerHTML = "";
        const effective = getEffectiveRules();
        const inputDisabled = rulesEditMode ? "" : "disabled";
        const ageDays = Number(effective?.age?.days ?? 7);
        const usedTrigger = Number(effective?.space?.used_trigger_percent ?? 80);
        const sessionKeep = Number(effective?.count?.session_backups_to_keep ?? 30);
        const manualKeep = Number(effective?.count?.manual_backups_to_keep ?? 30);
        const prerestoreKeep = Number(effective?.count?.prerestore_backups_to_keep ?? 30);
        const maxPerCategory = Number(effective?.count?.max_per_category ?? Math.max(sessionKeep, manualKeep, prerestoreKeep));
        const timeEnabled = !!effective?.time_based?.enabled;
        const backupTime = String(effective?.time_based?.time_of_backup || "03:00");
        const repeatMode = String(effective?.time_based?.repeat_mode || "does_not_repeat");
        const weeklyDay = String(effective?.time_based?.weekly_day || "Sunday");
        const monthlyDate = Number(effective?.time_based?.monthly_date ?? 1);
        const everyNDays = Number(effective?.time_based?.every_n_days ?? 1);
        const isStaleScope = currentScope === "stale_worlds";
        const repeatLabelMap = {
            does_not_repeat: "Does not repeat",
            daily: "Daily",
            weekly: `Weekly on ${weeklyDay}`,
            monthly: `Monthly on ${monthlyDate}`,
            weekdays: "Every weekday (Monday to Friday)",
            every_n_days: `Every ${everyNDays} day`,
        };
        const repeatLabel = !timeEnabled ? "Disabled" : (repeatLabelMap[repeatMode] || "Does not repeat");

        const item = document.createElement("article");
        item.className = "maintenance-card";
        item.innerHTML = `
            <div class="rule-section">
                <h3 class="maintenance-card-title">Age Rule</h3>
                <p class="rule-inline-sentence">
                    <span class="rule-inline-label">Minimum age to start deleting (Days)</span>
                    ${rulesEditMode
                        ? `<input class="ui-text-input" type="number" min="7" step="1" value="${ageDays}" data-rule-field="age.days" ${inputDisabled}>`
                        : `<span class="rule-inline-value">${ageDays}</span>`
                    }
                </p>
            </div>
            <div class="rule-section">
                <h3 class="maintenance-card-title">Space Rule</h3>
                <p class="rule-inline-sentence">
                    <span class="rule-inline-label">Max disk usage before deleting starts (% used)</span>
                    ${rulesEditMode
                        ? `<input class="ui-text-input" type="number" min="50" max="100" step="1" value="${usedTrigger}" data-rule-field="space.used_trigger_percent" ${inputDisabled}>`
                        : `<span class="rule-inline-value">${usedTrigger}%</span>`
                    }
                </p>
            </div>
            <div class="rule-section">
                <h3 class="maintenance-card-title">Count Rule</h3>
                ${isStaleScope
                    ? `
                        <p class="rule-inline-sentence">
                            <span class="rule-inline-label">Newest stale world entries to keep</span>
                            ${rulesEditMode
                                ? `<input class="ui-text-input" type="number" min="3" step="1" value="${maxPerCategory}" data-rule-field="count.max_per_category" ${inputDisabled}>`
                                : `<span class="rule-inline-value">${maxPerCategory}</span>`
                            }
                        </p>
                    `
                    : `
                        <p class="rule-inline-sentence">
                            <span class="rule-inline-label">Newest Session backups to keep</span>
                            ${rulesEditMode
                                ? `<input class="ui-text-input" type="number" min="3" step="1" value="${sessionKeep}" data-rule-field="count.session_backups_to_keep" ${inputDisabled}>`
                                : `<span class="rule-inline-value">${sessionKeep}</span>`
                            }
                        </p>
                        <p class="rule-inline-sentence">
                            <span class="rule-inline-label">Newest Manual backups to keep</span>
                            ${rulesEditMode
                                ? `<input class="ui-text-input" type="number" min="3" step="1" value="${manualKeep}" data-rule-field="count.manual_backups_to_keep" ${inputDisabled}>`
                                : `<span class="rule-inline-value">${manualKeep}</span>`
                            }
                        </p>
                        <p class="rule-inline-sentence">
                            <span class="rule-inline-label">Newest Prerestore backups to keep</span>
                            ${rulesEditMode
                                ? `<input class="ui-text-input" type="number" min="3" step="1" value="${prerestoreKeep}" data-rule-field="count.prerestore_backups_to_keep" ${inputDisabled}>`
                                : `<span class="rule-inline-value">${prerestoreKeep}</span>`
                            }
                        </p>
                    `
                }
            </div>
            <div class="rule-section">
                <h3 class="maintenance-card-title">Time Based Rule</h3>
                <p class="rule-inline-sentence">
                    <span class="rule-inline-label">Enable time based cleanup</span>
                    ${rulesEditMode
                        ? `<input class="rule-inline-control" type="checkbox" data-rule-field="time_based.enabled" ${timeEnabled ? "checked" : ""}>`
                        : `<span class="rule-inline-value rule-inline-value-wide">${timeEnabled ? "Enabled" : "Disabled"}</span>`
                    }
                </p>
                <p class="rule-inline-sentence">
                    <span class="rule-inline-label">Time of cleanup</span>
                    ${rulesEditMode
                        ? `<input class="ui-text-input rule-inline-control" type="time" value="${backupTime}" data-rule-field="time_based.time_of_backup" ${!timeEnabled ? "disabled" : inputDisabled}>`
                        : `<span class="rule-inline-value rule-inline-value-wide">${backupTime}</span>`
                    }
                </p>
                <p class="rule-inline-sentence">
                    <span class="rule-inline-label">Repeat</span>
                    ${rulesEditMode
                        ? `
                            <select class="rule-inline-control" data-rule-field="time_based.repeat_mode" ${!timeEnabled ? "disabled" : inputDisabled}>
                                <option value="does_not_repeat"${repeatMode === "does_not_repeat" ? " selected" : ""}>Does not repeat</option>
                                <option value="daily"${repeatMode === "daily" ? " selected" : ""}>Daily</option>
                                <option value="weekly"${repeatMode === "weekly" ? " selected" : ""}>Weekly on &lt;day&gt;</option>
                                <option value="monthly"${repeatMode === "monthly" ? " selected" : ""}>Monthly on &lt;date&gt;</option>
                                <option value="weekdays"${repeatMode === "weekdays" ? " selected" : ""}>Every weekday (Monday to Friday)</option>
                                <option value="every_n_days"${repeatMode === "every_n_days" ? " selected" : ""}>Every &lt;int&gt; day</option>
                            </select>
                        `
                        : `<span class="rule-inline-value rule-inline-value-wide">${repeatLabel}</span>`
                    }
                </p>
                ${rulesEditMode && timeEnabled && repeatMode === "weekly"
                    ? `
                        <p class="rule-inline-sentence">
                            <span class="rule-inline-label">Day:</span>
                            <select class="rule-inline-control" data-rule-field="time_based.weekly_day" ${inputDisabled}>
                                <option value="Sunday"${weeklyDay === "Sunday" ? " selected" : ""}>Sunday</option>
                                <option value="Monday"${weeklyDay === "Monday" ? " selected" : ""}>Monday</option>
                                <option value="Tuesday"${weeklyDay === "Tuesday" ? " selected" : ""}>Tuesday</option>
                                <option value="Wednesday"${weeklyDay === "Wednesday" ? " selected" : ""}>Wednesday</option>
                                <option value="Thursday"${weeklyDay === "Thursday" ? " selected" : ""}>Thursday</option>
                                <option value="Friday"${weeklyDay === "Friday" ? " selected" : ""}>Friday</option>
                                <option value="Saturday"${weeklyDay === "Saturday" ? " selected" : ""}>Saturday</option>
                            </select>
                        </p>
                    `
                    : ""
                }
                ${rulesEditMode && timeEnabled && repeatMode === "monthly"
                    ? `
                        <p class="rule-inline-sentence">
                            <span class="rule-inline-label">Date (1-31):</span>
                            <input class="ui-text-input rule-inline-control" type="number" min="1" max="31" step="1" value="${monthlyDate}" data-rule-field="time_based.monthly_date" ${inputDisabled}>
                        </p>
                    `
                    : ""
                }
                ${rulesEditMode && timeEnabled && repeatMode === "every_n_days"
                    ? `
                        <p class="rule-inline-sentence">
                            <span class="rule-inline-label">Every N day(s):</span>
                            <input class="ui-text-input rule-inline-control" type="number" min="1" max="365" step="1" value="${everyNDays}" data-rule-field="time_based.every_n_days" ${inputDisabled}>
                        </p>
                    `
                    : ""
                }
            </div>
        `;
        rulesCardList.appendChild(item);
        syncMaintenanceOverflowState();
    }

    // Render the candidate list for the selected scope and active view mode.
    function renderFileList() {
        if (!fileList) return;
        const items = (Array.isArray(preview?.items) ? preview.items : []).filter((item) => isItemInCurrentScope(item));
        const showSelectors = currentActionView === "manual";
        fileList.innerHTML = "";
        if (items.length === 0) {
            const li = document.createElement("li");
            li.className = "maintenance-file ineligible no-select";
            const title = document.createElement("span");
            title.className = "file-name name";
            title.textContent = currentScope === "stale_worlds" ? "No stale worlds found." : "No backup files found.";
            const meta = document.createElement("span");
            meta.className = "meta";
            meta.textContent = "Nothing is currently eligible to list for this scope.";
            li.appendChild(title);
            li.appendChild(meta);
            fileList.appendChild(li);
            syncMaintenanceOverflowState();
            return;
        }
        items.forEach((item) => {
            const rowMarkedForDelete = !!item.selected_for_delete;
            let stateClass = "";
            let deletableClass = "";
            if (currentActionView === "manual") {
                stateClass = item.eligible ? "eligible" : "ineligible";
                deletableClass = rowMarkedForDelete ? "deletable" : "";
            } else if (currentActionView === "rules") {
                stateClass = rowMarkedForDelete ? "eligible" : "";
            }
            const li = document.createElement("li");
            li.className = `maintenance-file ${stateClass} ${deletableClass}`.trim();
            if (!showSelectors) li.classList.add("no-select");
            li.dataset.path = item.path;
            if (showSelectors) {
                const checkbox = document.createElement("input");
                checkbox.type = "checkbox";
                checkbox.className = "maintenance-select";
                checkbox.disabled = !item.eligible;
                checkbox.value = item.path;
                li.appendChild(checkbox);
            }
            const title = document.createElement("span");
            title.className = "file-name name";
            title.textContent = item.name;
            const meta = document.createElement("span");
            meta.className = "meta";
            meta.textContent = `${item.category} | ${humanBytes(item.size)} | ${reasonText(item.reasons)}`;
            li.appendChild(title);
            li.appendChild(meta);
            fileList.appendChild(li);
        });
        syncMaintenanceOverflowState();
    }

    function renderHistory() {
        const meta = config?.meta || {};
        const missedRuns = getMissedRuns();
        const lastRun = document.getElementById("history-last-run");
        const ruleVersion = document.getElementById("history-rule-version");
        const scheduleVersion = document.getElementById("history-schedule-version");
        const lastChangedBy = document.getElementById("history-last-changed-by");
        const missedRunsCount = document.getElementById("history-missed-runs");
        const ackBtn = runAcknowledgeBtn;

        if (lastRun) {
            const runAt = formatAuditTimestamp(meta.last_run_at || "-");
            const trigger = meta.last_run_trigger || "-";
            const result = meta.last_run_result || "-";
            lastRun.textContent = `${runAt} | ${trigger} | ${result}`;
        }
        if (ruleVersion) ruleVersion.textContent = String(meta.rule_version ?? "-");
        if (scheduleVersion) scheduleVersion.textContent = String(meta.schedule_version ?? "-");
        if (lastChangedBy) {
            const by = formatAuditActor(meta.last_changed_by || "-");
            const at = formatAuditTimestamp(meta.last_changed_at || "-");
            lastChangedBy.textContent = `${by} @ ${at}`;
        }
        if (missedRunsCount) missedRunsCount.textContent = String(missedRuns.length);
        if (ackBtn) {
            ackBtn.disabled = missedRuns.length === 0;
            ackBtn.hidden = missedRuns.length === 0;
        }

        if (historyCardList) {
            historyCardList.innerHTML = "";
            const lastCard = document.createElement("article");
            lastCard.className = "maintenance-card";
            lastCard.innerHTML = `
                <h3 class="maintenance-card-title">Last Run</h3>
                <p class="maintenance-card-meta">${lastRun ? lastRun.textContent : "-"}</p>
            `;
            historyCardList.appendChild(lastCard);

            const runs = getHistoryRuns().slice().reverse().slice(0, 20);
            if (runs.length > 0) {
                runs.forEach((entry, idx) => {
                    const item = document.createElement("article");
                    item.className = "maintenance-card";
                    const at = String(entry?.at || "-");
                    const trigger = String(entry?.trigger || "-");
                    const result = String(entry?.result || "-");
                    const deleted = Number(entry?.deleted_count || 0);
                    const dryRun = !!entry?.dry_run;
                    item.innerHTML = `
                        <h3 class="maintenance-card-title">Run #${idx + 1}</h3>
                        <p class="maintenance-card-meta">${formatAuditTimestamp(at)} | ${trigger} | ${result}</p>
                        <p class="maintenance-card-meta">${dryRun ? "Dry run" : "Apply"} | Deleted: ${deleted}</p>
                    `;
                    historyCardList.appendChild(item);
                });
            }

            if (missedRuns.length === 0) {
                const empty = document.createElement("article");
                empty.className = "maintenance-card";
                empty.innerHTML = `<h3 class="maintenance-card-title">Missed Runs</h3><p class="maintenance-card-meta">No missed runs.</p>`;
                historyCardList.appendChild(empty);
                if (ackBtn && acknowledgeButtonHome && ackBtn.parentElement !== acknowledgeButtonHome) {
                    acknowledgeButtonHome.appendChild(ackBtn);
                }
            } else {
                missedRuns.forEach((entry, idx) => {
                    const at = typeof entry === "string" ? entry : (entry?.at || entry?.run_at || "-");
                    const reason = typeof entry === "string" ? "missed" : (entry?.reason || entry?.trigger || "missed");
                    const item = document.createElement("article");
                    item.className = "maintenance-card";
                    item.innerHTML = `
                        <h3 class="maintenance-card-title">Missed Run #${idx + 1}</h3>
                        <p class="maintenance-card-meta">${formatAuditTimestamp(at)} | ${reason}</p>
                    `;
                    if (idx === 0 && ackBtn) {
                        item.appendChild(ackBtn);
                    }
                    historyCardList.appendChild(item);
                });
            }
        }
    }

    function syncRulesHeaderButtons() {
        if (!rulesEditToggleBtn || !rulesSaveBtn) return;
        const isRulesView = currentActionView === "rules";
        rulesEditToggleBtn.hidden = !isRulesView;
        rulesSaveBtn.hidden = !isRulesView || !rulesEditMode;
        rulesEditToggleBtn.textContent = rulesEditMode ? "Cancel" : "Edit";
        rulesEditToggleBtn.classList.toggle("btn-stop", rulesEditMode);
        rulesEditToggleBtn.classList.toggle("btn-backup", !rulesEditMode);
    }

    function setActionView(viewName) {
        currentActionView = viewName;
        const viewMap = {
            rules: viewRules,
            manual: viewManual,
            history: viewHistory,
        };
        Object.entries(viewMap).forEach(([key, node]) => {
            if (!node) return;
            node.hidden = key !== viewName;
        });

        const btns = [
            [openRulesBtn, "rules"],
            [openHistoryBtn, "history"],
            [openManualBtn, "manual"],
        ];
        btns.forEach(([btn, key]) => {
            setPressedState(btn, key === viewName);
        });

        const viewMeta = {
            rules: { title: "Cleanup Rules", description: "" },
            manual: { title: "Manual Cleanup", description: "" },
            history: { title: "Cleanup History", description: "" },
        };
        const selected = viewMeta[viewName] || viewMeta.rules;
        if (actionTitle) {
            actionTitle.textContent = `${selected.title} - ${scopeLabel()}`;
            actionTitle.classList.add("maintenance-spaced-title");
        }
        if (actionDescription) actionDescription.textContent = selected.description;

        if (viewName === "rules") {
            renderRuleCards();
            syncRulesHeaderButtons();
        } else {
            if (rulesEditToggleBtn) rulesEditToggleBtn.hidden = true;
            if (rulesSaveBtn) rulesSaveBtn.hidden = true;
            if (viewName === "manual") syncManualCleanupModeState();
        }
        if (actionToolbar && actionDescription) {
            actionToolbar.hidden = !actionDescription.textContent.trim();
        }
        renderFileList();
    }

    function clearUnsavedActions() {
        cancelRulesEdit();
        document.querySelectorAll(".maintenance-select:checked").forEach((node) => {
            node.checked = false;
        });
        if (ruleRunDryRunInput) ruleRunDryRunInput.checked = true;
        if (ruleRunDestructiveConfirmInput) ruleRunDestructiveConfirmInput.checked = false;
        if (manualDryRunInput) manualDryRunInput.checked = true;
        if (manualDestructiveConfirmInput) manualDestructiveConfirmInput.checked = false;
        syncRuleRunModeState();
        syncManualCleanupModeState();
        closePasswordModal();
    }

    function syncDestructiveModeState(options) {
        const isDryRun = !!options.dryRunInput?.checked;
        const hasDestructiveConfirm = !!options.destructiveConfirmInput?.checked;
        const button = options.runButton;
        if (!button) return;

        if (isDryRun) {
            if (options.destructiveConfirmWrap) options.destructiveConfirmWrap.hidden = true;
            if (options.destructiveConfirmInput) options.destructiveConfirmInput.checked = false;
            button.disabled = false;
            button.classList.remove("btn-stop");
            button.classList.add("btn-start");
            return;
        }

        if (options.destructiveConfirmWrap) options.destructiveConfirmWrap.hidden = false;
        button.classList.remove("btn-start");
        button.classList.add("btn-stop");
        button.disabled = !hasDestructiveConfirm;
    }

    function syncManualCleanupModeState() {
        syncDestructiveModeState({
            dryRunInput: manualDryRunInput,
            destructiveConfirmWrap: manualDestructiveConfirmWrap,
            destructiveConfirmInput: manualDestructiveConfirmInput,
            runButton: runManualDeleteBtn,
        });
    }

    function syncRuleRunModeState() {
        syncDestructiveModeState({
            dryRunInput: ruleRunDryRunInput,
            destructiveConfirmWrap: ruleRunDestructiveConfirmWrap,
            destructiveConfirmInput: ruleRunDestructiveConfirmInput,
            runButton: runRuleDeleteBtn,
        });
    }

    function openPasswordModal(actionKey, promptText) {
        pendingProtectedAction = actionKey;
        if (passwordText) passwordText.textContent = promptText || "Enter sudo password to continue.";
        if (passwordInput) passwordInput.value = "";
        if (!passwordModal) return;
        passwordModal.setAttribute("aria-hidden", "false");
        if (passwordInput) passwordInput.focus();
    }

    function closePasswordModal() {
        pendingProtectedAction = null;
        if (!passwordModal) return;
        passwordModal.setAttribute("aria-hidden", "true");
        if (passwordInput) passwordInput.value = "";
    }

    async function submitProtectedAction() {
        const sudoPassword = (passwordInput?.value || "").trim();
        if (!sudoPassword) {
            showError("Password required.", "Enter sudo password to continue.");
            return null;
        }
        if (pendingProtectedAction === "open-rules-edit") {
            await apiPost("/maintenance/api/confirm-password", {
                scope: currentScope,
                action: "open_rules_edit",
                sudo_password: sudoPassword,
            });
            beginRulesEdit();
            return { skipRefresh: true };
        }
        if (pendingProtectedAction === "save-rules") {
            const saved = await saveRulesEdit(sudoPassword);
            if (!saved) return null;
            return { skipRefresh: true };
        }
        if (pendingProtectedAction === "run-rules") {
            return await apiPost("/maintenance/api/run-rules", {
                scope: currentScope,
                sudo_password: sudoPassword,
                dry_run: !!ruleRunDryRunInput?.checked,
                rule_key: "",
            });
        }
        if (pendingProtectedAction === "manual-delete") {
            const selected = Array.from(document.querySelectorAll(".maintenance-select:checked")).map((node) => node.value);
            if (selected.length === 0) {
                showError("No files selected.", "Select at least one eligible file from the left pane.");
                return null;
            }
            return await apiPost("/maintenance/api/manual-delete", {
                scope: currentScope,
                sudo_password: sudoPassword,
                selected_paths: selected,
                dry_run: !!manualDryRunInput?.checked,
            });
        }
        return null;
    }

    // Thin API client for maintenance JSON endpoints with shared error handling.
    async function apiPost(path, body) {
        if (UI_ONLY_MODE) {
            const payloadBody = body || {};
            if (path === "/maintenance/api/save-rules") {
                config = { ...(config || {}), rules: payloadBody.rules || config?.rules || {} };
                devActionAlert("Would save cleanup rules", formatDevPayload(payloadBody));
                return { ok: true, config, preview };
            }
            if (path === "/maintenance/api/run-rules") {
                devActionAlert("Would run rule-based cleanup", formatDevPayload(payloadBody));
                return { ok: true, dry_run: !!payloadBody.dry_run, config };
            }
            if (path === "/maintenance/api/manual-delete") {
                devActionAlert("Would run manual cleanup", formatDevPayload(payloadBody));
                return { ok: true, dry_run: !!payloadBody.dry_run, config };
            }
            if (path === "/maintenance/api/ack-non-normal") {
                const existing = Array.isArray(nonNormal?.missed_runs) ? nonNormal.missed_runs : [];
                const kept = existing.filter((entry) => {
                    if (!entry || typeof entry !== "object") return false;
                    const explicitScope = String(entry.scope || "").trim().toLowerCase();
                    if (explicitScope === "backups" || explicitScope === "stale_worlds") {
                        return explicitScope !== currentScope;
                    }
                    const scheduleId = String(entry.schedule_id || "").trim().toLowerCase();
                    if (scheduleId.startsWith("backups:")) return currentScope !== "backups";
                    if (scheduleId.startsWith("stale_worlds:")) return currentScope !== "stale_worlds";
                    return false;
                });
                nonNormal = { ...(nonNormal || {}), missed_runs: kept };
                devActionAlert("Would acknowledge missed runs", formatDevPayload(payloadBody));
                return { ok: true, non_normal: nonNormal };
            }
            devActionAlert(`Would call ${path}`, formatDevPayload(payloadBody));
            return { ok: true };
        }

        const response = await fetch(path, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRF-Token": csrfToken,
            },
            body: JSON.stringify(body || {}),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || !payload.ok) {
            throw payload;
        }
        return payload;
    }

    async function refreshState(options = {}) {
        const preservePreview = !!options.preservePreview;
        const requestedScope = options.scope || currentScope;
        if (UI_ONLY_MODE) {
            renderSummaryPanels();
            if (currentActionView === "rules") {
                setActionView(currentActionView);
            }
            return;
        }

        const response = await fetch(`/maintenance/api/state?scope=${encodeURIComponent(requestedScope)}`, { headers: { Accept: "application/json" } });
        const payload = await response.json();
        if (!payload.ok) throw payload;
        currentScope = String(payload.scope || requestedScope || "backups");
        config = payload.config || config;
        if (!preservePreview) {
            preview = payload.preview || preview;
        }
        nonNormal = payload.non_normal || nonNormal;
        storage = payload.storage || storage;
        cleanupHistory = payload.history || cleanupHistory;
        deviceMap = payload.device_map || deviceMap;
        nextRunAt = payload.next_run_at || "-";
        syncScopeButtons();
        renderSummaryPanels();
        if (currentActionView === "rules") {
            setActionView(currentActionView);
        }
    }

    document.getElementById("maintenance-error-ok")?.addEventListener("click", closeError);
    errorModal?.addEventListener("click", (event) => {
        if (event.target === errorModal) closeError();
    });

    runRuleDeleteBtn?.addEventListener("click", () => {
        openPasswordModal("run-rules", "Enter sudo password to run rule-based cleanup.");
    });

    runManualDeleteBtn?.addEventListener("click", () => {
        openPasswordModal("manual-delete", "Enter sudo password to confirm manual cleanup.");
    });

    manualDryRunInput?.addEventListener("change", syncManualCleanupModeState);
    manualDestructiveConfirmInput?.addEventListener("change", syncManualCleanupModeState);
    ruleRunDryRunInput?.addEventListener("change", syncRuleRunModeState);
    ruleRunDestructiveConfirmInput?.addEventListener("change", syncRuleRunModeState);

    passwordCancel?.addEventListener("click", closePasswordModal);
    passwordModal?.addEventListener("click", (event) => {
        if (event.target === passwordModal) closePasswordModal();
    });
    passwordInput?.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            event.preventDefault();
            closePasswordModal();
            return;
        }
        if (event.key === "Enter") {
            event.preventDefault();
            passwordSubmit?.click();
        }
    });
    passwordSubmit?.addEventListener("click", async () => {
        try {
            const actionKey = pendingProtectedAction;
            const payload = await submitProtectedAction();
            if (!payload) return;
            const preservePreview = !!payload?.dry_run && !!payload?.preview && Array.isArray(payload.preview.items);
            if (preservePreview) {
                preview = payload.preview;
            }
            closePasswordModal();
            if (!payload?.skipRefresh) {
                await refreshState({ preservePreview });
            }
            if (payload?.dry_run && payload?.preview) {
                showDryRunModal(payload.preview, actionKey);
            }
        } catch (err) {
            showError(err?.message || "Cleanup action failed.", err?.details || err?.error_code);
        }
    });

    dryRunOk?.addEventListener("click", closeDryRunModal);
    dryRunModal?.addEventListener("click", (event) => {
        if (event.target === dryRunModal) closeDryRunModal();
    });

    runAcknowledgeBtn?.addEventListener("click", async () => {
        try {
            await apiPost("/maintenance/api/ack-non-normal", { scope: currentScope });
            await refreshState();
        } catch (err) {
            showError(err?.message || "Failed to acknowledge warning.", err?.details || err?.error_code);
        }
    });

    renderRuleCards();
    syncScopeButtons();
    renderSummaryPanels();
    syncManualCleanupModeState();
    syncRuleRunModeState();

    function onRuleFieldChanged(target) {
        if (!(target instanceof HTMLInputElement) && !(target instanceof HTMLSelectElement)) return;
        setRuleFieldWarning(target);
        const field = target.getAttribute("data-rule-field");
        if (!field || !rulesEditMode || !rulesDraft) return;

        const applyUpdate = RULE_FIELD_UPDATERS[field];
        if (!applyUpdate) return;
        const nextValue = (target instanceof HTMLInputElement && target.type === "checkbox") ? target.checked : target.value;
        applyUpdate(rulesDraft, nextValue);

        if (field.startsWith("time_based.")) {
            renderRuleCards();
        }
        renderFileList();
    }

    rulesCardList?.addEventListener("input", (event) => {
        onRuleFieldChanged(event.target);
    });
    rulesCardList?.addEventListener("change", (event) => {
        onRuleFieldChanged(event.target);
    });

    openRulesBtn?.addEventListener("click", () => {
        clearUnsavedActions();
        setActionView("rules");
    });
    openHistoryBtn?.addEventListener("click", () => {
        clearUnsavedActions();
        setActionView("history");
    });
    openManualBtn?.addEventListener("click", () => {
        clearUnsavedActions();
        setActionView("manual");
    });
    scopeBackupsBtn?.addEventListener("click", async () => {
        if (currentScope === "backups") return;
        clearUnsavedActions();
        currentScope = "backups";
        syncScopeButtons();
        try {
            await refreshState({ scope: currentScope });
            setActionView(currentActionView);
        } catch (err) {
            showError(err?.message || "Failed to switch scope.", err?.details || err?.error_code);
        }
    });
    scopeStaleBtn?.addEventListener("click", async () => {
        if (currentScope === "stale_worlds") return;
        clearUnsavedActions();
        currentScope = "stale_worlds";
        syncScopeButtons();
        try {
            await refreshState({ scope: currentScope });
            setActionView(currentActionView);
        } catch (err) {
            showError(err?.message || "Failed to switch scope.", err?.details || err?.error_code);
        }
    });
    rulesEditToggleBtn?.addEventListener("click", () => {
        if (rulesEditMode) {
            cancelRulesEdit();
            return;
        }
        openPasswordModal("open-rules-edit", "Enter sudo password to edit cleanup rules.");
    });
    rulesSaveBtn?.addEventListener("click", async () => {
        if (!rulesEditMode) return;
        if (hasRuleFieldValidationErrors()) {
            showError("Some rule values are invalid.", "Fix the highlighted fields before saving.");
            const firstInvalid = rulesCardList?.querySelector("input.rule-input-invalid, select.rule-input-invalid");
            if (firstInvalid && typeof firstInvalid.focus === "function") firstInvalid.focus();
            return;
        }
        openPasswordModal("save-rules", "Enter sudo password to save cleanup rules.");
    });
    const missedRuns = getMissedRuns();
    setActionView(missedRuns.length > 0 ? "history" : "rules");
    syncMaintenanceOverflowState();
    window.addEventListener("resize", syncMaintenanceOverflowState);
    if (window.ResizeObserver) {
        const ro = new ResizeObserver(syncMaintenanceOverflowState);
        if (actionContent) ro.observe(actionContent);
        if (maintenanceFileListPane) ro.observe(maintenanceFileListPane);
    }

    document.querySelectorAll(".nav-link").forEach((link) => {
        link.addEventListener("click", clearUnsavedActions);
    });
    window.addEventListener("pagehide", clearUnsavedActions);
});



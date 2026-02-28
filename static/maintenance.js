"use strict";

document.addEventListener("DOMContentLoaded", () => {
    const darkModeQuery = window.matchMedia("(prefers-color-scheme: dark)");
    function applyThemePreference() {
        document.documentElement.classList.toggle("theme-dark", darkModeQuery.matches);
    }
    applyThemePreference();
    if (darkModeQuery.addEventListener) {
        darkModeQuery.addEventListener("change", applyThemePreference);
    } else if (darkModeQuery.addListener) {
        darkModeQuery.addListener(applyThemePreference);
    }

    const toggles = Array.from(document.querySelectorAll(".maintenance-toggle"));
    const modePanels = Array.from(document.querySelectorAll("[data-mode-panel]"));
    const modeInputs = Array.from(document.querySelectorAll('input[name="mode"]'));
    const ruleToggleButtons = Array.from(document.querySelectorAll(".rule-toggle-btn[data-rule-toggle]"));
    const backupsForm = document.querySelector('form[action="/maintenance/cleanup-backups"]');
    const staleForm = document.querySelector('form[action="/maintenance/cleanup-stale-worlds"]');
    const fileListPanes = Array.from(document.querySelectorAll(".maintenance-file-list"));
    const backupRows = Array.from(document.querySelectorAll('.maintenance-file-list[data-mode-panel="backups"] .maintenance-file'));
    const staleRows = Array.from(document.querySelectorAll('.maintenance-file-list[data-mode-panel="stale"] .maintenance-file'));

    function syncVerticalScrollbarClass(target) {
        if (!target) return;
        const hasVerticalScrollbar = target.scrollHeight > target.clientHeight + 1;
        target.classList.toggle("has-vscroll", hasVerticalScrollbar);
    }

    function watchVerticalScrollbarClass(target) {
        if (!target) return;
        syncVerticalScrollbarClass(target);
        target.addEventListener("scroll", () => syncVerticalScrollbarClass(target), { passive: true });
        window.addEventListener("resize", () => syncVerticalScrollbarClass(target));
        if (window.ResizeObserver) {
            const ro = new ResizeObserver(() => syncVerticalScrollbarClass(target));
            ro.observe(target);
        }
        if (window.MutationObserver) {
            const mo = new MutationObserver(() => syncVerticalScrollbarClass(target));
            mo.observe(target, { childList: true, subtree: true, characterData: true });
        }
    }

    fileListPanes.forEach((pane) => watchVerticalScrollbarClass(pane));

    function setMode(mode) {
        const next = mode === "stale" ? "stale" : "backups";
        toggles.forEach((toggle) => {
            toggle.dataset.mode = next;
            toggle.querySelectorAll(".maintenance-toggle-btn").forEach((btn) => {
                const active = btn.dataset.modeTarget === next;
                btn.classList.toggle("active", active);
            });
        });
        modePanels.forEach((panel) => {
            const show = panel.dataset.modePanel === next;
            panel.classList.toggle("hidden", !show);
        });
        modeInputs.forEach((input) => {
            input.value = next;
        });
    }

    toggles.forEach((toggle) => {
        toggle.querySelectorAll(".maintenance-toggle-btn").forEach((btn) => {
            btn.addEventListener("click", () => {
                setMode(btn.dataset.modeTarget || "backups");
            });
        });
    });

    const initial = (toggles[0] && toggles[0].dataset.mode) || "backups";
    setMode(initial);

    function intValue(input, fallback) {
        if (!input) return fallback;
        const raw = String(input.value || "").trim();
        const parsed = Number.parseInt(raw, 10);
        if (!Number.isFinite(parsed) || parsed < 0) return fallback;
        return parsed;
    }

    function boolValue(input, fallback = true) {
        if (!input) return fallback;
        const raw = String(input.value || "").trim().toLowerCase();
        if (raw === "false" || raw === "0" || raw === "off" || raw === "no") return false;
        if (raw === "true" || raw === "1" || raw === "on" || raw === "yes") return true;
        return fallback;
    }

    function syncRuleToggle(button) {
        const hiddenName = button.dataset.ruleToggle || "";
        const form = button.closest("form");
        if (!hiddenName || !form) return;
        const hidden = form.querySelector(`input[type="hidden"][name="${hiddenName}"]`);
        if (!hidden) return;
        const enabled = boolValue(hidden, true);
        button.classList.toggle("on", enabled);
        button.textContent = enabled ? "ON" : "OFF";
        button.setAttribute("aria-pressed", enabled ? "true" : "false");
        const row = button.closest(".maintenance-rule-row");
        if (!row) return;
        const numberInput = row.querySelector('input[type="number"]');
        if (numberInput) numberInput.disabled = !enabled;
    }

    function setRowDeletable(row, deletable) {
        row.classList.toggle("deletable-live", !!deletable);
        row.classList.toggle("deletable", !!deletable);
        const meta = row.querySelector(".meta");
        if (!meta) return;
        const base = meta.dataset.metaBase || meta.textContent || "";
        meta.textContent = deletable ? `${base} | deletable` : base;
    }

    function updateBackupsPreview() {
        if (!backupsForm || backupRows.length === 0) return;
        const keepManual = intValue(backupsForm.querySelector('input[name="keep_manual_count"]'), 30);
        const keepOther = intValue(backupsForm.querySelector('input[name="keep_other_count"]'), 20);
        const keepAutoDays = intValue(backupsForm.querySelector('input[name="keep_auto_days"]'), 7);
        const keepSessionDays = intValue(backupsForm.querySelector('input[name="keep_session_days"]'), 14);
        const keepPreRestoreDays = intValue(backupsForm.querySelector('input[name="keep_pre_restore_days"]'), 14);
        const keepManualEnabled = boolValue(backupsForm.querySelector('input[name="rule_keep_manual_enabled"]'));
        const keepOtherEnabled = boolValue(backupsForm.querySelector('input[name="rule_keep_other_enabled"]'));
        const keepAutoEnabled = boolValue(backupsForm.querySelector('input[name="rule_keep_auto_enabled"]'));
        const keepSessionEnabled = boolValue(backupsForm.querySelector('input[name="rule_keep_session_enabled"]'));
        const keepPreRestoreEnabled = boolValue(backupsForm.querySelector('input[name="rule_keep_pre_restore_enabled"]'));
        const nowSec = Date.now() / 1000;
        const autoCutoff = nowSec - (keepAutoDays * 86400);
        const sessionCutoff = nowSec - (keepSessionDays * 86400);
        const preRestoreCutoff = nowSec - (keepPreRestoreDays * 86400);
        let manualSeen = 0;
        let otherSeen = 0;

        backupRows.forEach((row) => {
            const bucket = String(row.dataset.bucket || "").trim().toLowerCase();
            const mtime = Number.parseFloat(row.dataset.mtime || "0");
            let deletable = false;
            if (bucket === "manual") {
                manualSeen += 1;
                deletable = keepManualEnabled && manualSeen > keepManual;
            } else if (bucket === "other") {
                otherSeen += 1;
                deletable = keepOtherEnabled && otherSeen > keepOther;
            } else if (bucket === "auto") {
                deletable = keepAutoEnabled && Number.isFinite(mtime) && mtime < autoCutoff;
            } else if (bucket === "session") {
                deletable = keepSessionEnabled && Number.isFinite(mtime) && mtime < sessionCutoff;
            } else if (bucket === "pre_restore") {
                deletable = keepPreRestoreEnabled && Number.isFinite(mtime) && mtime < preRestoreCutoff;
            }
            setRowDeletable(row, deletable);
        });
    }

    function updateStalePreview() {
        if (!staleForm || staleRows.length === 0) return;
        const keepStaleCount = intValue(staleForm.querySelector('input[name="keep_stale_count"]'), 2);
        const staleMaxAgeDays = intValue(staleForm.querySelector('input[name="stale_max_age_days"]'), 3);
        const keepStaleEnabled = boolValue(staleForm.querySelector('input[name="rule_keep_stale_enabled"]'));
        const staleAgeEnabled = boolValue(staleForm.querySelector('input[name="rule_stale_age_enabled"]'));
        const nowSec = Date.now() / 1000;
        const staleCutoff = nowSec - (staleMaxAgeDays * 86400);

        staleRows.forEach((row, idx) => {
            const mtime = Number.parseFloat(row.dataset.mtime || "0");
            const olderThanCutoff = staleAgeEnabled && Number.isFinite(mtime) && mtime <= staleCutoff;
            const pastKeepCount = keepStaleEnabled ? idx >= keepStaleCount : true;
            const deletable = pastKeepCount && olderThanCutoff;
            setRowDeletable(row, deletable);
        });
    }

    ruleToggleButtons.forEach((button) => {
        syncRuleToggle(button);
        button.addEventListener("click", () => {
            const hiddenName = button.dataset.ruleToggle || "";
            const form = button.closest("form");
            if (!hiddenName || !form) return;
            const hidden = form.querySelector(`input[type="hidden"][name="${hiddenName}"]`);
            if (!hidden) return;
            const enabled = !boolValue(hidden, true);
            hidden.value = enabled ? "true" : "false";
            syncRuleToggle(button);
            if (form === backupsForm) {
                updateBackupsPreview();
            } else if (form === staleForm) {
                updateStalePreview();
            }
        });
    });

    if (backupsForm) {
        backupsForm.querySelectorAll('input[type="number"]').forEach((input) => {
            input.addEventListener("input", updateBackupsPreview);
        });
        updateBackupsPreview();
    }
    if (staleForm) {
        staleForm.querySelectorAll('input[type="number"]').forEach((input) => {
            input.addEventListener("input", updateStalePreview);
        });
        updateStalePreview();
    }
});

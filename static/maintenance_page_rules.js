(function (global) {
    const namespace = global.MCWebMaintenancePage || {};

    function createRulesController(ctx) {
        const dom = ctx.dom || {};
        const state = ctx.state || {};

        const RULE_FIELD_UPDATERS = {
            "age.days": (draft, value) => { draft.age.days = Math.max(3, Number(value || 3)); },
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

        function getEffectiveRules() {
            const rules = state.config?.rules || {};
            const countMax = Number(rules?.count?.max_per_category ?? 30);
            const effective = {
                age: {
                    days: Number(rules?.age?.days ?? 3),
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
            if (state.rulesEditMode && state.rulesDraft) {
                effective.age = { ...effective.age, ...(state.rulesDraft.age || {}) };
                effective.space = { ...effective.space, ...(state.rulesDraft.space || {}) };
                effective.count = { ...effective.count, ...(state.rulesDraft.count || {}) };
                effective.time_based = { ...effective.time_based, ...(state.rulesDraft.time_based || {}) };
            }
            return effective;
        }

        function beginRulesEdit() {
            state.rulesDraft = getEffectiveRules();
            state.rulesEditMode = true;
            renderRuleCards();
            ctx.actions.renderFileList?.();
            ctx.actions.syncPaneHeadActions?.();
        }

        function cancelRulesEdit() {
            state.rulesEditMode = false;
            state.rulesDraft = null;
            renderRuleCards();
            ctx.actions.renderFileList?.();
            ctx.actions.syncPaneHeadActions?.();
        }

        async function saveRulesEdit(sudoPassword = "") {
            if (!state.rulesEditMode || !state.rulesDraft) return false;
            if (hasRuleFieldValidationErrors()) {
                ctx.actions.showError?.("Some rule values are invalid.", "Fix the highlighted fields before saving.");
                const firstInvalid = dom.rulesCardList?.querySelector("input.rule-input-invalid, select.rule-input-invalid");
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
            const payload = await ctx.actions.apiPost?.("/maintenance/api/save-rules", {
                scope: state.currentScope,
                sudo_password: String(sudoPassword || ""),
                rules: {
                    age: {
                        days: Math.max(3, Number(nextRules.age.days ?? 3)),
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
                        max_delete_files_absolute: 10,
                        max_delete_percent_eligible: 50,
                        max_delete_min_if_non_empty: 1,
                    },
                },
            });
            state.config = payload.config || state.config;
            state.preview = payload.preview || state.preview;
            ctx.actions.renderHistory?.();
            ctx.actions.renderStats?.();
            cancelRulesEdit();
            return true;
        }

        function renderRuleCards() {
            if (!dom.rulesCardList) return;
            dom.rulesCardList.innerHTML = "";
            const effective = getEffectiveRules();
            const inputDisabled = state.rulesEditMode ? "" : "disabled";
            const ageDays = Number(effective?.age?.days ?? 3);
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
            const isStaleScope = state.currentScope === "stale_worlds";
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
                        ${state.rulesEditMode
                            ? `<input class="ui-text-input" type="number" min="3" step="1" value="${ageDays}" data-rule-field="age.days" ${inputDisabled}>`
                            : `<span class="rule-inline-value">${ageDays}</span>`
                        }
                    </p>
                </div>
                <div class="rule-section">
                    <h3 class="maintenance-card-title">Space Rule</h3>
                    <p class="rule-inline-sentence">
                        <span class="rule-inline-label">Max disk usage before deleting starts (% used)</span>
                        ${state.rulesEditMode
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
                                ${state.rulesEditMode
                                    ? `<input class="ui-text-input" type="number" min="3" step="1" value="${maxPerCategory}" data-rule-field="count.max_per_category" ${inputDisabled}>`
                                    : `<span class="rule-inline-value">${maxPerCategory}</span>`
                                }
                            </p>
                        `
                        : `
                            <p class="rule-inline-sentence">
                                <span class="rule-inline-label">Newest Session backups to keep</span>
                                ${state.rulesEditMode
                                    ? `<input class="ui-text-input" type="number" min="3" step="1" value="${sessionKeep}" data-rule-field="count.session_backups_to_keep" ${inputDisabled}>`
                                    : `<span class="rule-inline-value">${sessionKeep}</span>`
                                }
                            </p>
                            <p class="rule-inline-sentence">
                                <span class="rule-inline-label">Newest Manual backups to keep</span>
                                ${state.rulesEditMode
                                    ? `<input class="ui-text-input" type="number" min="3" step="1" value="${manualKeep}" data-rule-field="count.manual_backups_to_keep" ${inputDisabled}>`
                                    : `<span class="rule-inline-value">${manualKeep}</span>`
                                }
                            </p>
                            <p class="rule-inline-sentence">
                                <span class="rule-inline-label">Newest Prerestore backups to keep</span>
                                ${state.rulesEditMode
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
                        ${state.rulesEditMode
                            ? `<input class="rule-inline-control" type="checkbox" data-rule-field="time_based.enabled" ${timeEnabled ? "checked" : ""}>`
                            : `<span class="rule-inline-value rule-inline-value-wide">${timeEnabled ? "Enabled" : "Disabled"}</span>`
                        }
                    </p>
                    <p class="rule-inline-sentence">
                        <span class="rule-inline-label">Time of cleanup</span>
                        ${state.rulesEditMode
                            ? `<input class="ui-text-input rule-inline-control" type="time" value="${backupTime}" data-rule-field="time_based.time_of_backup" ${!timeEnabled ? "disabled" : inputDisabled}>`
                            : `<span class="rule-inline-value rule-inline-value-wide">${backupTime}</span>`
                        }
                    </p>
                    <p class="rule-inline-sentence">
                        <span class="rule-inline-label">Repeat</span>
                        ${state.rulesEditMode
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
                    ${state.rulesEditMode && timeEnabled && repeatMode === "weekly"
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
                    ${state.rulesEditMode && timeEnabled && repeatMode === "monthly"
                        ? `
                            <p class="rule-inline-sentence">
                                <span class="rule-inline-label">Date (1-31):</span>
                                <input class="ui-text-input rule-inline-control" type="number" min="1" max="31" step="1" value="${monthlyDate}" data-rule-field="time_based.monthly_date" ${inputDisabled}>
                            </p>
                        `
                        : ""
                    }
                    ${state.rulesEditMode && timeEnabled && repeatMode === "every_n_days"
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
            dom.rulesCardList.appendChild(item);
            ctx.actions.syncMaintenanceOverflowState?.();
        }

        function getRuleFieldWarning(target) {
            if (!(target instanceof HTMLInputElement) && !(target instanceof HTMLSelectElement)) return "";
            if (target.disabled || target.validity.valid) return "";
            const field = target.getAttribute("data-rule-field") || "";
            if (target.validity.valueMissing) return "Value is required.";
            if (target.validity.badInput) return "Enter a valid number.";
            if (target.validity.rangeUnderflow) {
                if (field === "age.days") return "Minimum is 3 days.";
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
            if (!dom.rulesCardList) return false;
            const fields = dom.rulesCardList.querySelectorAll("input[data-rule-field], select[data-rule-field]");
            let hasErrors = false;
            fields.forEach((field) => {
                setRuleFieldWarning(field);
                if ((field instanceof HTMLInputElement || field instanceof HTMLSelectElement) && !field.disabled && !field.validity.valid) {
                    hasErrors = true;
                }
            });
            return hasErrors;
        }

        function onRuleFieldChanged(target) {
            if (!(target instanceof HTMLInputElement) && !(target instanceof HTMLSelectElement)) return;
            setRuleFieldWarning(target);
            const field = target.getAttribute("data-rule-field");
            if (!field || !state.rulesEditMode || !state.rulesDraft) return;

            const applyUpdate = RULE_FIELD_UPDATERS[field];
            if (!applyUpdate) return;
            const nextValue = (target instanceof HTMLInputElement && target.type === "checkbox") ? target.checked : target.value;
            applyUpdate(state.rulesDraft, nextValue);

            if (field.startsWith("time_based.")) {
                renderRuleCards();
            }
            ctx.actions.renderFileList?.();
        }

        return {
            beginRulesEdit,
            cancelRulesEdit,
            saveRulesEdit,
            renderRuleCards,
            getEffectiveRules,
            hasRuleFieldValidationErrors,
            onRuleFieldChanged,
        };
    }

    global.MCWebMaintenancePage = Object.assign({}, namespace, {
        rules: {
            createRulesController,
        },
    });
})(window);

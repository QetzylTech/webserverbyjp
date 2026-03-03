document.addEventListener("DOMContentLoaded", () => {
    const form = document.querySelector(".setup-form");
    if (!form) return;

    const steps = Array.from(document.querySelectorAll(".wizard-step"));
    const paneTitle = document.getElementById("setup-pane-title");
    const backBtn = document.getElementById("setup-back-btn");
    const nextBtn = document.getElementById("setup-next-btn");

    const serviceInput = form.querySelector('input[name="service"]');
    const tzInput = document.getElementById("setup-display-tz");
    const rootInput = document.getElementById("setup-minecraft-root");
    const backupInput = document.getElementById("setup-backup-dir");
    const createBackupWrap = document.getElementById("create-backup-dir-wrap");
    const createBackupInput = document.getElementById("create-backup-dir");
    const pwd = document.getElementById("setup-admin-password");
    const pwdConfirm = document.getElementById("setup-admin-password-confirm");
    const pwdStatus = document.getElementById("setup-password-status");

    let stepIndex = 0;
    const stepTitles = [
        "Welcome",
        "Timezone",
        "Root Location",
        "Backup Location",
        "Password",
        "Waiting for Configuration to Finish",
        "Success and Confirmation",
    ];

    function setFieldError(fieldKey, message) {
        const key = String(fieldKey || "").toLowerCase();
        const errorKey = key === "service" ? "minecraft_root_dir" : key;
        const map = {
            display_tz: tzInput,
            minecraft_root_dir: rootInput,
            backup_dir: backupInput,
            admin_password: pwd,
            admin_password_confirm: pwdConfirm,
            service: rootInput,
        };
        const input = map[key];
        if (input) input.classList.add("field-invalid");
        const errorNode = document.getElementById(`error-${errorKey}`);
        if (errorNode) errorNode.textContent = message || "";
    }

    function clearFieldError(fieldKey) {
        const key = String(fieldKey || "").toLowerCase();
        const errorKey = key === "service" ? "minecraft_root_dir" : key;
        const map = {
            display_tz: tzInput,
            minecraft_root_dir: rootInput,
            backup_dir: backupInput,
            admin_password: pwd,
            admin_password_confirm: pwdConfirm,
            service: rootInput,
        };
        const input = map[key];
        if (input) input.classList.remove("field-invalid");
        const errorNode = document.getElementById(`error-${errorKey}`);
        if (errorNode) errorNode.textContent = "";
    }

    function clearKnownErrors() {
        [
            "display_tz",
            "minecraft_root_dir",
            "backup_dir",
            "admin_password",
            "admin_password_confirm",
            "service",
        ].forEach(clearFieldError);
    }

    function setCreateOptionVisible(fieldKey, visible) {
        const key = String(fieldKey || "").toLowerCase();
        if (key === "backup_dir" && createBackupWrap) {
            createBackupWrap.hidden = !visible;
        }
    }

    function showStep(index) {
        stepIndex = Math.max(0, Math.min(index, steps.length - 1));
        if (paneTitle) paneTitle.textContent = stepTitles[stepIndex] || "Setup";
        steps.forEach((node, i) => {
            node.hidden = i !== stepIndex;
        });
        const isInteractive = stepIndex <= 4;
        if (backBtn) {
            backBtn.hidden = !isInteractive;
            backBtn.disabled = stepIndex === 0;
        }
        if (nextBtn) {
            nextBtn.hidden = !isInteractive;
            if (stepIndex < 4) {
                nextBtn.textContent = "Next";
                nextBtn.disabled = false;
            } else {
                nextBtn.textContent = "Apply Configuration";
                nextBtn.disabled = false;
            }
        }
    }

    function validatePasswordMatch() {
        const a = String(pwd?.value || "");
        const b = String(pwdConfirm?.value || "");
        if (!a && !b) {
            if (pwdStatus) {
                pwdStatus.textContent = "";
                pwdStatus.classList.remove("match", "no-match");
            }
            return true;
        }
        if (a && b && a === b) {
            if (pwdStatus) {
                pwdStatus.textContent = "Passwords match.";
                pwdStatus.classList.remove("no-match");
                pwdStatus.classList.add("match");
            }
            clearFieldError("admin_password");
            clearFieldError("admin_password_confirm");
            return true;
        }
        if (pwdStatus) {
            pwdStatus.textContent = "Passwords do not match.";
            pwdStatus.classList.remove("match");
            pwdStatus.classList.add("no-match");
        }
        setFieldError("admin_password", "Passwords do not match.");
        setFieldError("admin_password_confirm", "Passwords do not match.");
        return false;
    }

    async function validateStepServer(kind, values) {
        const response = await fetch("/setup/validate", {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({ kind, values }),
        });
        const payload = await response.json().catch(() => ({}));
        const missing = payload.missing_fields || {};
        setCreateOptionVisible("backup_dir", Boolean(missing.BACKUP_DIR));
        if (!response.ok || !payload.ok) {
            const errors = payload.field_errors || {};
            Object.keys(errors).forEach((key) => setFieldError(key, errors[key]));
            return false;
        }
        return true;
    }

    async function validateCurrentStep() {
        clearKnownErrors();

        if (stepIndex === 0) {
            return true;
        }
        if (stepIndex === 1) {
            if (!String(tzInput?.value || "").trim()) {
                setFieldError("display_tz", "This field is required.");
                return false;
            }
            return validateStepServer("timezone", { DISPLAY_TZ: String(tzInput.value || "").trim() });
        }
        if (stepIndex === 2) {
            const root = String(rootInput?.value || "").trim();
            if (!root) {
                setFieldError("minecraft_root_dir", "This field is required.");
                return false;
            }
            return validateStepServer("root", {
                SERVICE: String(serviceInput?.value || "minecraft").trim() || "minecraft",
                MINECRAFT_ROOT_DIR: root,
            });
        }
        if (stepIndex === 3) {
            const backup = String(backupInput?.value || "").trim();
            if (!backup) {
                setFieldError("backup_dir", "This field is required.");
                return false;
            }
            return validateStepServer("backup", {
                BACKUP_DIR: backup,
                CREATE_BACKUP_DIR: Boolean(createBackupInput?.checked),
            });
        }
        if (stepIndex === 4) {
            const password = String(pwd?.value || "").trim();
            const confirm = String(pwdConfirm?.value || "").trim();
            let ok = true;
            if (!password) {
                setFieldError("admin_password", "This field is required.");
                ok = false;
            }
            if (!confirm) {
                setFieldError("admin_password_confirm", "This field is required.");
                ok = false;
            }
            if (!ok) {
                validatePasswordMatch();
                return false;
            }
            if (password.length < 8) {
                setFieldError("admin_password", "Password must be at least 8 characters.");
                return false;
            }
            return validatePasswordMatch();
        }
        return true;
    }

    async function submitSetup() {
        showStep(5);
        const formData = new FormData(form);
        const response = await fetch("/setup/submit", {
            method: "POST",
            body: formData,
            headers: { Accept: "application/json" },
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || !payload.ok) {
            showStep(4);
            const errors = payload.field_errors || {};
            Object.keys(errors).forEach((key) => setFieldError(key, errors[key]));
            if (errors.admin_password || errors.admin_password_confirm) {
                validatePasswordMatch();
            }
            return;
        }
        showStep(6);
        setTimeout(() => {
            window.location.href = payload.redirect || "/";
        }, 1400);
    }

    if (backBtn) {
        backBtn.addEventListener("click", () => {
            if (stepIndex > 0) showStep(stepIndex - 1);
        });
    }

    if (nextBtn) {
        nextBtn.addEventListener("click", async () => {
            const ok = await validateCurrentStep();
            if (!ok) return;
            if (stepIndex < 4) {
                showStep(stepIndex + 1);
                return;
            }
            nextBtn.disabled = true;
            await submitSetup();
            nextBtn.disabled = false;
        });
    }

    if (pwd) pwd.addEventListener("input", validatePasswordMatch);
    if (pwdConfirm) pwdConfirm.addEventListener("input", validatePasswordMatch);

    [tzInput, rootInput, backupInput, pwd, pwdConfirm].forEach((node) => {
        if (!node) return;
        node.addEventListener("input", () => {
            const key = node.name || node.id || "";
            clearFieldError(key);
        });
        node.addEventListener("change", () => {
            const key = node.name || node.id || "";
            clearFieldError(key);
        });
    });

    if (backupInput) {
        backupInput.addEventListener("input", () => {
            setCreateOptionVisible("backup_dir", false);
            if (createBackupInput) createBackupInput.checked = false;
        });
    }

    showStep(0);
});

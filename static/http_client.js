(function () {
    async function parseJsonSafe(response) {
        try {
            return await response.json();
        } catch (_) {
            return {};
        }
    }

    async function postForm(path, formData, options = {}) {
        const csrfToken = String(options.csrfToken || "").trim();
        const headers = Object.assign(
            {
                "X-Requested-With": "XMLHttpRequest",
                Accept: "application/json",
            },
            options.headers || {}
        );
        if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
        const response = await fetch(path, {
            method: "POST",
            body: formData,
            headers,
            cache: "no-store",
        });
        const payload = await parseJsonSafe(response);
        return { response, payload };
    }

    async function postUrlEncoded(path, values, options = {}) {
        const csrfToken = String(options.csrfToken || "").trim();
        const body = new URLSearchParams();
        Object.keys(values || {}).forEach((key) => {
            body.set(key, String(values[key] ?? ""));
        });
        const headers = Object.assign(
            {
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                Accept: "application/json",
            },
            options.headers || {}
        );
        if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
        const response = await fetch(path, {
            method: "POST",
            headers,
            body: body.toString(),
            cache: "no-store",
        });
        const payload = await parseJsonSafe(response);
        return { response, payload };
    }

    async function postJson(path, payload, options = {}) {
        const csrfToken = String(options.csrfToken || "").trim();
        const headers = Object.assign(
            {
                "Content-Type": "application/json",
                Accept: "application/json",
            },
            options.headers || {}
        );
        if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
        const response = await fetch(path, {
            method: "POST",
            headers,
            body: JSON.stringify(payload || {}),
            cache: "no-store",
        });
        const body = await parseJsonSafe(response);
        return { response, payload: body };
    }

    async function getJson(path, options = {}) {
        const headers = Object.assign(
            {
                Accept: "application/json",
            },
            options.headers || {}
        );
        const response = await fetch(path, {
            method: "GET",
            headers,
            cache: "no-store",
        });
        const payload = await parseJsonSafe(response);
        return { response, payload };
    }

    window.MCWebHttp = {
        postForm,
        postUrlEncoded,
        postJson,
        getJson,
    };
})();

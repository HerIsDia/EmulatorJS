/**
 * RetroAchievements through a local proxy (see /ra-proxy).
 *
 * The core's RetroArch build (compiled with HAVE_CHEEVOS) evaluates achievements itself; this module
 * only points it at the proxy through retroarch.cfg, then talks to the proxy's REST/SSE API to list
 * achievements and show unlock notifications. Unlocks stay on the proxy and are never sent to
 * retroachievements.org. Hardcore mode is always disabled.
 *
 * Configuration:
 *   EJS_retroAchievements = {
 *       proxyUrl: "https://example.com/ra-proxy", // required
 *       username: "alice",                         // local proxy account
 *       token: "...",                              // local proxy token, or:
 *       password: "...",                           // local proxy password (exchanged for a token)
 *       gameId: 1234,                              // optional RetroAchievements game id (e.g. RomM's ra_id)
 *       notifications: true                        // optional, default true
 *   };
 */
class EJS_Achievements {
    constructor(ejs) {
        this.ejs = ejs;
        const opts = ejs.config.retroAchievements;
        this.enabled = !!(opts && typeof opts === "object" && typeof opts.proxyUrl === "string" && opts.proxyUrl.trim());
        this.events = null;
        this.sessionStarted = false;
        if (!this.enabled) return;
        this.proxyUrl = opts.proxyUrl.trim().replace(/\/+$/, "");
        this.username = typeof opts.username === "string" ? opts.username : "";
        this.token = typeof opts.token === "string" ? opts.token : "";
        this.password = typeof opts.password === "string" ? opts.password : "";
        this.gameId = (typeof opts.gameId === "number" && opts.gameId > 0) ? opts.gameId : null;
        this.defaultNotifications = opts.notifications !== false;
        this.ejs.on("start", () => this.start());
        this.ejs.on("exit", () => this.stop());
    }
    isActive() {
        return this.enabled && this.ejs.preGetSetting("retroachievements") !== "disabled";
    }
    // Set from the core's core.json ("cheevos": true) once cores are built with HAVE_CHEEVOS.
    coreSupported() {
        return this.ejs.coreSupportsCheevos === true || this.sessionStarted;
    }
    notificationsEnabled() {
        const value = this.ejs.getSettingValue("retroachievements-notifications");
        if (value === "disabled") return false;
        if (value === "enabled") return true;
        return this.defaultNotifications;
    }
    getRetroArchCfg() {
        if (!this.isActive()) return "";
        const quote = (value) => "\"" + String(value).replace(/["\n\r]/g, "") + "\"";
        let cfg = "cheevos_enable = \"true\"\n" +
            "cheevos_custom_host = " + quote(this.proxyUrl) + "\n" +
            "cheevos_hardcore_mode_enable = \"false\"\n" +
            // EmulatorJS shows its own unlock notifications; keep RetroArch's leaderboard and challenge widgets only.
            "cheevos_visibility_unlock = \"false\"\n" +
            "cheevos_visibility_mastery = \"false\"\n" +
            "cheevos_visibility_account = \"false\"\n" +
            "cheevos_visibility_summary = \"2\"\n" +
            "cheevos_username = " + quote(this.username) + "\n";
        if (this.token) {
            cfg += "cheevos_token = " + quote(this.token) + "\n";
        } else if (this.password) {
            cfg += "cheevos_password = " + quote(this.password) + "\n";
        }
        return cfg;
    }
    resolveUrl(url) {
        if (typeof url !== "string" || !url) return "";
        return url.startsWith("/") ? this.proxyUrl + url : url;
    }
    async login() {
        if (this.token || !this.password) return !!this.token;
        try {
            const res = await fetch(this.proxyUrl + "/api/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username: this.username, password: this.password })
            });
            if (!res.ok) return false;
            const data = await res.json();
            this.token = data.token || "";
        } catch (e) {
            if (this.ejs.debug) console.warn("[EJS Achievements] Login failed", e);
        }
        return !!this.token;
    }
    authQuery() {
        return "u=" + encodeURIComponent(this.username) + "&t=" + encodeURIComponent(this.token);
    }
    async start() {
        if (!this.isActive()) return;
        if (!(await this.login())) {
            this.ejs.frontend.displayMessage("Could not connect to the achievements server", 5000);
            return;
        }
        if (typeof EventSource === "function" && !this.events) {
            this.events = new EventSource(this.proxyUrl + "/api/events?" + this.authQuery());
            this.events.addEventListener("session", (e) => {
                this.sessionStarted = true;
                try {
                    const data = JSON.parse(e.data);
                    if (data.gameId > 0) this.gameId = data.gameId;
                } catch (err) {}
            });
            this.events.addEventListener("unlock", (e) => {
                this.sessionStarted = true;
                let data;
                try {
                    data = JSON.parse(e.data);
                } catch (err) {
                    return;
                }
                data.badgeUrl = this.resolveUrl(data.badgeUrl);
                data.badgeLockedUrl = this.resolveUrl(data.badgeLockedUrl);
                this.ejs.callEvent("achievementUnlocked", data);
                if (this.notificationsEnabled()) this.ejs.frontend.showAchievementToast(data);
            });
        }
        if (!this.coreSupported() && this.ejs.debug) {
            console.info("[EJS Achievements] This core has not been built with achievements support (HAVE_CHEEVOS).");
        }
    }
    stop() {
        if (this.events) {
            this.events.close();
            this.events = null;
        }
    }
    async fetchGame() {
        if (!this.gameId) return null;
        if (!(await this.login())) throw new Error("login");
        const res = await fetch(this.proxyUrl + "/api/games/" + this.gameId + "?" + this.authQuery());
        if (!res.ok) throw new Error("HTTP " + res.status);
        const data = await res.json();
        for (const achievement of data.achievements) {
            achievement.badgeUrl = this.resolveUrl(achievement.badgeUrl);
            achievement.badgeLockedUrl = this.resolveUrl(achievement.badgeLockedUrl);
        }
        return data;
    }
}

export { EJS_Achievements };

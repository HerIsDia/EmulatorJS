// End-to-end check that an achievement is triggered by the core itself (no simulated request).
// Prerequisites and usage: see tools/cheevos-cores/README.md
// Usage: node e2e.cjs [page URL] [screenshot directory]
const { chromium } = require("playwright");

const pageUrl = process.argv[2] || "http://localhost:8000/tools/cheevos-cores/e2e.html";
const shots = process.argv[3] || ".";
const proxy = "http://localhost:8081";

(async () => {
    const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
    const page = await browser.newPage({ viewport: { width: 820, height: 620 } });
    const logs = [];
    page.on("console", (m) => logs.push(m.text()));
    page.on("pageerror", (e) => logs.push("pageerror: " + e.message));

    await page.goto(pageUrl);
    await page.waitForFunction(() => window.EJS_emulator && window.EJS_emulator.started === true, null, { timeout: 90000 });
    console.log("core declares cheevos:", await page.evaluate(() => window.EJS_emulator.coreSupportsCheevos === true));

    // The ROM flips $0010 from 0 to 1 after ~3 seconds; rcheevos in the core must notice and award achievement 101.
    await page.waitForSelector(".ejs_achievement_toast", { timeout: 60000 });
    console.log("toast:", (await page.innerText(".ejs_achievement_toast")).replace(/\n/g, " | "));
    await page.screenshot({ path: shots + "/e2e-core-toast.png" });
    await page.waitForTimeout(1500);
    await page.screenshot({ path: shots + "/e2e-core-osd.png" });

    const token = await page.evaluate(() => window.EJS_emulator.achievements.token);
    const unlocks = await (await fetch(`${proxy}/api/users/alice/unlocks?t=${token}`)).json();
    console.log("unlocks stored by the proxy:", JSON.stringify(unlocks.unlocks.map((u) => u.id)));

    const rcheevos = logs.filter((l) => /RCHEEVOS|cheevos/i.test(l));
    console.log("--- RetroArch achievement logs (" + rcheevos.length + ") ---\n" + rcheevos.slice(0, 40).join("\n"));
    await browser.close();
    if (!unlocks.unlocks.some((u) => u.id === 101)) {
        console.error("E2E FAILED: achievement 101 not unlocked");
        process.exit(1);
    }
})().catch((e) => {
    console.error("E2E FAILED:", e);
    process.exit(1);
});

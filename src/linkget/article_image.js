// Render a saved article with an isolated Chromium process and no web requests.
const { execFileSync, spawn } = require("node:child_process");
const { mkdtempSync, readFileSync, rmSync, writeFileSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { join } = require("node:path");
const { pathToFileURL } = require("node:url");

const [browser, html, output, ffmpeg] = process.argv.slice(2);
const profile = mkdtempSync(join(tmpdir(), "linkget-article-render-"));
const chrome = spawn(browser, [
  "--headless", "--remote-debugging-pipe", `--user-data-dir=${profile}`,
  // Neo's server can outlive this browser and rewrite shared MCP settings.
  "--disable-browseros-server",
  "--no-first-run", "--disable-background-networking", "--no-proxy-server",
  "--host-resolver-rules=MAP * ~NOTFOUND", "about:blank",
], { stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"] });

const pending = new Map();
const failPending = (error) => {
  for (const { reject } of pending.values()) reject(error);
  pending.clear();
};
chrome.on("error", failPending);
chrome.stdio[3].on("error", failPending);
const closed = new Promise((resolve) => chrome.on("close", () => {
  failPending(new Error("The browser closed before rendering the article."));
  resolve();
}));

let buffer = "";
chrome.stdio[4].setEncoding("utf8").on("data", (chunk) => {
  buffer += chunk;
  for (let end = buffer.indexOf("\0"); end !== -1; end = buffer.indexOf("\0")) {
    const { id, error, result } = JSON.parse(buffer.slice(0, end));
    buffer = buffer.slice(end + 1);
    const reply = pending.get(id);
    if (reply) {
      pending.delete(id);
      if (error) reply.reject(new Error(error.message));
      else reply.resolve(result);
    }
  }
});

let id = 0;
function send(method, params = {}, sessionId) {
  return new Promise((resolve, reject) => {
    pending.set(++id, { resolve, reject });
    chrome.stdio[3].write(JSON.stringify({ id, method, params, sessionId }) + "\0");
  });
}

async function prepareArticle() {
  // Keep the author and the original source in the share image.
  document.querySelector(".article-footer > div + div")?.remove();
  const header = document.querySelector(".article-header");
  const byline = header?.querySelector(".meta");
  if (byline) header.prepend(byline);
  const cover = document.querySelector("main > .cover");
  if (header && cover) header.after(cover);
  const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
  for (const metric of document.querySelectorAll(".metric strong")) {
    const exact = Number(metric.textContent.replaceAll(",", ""));
    if (Number.isSafeInteger(exact) && exact >= 1000) metric.textContent = compact.format(exact);
  }
  const style = document.createElement("style");
  style.textContent = `
    :root {
      color-scheme: light;
      --bg: #fbfaf6; --fg: #20282a; --muted: #4e5958;
      --border: #dadbd0; --card: #eeefe7; --accent: #23524a;
    }
    body { padding: 46px 54px 42px; border-top: 8px solid var(--accent); font-size: 21px; line-height: 1.66; }
    main { max-width: none; }
    header.article-header { padding-bottom: 25px; margin-bottom: 32px; }
    .meta { justify-content: space-between; gap: 14px; font-size: 15px; line-height: 1.4; color: var(--muted); }
    .author { gap: 13px; }
    .author a { color: var(--fg); }
    .author-name { font-size: 18px; font-weight: 700; }
    .author-handle { font-size: 15px; }
    .avatar { width: 52px; height: 52px; flex: none; box-shadow: 0 0 0 1px var(--border); }
    .verified { font-size: 12px; width: 17px; height: 17px; }
    .affiliation { color: var(--accent); background: var(--card); font-size: 12px; }
    .publication { font-size: 14px; }
    .publication .site { color: var(--accent); font-weight: 700; }
    h1 { margin: 25px 0 0; font: 700 52px/1.08 Georgia, "Songti SC", serif; letter-spacing: -0.035em; text-wrap: balance; overflow-wrap: anywhere; }
    .metrics { gap: 8px 20px; margin-top: 18px; font-size: 14px; color: var(--muted); }
    .metric strong { color: var(--fg); font-variant-numeric: tabular-nums; }
    .cover { margin-bottom: 32px; }
    .cover img { border-radius: 12px; }
    article p { margin: 0 0 1.1em; }
    article h2, article h3, article h4 { color: var(--accent); line-height: 1.25; margin: 1.3em 0 0.5em; }
    article figure { margin: 1.5em 0; }
    article img { width: 100%; margin: 0; }
    article img + figcaption { margin-top: 10px; }
    figcaption { color: var(--muted); }
    article pre { white-space: pre-wrap; overflow-wrap: anywhere; }
    article :is(th, td) { overflow-wrap: anywhere; }
    article a { text-decoration: underline; text-decoration-color: #9aaea4; text-underline-offset: 0.14em; }
    footer.article-footer { margin-top: 34px; padding: 16px 20px; border: 0; border-radius: 8px; background: var(--card); font-size: 14px; }
    * { animation: none !important; }
  `;
  document.head.append(style);
  const assets = new URL("./assets/", location.href).href;
  const photos = Array.from(document.querySelectorAll("main img"));
  await Promise.all(photos.map(async (image) => {
    const optional = image.matches(".avatar, .affiliation img");
    try {
      if (!image.src.startsWith(assets)) throw new Error("Missing photo");
      image.loading = "eager";
      await image.decode();
    } catch {
      if (!optional) throw new Error("An article photo is missing or could not be decoded.");
      image.remove();
    }
  }));
  await document.fonts.ready;
  return Math.ceil(document.documentElement.scrollHeight);
}

async function render() {
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  const page = (method, params) => send(method, params, sessionId);
  const width = 800;
  const scale = 3;
  await page("Emulation.setDeviceMetricsOverride", {
    width, height: 600, deviceScaleFactor: scale, mobile: false,
  });
  await page("Emulation.setEmulatedMedia", {
    features: [{ name: "prefers-color-scheme", value: "light" }],
  });
  await page("Network.enable");
  await page("Network.setBlockedURLs", { urls: ["http://*", "https://*"] });
  const url = pathToFileURL(html).href;
  const navigation = await page("Page.navigate", { url });
  if (navigation.errorText) throw new Error("Could not open the saved article.");
  const expression = `(async () => {
    if (location.href !== ${JSON.stringify(url)}) return { ready: false };
    if (document.readyState === "loading") {
      await new Promise((resolve) => document.addEventListener("DOMContentLoaded", resolve, { once: true }));
    }
    if (!document.querySelector("main article")) throw new Error("The article layout is missing.");
    return { ready: true, height: await (${prepareArticle.toString()})() };
  })()`;
  let result;
  do {
    const response = await page("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (response.exceptionDetails) {
      if (!response.exceptionDetails.exception) continue; // Navigation replaced the JS context.
      throw new Error(response.exceptionDetails.exception.description.split("\n")[0]);
    }
    result = response.result.value;
    if (!result.ready) await new Promise((resolve) => setTimeout(resolve, 30));
  } while (!result?.ready);
  const height = result.height;
  if (height * scale > 65535 || width * height * scale ** 2 > 120_000_000) {
    throw new Error("This article is too long for one full-resolution PNG.");
  }
  // Chromium can wrap its rendered surface on very tall captures even when
  // the returned PNG has the requested dimensions. Render shorter bands.
  const tileHeight = 4000;
  if (height > tileHeight && !ffmpeg) throw new Error("FFmpeg is required to render very tall articles.");
  const tiles = [];
  for (let y = 0; y < height; y += tileHeight) {
    const rows = Math.min(tileHeight, height - y);
    const { data } = await page("Page.captureScreenshot", {
      format: "png", captureBeyondViewport: true,
      clip: { x: 0, y, width, height: rows, scale: 1 },
    });
    const png = Buffer.from(data, "base64");
    checkSize(png, width * scale, rows * scale);
    const file = join(profile, `tile-${tiles.length}.png`);
    writeFileSync(file, png);
    tiles.push(file);
  }
  let png = readFileSync(tiles[0]);
  if (tiles.length > 1) {
    const joined = join(profile, "joined.png");
    try {
      execFileSync(ffmpeg, [
        "-hide_banner", "-loglevel", "error", "-nostdin",
        ...tiles.flatMap((file) => ["-i", file]),
        "-filter_complex", `vstack=inputs=${tiles.length}`,
        "-frames:v", "1", "-threads", "1", joined,
      ], { timeout: 20000 });
    } catch {
      throw new Error("Could not join the article image sections.");
    }
    png = readFileSync(joined);
    checkSize(png, width * scale, height * scale);
  }
  writeFileSync(output, png, { flag: "wx", mode: 0o600 });
}

function checkSize(png, width, height) {
  if (png.length < 24 || png.readUInt32BE(16) !== width || png.readUInt32BE(20) !== height) {
    throw new Error("The browser did not capture the entire article at full resolution.");
  }
}

const timer = setTimeout(() => {
  failPending(new Error("Rendering the article exceeded 45 seconds."));
  chrome.kill("SIGKILL");
}, 45000);
render().catch((error) => {
  process.stderr.write(error.message + "\n");
  process.exitCode = 1;
}).finally(async () => {
  chrome.kill();
  const stop = setTimeout(() => chrome.kill("SIGKILL"), 2000);
  await closed;
  clearTimeout(stop);
  clearTimeout(timer);
  rmSync(profile, { recursive: true, force: true });
});

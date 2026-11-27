import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  detectArticleArchive,
  formatBytes,
  formatCompactTaskDate,
  formatDuration,
  formatTaskDate,
  readArticleLocalMeta,
  readArticleMarkdown,
  rewriteArticleImageLinksForRaycast,
} from "../src/lib/articles.ts";
import {
  augmentedEnv,
  buildCleanArgs,
  buildDownloadArgs,
  buildInspectArgs,
  buildReadArgs,
  buildTasksArgs,
  effectiveSaveMode,
  extractUrlInput,
  isArticleFolderRequiredError,
  isArticleInspectTarget,
  normalizeLinkgetError,
  parseLinkgetJson,
  resolveDefaultDownloadTarget,
  resolveEffectiveArticleFolder,
  resolveEffectiveAudioFolder,
  resolveInputTarget,
  resolveLinkgetBinary,
  resolveReadOutputFolder,
  runLinkgetJson,
  runLinkgetJsonDetached,
} from "../src/lib/cli.ts";
import {
  detectArticleFolderFromFiles,
  formatMachineFilenameTitle,
  inferTitleFromArchiveFolder,
  isPhotosLibraryFile,
  shortenHomePath,
  summarizeMediaFiles,
} from "../src/lib/task-format.ts";

test("augmentedEnv prepends Homebrew paths cleanly and disables update checks", () => {
  const env = augmentedEnv({ PATH: "/usr/bin:/opt/homebrew/bin:/bin" });
  const parts = env.PATH.split(path.delimiter);
  assert.equal(parts[0], "/opt/homebrew/bin");
  assert.equal(parts[1], "/usr/local/bin");
  assert.equal(parts.filter((p) => p === "/opt/homebrew/bin").length, 1);
  assert.equal(env.LINKGET_NO_UPDATE_CHECK, "1");
});

test("extractUrlInput and resolveInputTarget handle URLs, share text, and local paths", () => {
  assert.equal(
    extractUrlInput("https://x.com/404jsh/status/2124506790020948428"),
    "https://x.com/404jsh/status/2124506790020948428",
  );
  assert.equal(
    extractUrlInput("38 【分享】看这篇 https://xhslink.com/o/abc123 复制打开！"),
    "https://xhslink.com/o/abc123",
  );
  assert.equal(
    extractUrlInput("See (https://en.wikipedia.org/wiki/Python_(language))."),
    "https://en.wikipedia.org/wiki/Python_(language)",
  );
  assert.equal(extractUrlInput("just plain text without links"), null);
  assert.equal(resolveInputTarget("/tmp/sample.mp4"), "/tmp/sample.mp4");
  assert.equal(resolveInputTarget("not a path or url"), null);
});

test("argument builders produce exact CLI flag arrays", () => {
  assert.deepEqual(
    buildInspectArgs("https://x.com/u/status/1", {
      article: true,
      noNetwork: true,
      browser: "chrome",
    }),
    ["inspect", "https://x.com/u/status/1", "--article", "--no-network", "--browser", "chrome", "--json"],
  );

  assert.deepEqual(
    buildDownloadArgs("https://youtu.be/abc", {
      mode: "audio",
      audioFormat: "mp3",
      folder: "/tmp/Audio",
      clip: "0:30-1:45",
      maxDuration: "10m",
      maxSize: "100MB",
      browser: "none",
    }),
    [
      "https://youtu.be/abc",
      "--audio-format",
      "mp3",
      "--folder",
      "/tmp/Audio",
      "--clip",
      "0:30-1:45",
      "--max-duration",
      "10m",
      "--max-size",
      "100MB",
      "--browser",
      "none",
      "--non-interactive",
      "--json",
    ],
  );

  assert.deepEqual(
    buildDownloadArgs("https://x.com/u/status/2", {
      mode: "article",
      noAssets: true,
      folder: "/tmp/Articles",
    }),
    [
      "https://x.com/u/status/2",
      "--article",
      "--no-assets",
      "--folder",
      "/tmp/Articles",
      "--non-interactive",
      "--json",
    ],
  );

  assert.deepEqual(
    buildReadArgs("/tmp/clip.mp4", { frameOcr: true, browser: "none" }),
    ["read", "/tmp/clip.mp4", "--frame-ocr", "--browser", "none", "--non-interactive", "--json"],
  );

  assert.deepEqual(buildTasksArgs({ limit: 50, kind: "download" }), [
    "tasks",
    "--kind",
    "download",
    "--limit",
    "50",
    "--json",
    "--non-interactive",
  ]);

  assert.deepEqual(buildCleanArgs({ olderThan: "7d", apply: true }), [
    "clean",
    "--older-than",
    "7d",
    "--apply",
    "--json",
    "--non-interactive",
  ]);
});

test("folder resolution respects CLI config -> Raycast pref -> Downloads fallback", () => {
  const prefs = {
    defaultArticleFolder: "/tmp/CustomArticles",
    defaultMediaFolder: "/tmp/CustomMedia",
    defaultBrowser: "auto",
  };
  assert.equal(
    resolveEffectiveArticleFolder(prefs, {
      ok: true,
      article_destination: { mode: "folder", path: "/tmp/ConfigArticles" },
      destination: { mode: "photos" },
    }),
    "/tmp/ConfigArticles",
  );
  assert.equal(
    resolveEffectiveArticleFolder(prefs, {
      ok: true,
      article_destination: null,
      destination: { mode: "photos" },
    }),
    "/tmp/CustomArticles",
  );
  assert.equal(
    resolveEffectiveArticleFolder({ defaultBrowser: "auto" }, null),
    path.join(os.homedir(), "Downloads"),
  );
  assert.equal(isArticleFolderRequiredError("Article downloads require a folder."), true);
  assert.equal(
    isArticleFolderRequiredError({
      message: "Article downloads require a folder. Use --folder [PATH].",
    }),
    true,
  );
  assert.equal(
    isArticleFolderRequiredError({
      message: "Articles cannot be imported into Photos. Use --folder [PATH] or omit --photos.",
    }),
    true,
  );
  assert.equal(
    isArticleFolderRequiredError({
      message: "--live-photos cannot be used with article downloads.",
    }),
    true,
  );
  assert.equal(isArticleFolderRequiredError("Network timeout"), false);
  assert.equal(
    resolveEffectiveAudioFolder(prefs, {
      ok: true,
      audio_destination: null,
      destination: { mode: "photos" },
    }),
    "/tmp/CustomMedia",
  );

  const defaultPrefs = { defaultBrowser: "auto" };
  assert.deepEqual(
    resolveDefaultDownloadTarget("media", defaultPrefs, {
      ok: true,
      destination: { mode: "cwd" },
    }),
    { destination: "photos" },
  );
  assert.deepEqual(
    resolveDefaultDownloadTarget("article", defaultPrefs, {
      ok: true,
      destination: { mode: "photos" },
      article_destination: null,
    }),
    { destination: "default", folder: path.join(os.homedir(), "Downloads") },
  );
  assert.deepEqual(
    resolveDefaultDownloadTarget("audio", defaultPrefs, {
      ok: true,
      destination: { mode: "photos" },
      audio_destination: null,
    }),
    { destination: "default", folder: path.join(os.homedir(), "Downloads") },
  );
  assert.deepEqual(
    resolveDefaultDownloadTarget(
      "auto",
      defaultPrefs,
      {
        ok: true,
        destination: { mode: "photos" },
        article_destination: { mode: "folder", path: "~/Downloads" },
      },
      { ok: true, domain: "x.com", engine: "gallery-dl" },
    ),
    { destination: "default" },
  );
  assert.deepEqual(
    resolveDefaultDownloadTarget(
      "auto",
      defaultPrefs,
      {
        ok: true,
        destination: { mode: "cwd" },
        article_destination: null,
      },
      { ok: true, domain: "x.com", engine: "gallery-dl" },
    ),
    { destination: "photos" },
  );
  assert.deepEqual(
    resolveDefaultDownloadTarget(
      "auto",
      defaultPrefs,
      {
        ok: true,
        destination: { mode: "cwd" },
      },
      null,
    ),
    { destination: "photos" },
  );
  assert.deepEqual(normalizeLinkgetError("Rate limited"), {
    title: "Command Failed",
    message: "Rate limited",
    retryable: false,
  });
  assert.deepEqual(
    normalizeLinkgetError({
      category: "network",
      label: "Timeout",
      message: "Timed out",
      next_step: "Retry later",
      retryable: true,
    }),
    {
      title: "Timeout",
      message: "Timed out",
      nextStep: "Retry later",
      category: "network",
      retryable: true,
    },
  );
});

test("parseLinkgetJson extracts clean or trailing JSON and structured errors", () => {
  const clean = parseLinkgetJson('{"ok":true,"version":"0.3.2"}', "", 0);
  assert.equal(clean.ok, true);
  assert.equal(clean.version, "0.3.2");

  const noisy = parseLinkgetJson(
    'Starting job...\n{"ok":false,"error":{"category":"network","message":"Timed out","retryable":true}}\n',
    "",
    1,
  );
  assert.equal(noisy.ok, false);
  assert.equal(noisy.error.retryable, true);

  assert.throws(
    () => parseLinkgetJson("", "fatal shell error", 2),
    /fatal shell error/,
  );
});

test("runLinkgetJson runs executable stubs cleanly, parses non-zero error JSON, and supports AbortSignal", async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "linkget-raycast-cli-"));
  const stubBin = path.join(tmpDir, "linkget-stub");
  fs.writeFileSync(
    stubBin,
    `#!/bin/sh
if [ "$1" = "ok" ]; then
  printf '{"ok":true,"command":"test","path_check":"%s"}\\n' "$LINKGET_NO_UPDATE_CHECK"
  exit 0
elif [ "$1" = "fail" ]; then
  printf '{"ok":false,"error":{"category":"auth","label":"Login required","message":"Need login","retryable":false}}\\n'
  exit 1
elif [ "$1" = "slow" ]; then
  sleep 5
  printf '{"ok":true}\\n'
  exit 0
fi
`,
    { mode: 0o755 },
  );

  try {
    assert.equal(resolveLinkgetBinary(stubBin), stubBin);

    const okRes = await runLinkgetJson(["ok"], { binaryPath: stubBin });
    assert.equal(okRes.ok, true);
    assert.equal(okRes.path_check, "1");

    const failRes = await runLinkgetJson(["fail"], { binaryPath: stubBin });
    assert.equal(failRes.ok, false);
    assert.equal(failRes.error.category, "auth");

    const ac = new AbortController();
    setTimeout(() => ac.abort(), 50);
    await assert.rejects(
      () => runLinkgetJson(["slow"], { binaryPath: stubBin, signal: ac.signal }),
      { name: "AbortError" },
    );
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("article helpers read saved bundle metadata, markdown, and tolerate missing folders", async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "linkget-raycast-art-"));
  const artDir = path.join(tmpDir, "2026-11-24-demo-article");
  const assetsDir = path.join(artDir, "assets");
  fs.mkdirSync(assetsDir, { recursive: true });

  const htmlPath = path.join(artDir, "article.html");
  const mdPath = path.join(artDir, "article.md");
  const jsonPath = path.join(artDir, "content.json");
  const coverPath = path.join(assetsDir, "cover.jpg");

  fs.writeFileSync(htmlPath, "<html><body>Hello</body></html>");
  fs.writeFileSync(
    mdPath,
    "---\ntitle: Test Title\n---\n\n# Test Title\n\nBody paragraph ![cover](assets/cover.jpg)\n",
  );
  fs.writeFileSync(
    jsonPath,
    JSON.stringify({
      title: "Test Title",
      author: "Alice",
      author_handle: "@alice",
      site: "X Article",
      published_at: "2026-11-24",
      canonical_url: "https://x.com/alice/article/123",
      reference: "x.com/alice/article/123",
      cover_local_path: "assets/cover.jpg",
      assets_downloaded: 1,
      assets_failed: 0,
      assets: [{ status: "downloaded", local_path: "assets/cover.jpg" }],
    }),
  );
  fs.writeFileSync(coverPath, Buffer.from([0xff, 0xd8, 0xff, 0xd9]));

  try {
    const detected = detectArticleArchive([htmlPath, mdPath, jsonPath, coverPath], artDir);
    assert.ok(detected);
    assert.equal(detected.folderPath, artDir);
    assert.equal(detected.coverPath, coverPath);

    const meta = await readArticleLocalMeta(detected);
    assert.equal(meta.exists, true);
    assert.equal(meta.title, "Test Title");
    assert.equal(meta.author, "Alice");
    assert.equal(meta.site, "X Article");
    assert.equal(meta.coverPath, coverPath);
    assert.equal(meta.assetsDownloaded, 1);
    assert.match(meta.excerpt || "", /Body paragraph/);

    const md = await readArticleMarkdown(mdPath);
    const rewritten = rewriteArticleImageLinksForRaycast(md.markdown, artDir);
    assert.match(rewritten, /file:\/\//);

    const missingMeta = await readArticleLocalMeta({
      folderPath: path.join(tmpDir, "missing-dir"),
      htmlPath: path.join(tmpDir, "missing-dir", "article.html"),
      mdPath: null,
      jsonPath: null,
      txtPath: null,
      sourcePath: null,
      coverPath: null,
      assetPaths: [],
    });
    assert.equal(missingMeta.exists, false);

    assert.equal(formatBytes(1536), "1.5 KB");
    assert.equal(formatDuration(125), "2:05");
    assert.match(formatTaskDate("20261124T105751-6f203dc53c50") || "", /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("isArticleInspectTarget distinguishes actual article targets from ordinary X media posts", () => {
  assert.equal(
    isArticleInspectTarget({
      ok: true,
      site: "X",
      engine: "gallery-dl",
      domain: "x.com",
      supports_article_download: true,
    }),
    false,
  );
  assert.equal(
    isArticleInspectTarget({
      ok: true,
      site: "X Article",
      engine: "x_article",
      domain: "x.com",
      supports_article_download: true,
    }),
    true,
  );
  assert.equal(
    isArticleInspectTarget({
      ok: true,
      site: "WeChat",
      engine: "web_article",
      supports_article_download: true,
    }),
    true,
  );
  assert.equal(
    isArticleInspectTarget({
      ok: true,
      site: "Toutiao",
      engine: "rendered_page",
      domain: "toutiao.com",
      canonical_url: "https://www.toutiao.com/article/123/",
    }),
    true,
  );
});

test("resolveReadOutputFolder returns a non-existent leaf under an existing parent and supports content.md archives", () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "linkget-raycast-read-"));
  try {
    const leaf = resolveReadOutputFolder(path.join(tmpDir, "notes"));
    assert.equal(fs.existsSync(path.dirname(leaf)), true);
    assert.equal(fs.existsSync(leaf), false);

    const readArchiveDir = path.join(tmpDir, "read-article");
    fs.mkdirSync(path.join(readArchiveDir, "assets"), { recursive: true });
    fs.writeFileSync(path.join(readArchiveDir, "article.html"), "<html></html>");
    fs.writeFileSync(path.join(readArchiveDir, "content.md"), "# Read Title\n");
    fs.writeFileSync(path.join(readArchiveDir, "assets", "cover.jpg"), Buffer.from([1, 2, 3]));

    const detected = detectArticleArchive([], readArchiveDir);
    assert.ok(detected);
    assert.equal(detected.mdPath, path.join(readArchiveDir, "content.md"));

    const escaped = rewriteArticleImageLinksForRaycast(
      "![alt \\[tag\\]](assets/cover.jpg)",
      readArchiveDir,
    );
    assert.match(escaped, /file:\/\//);

    const start = Date.now();
    const tricky = `![${"\\a".repeat(200)}](https://example.com/a.png)`;
    assert.equal(rewriteArticleImageLinksForRaycast(tricky, readArchiveDir), tricky);
    assert.ok(Date.now() - start < 200);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("unified save types preserve Auto detection and map explicit overrides to CLI flags", () => {
  const article = { ok: true, engine: "web_article" };
  assert.equal(effectiveSaveMode("auto", article), "article");
  assert.equal(effectiveSaveMode("video", article), "video");
  assert.equal(effectiveSaveMode("auto", { ok: true, engine: "gallery-dl" }), "auto");
  for (const mode of ["photo", "video"]) {
    const url = "https://example.com/post";
    assert.deepEqual(buildInspectArgs(url, { saveAs: mode, noNetwork: true }), [
      "inspect", url, "--as", mode, "--no-network", "--json",
    ]);
    assert.deepEqual(buildDownloadArgs(url, { mode, destination: "photos", livePhotos: true }), [
      url, "--as", mode, "--photos", "--non-interactive", "--json",
    ]);
  }
  const routed = resolveDefaultDownloadTarget(
    "auto",
    { defaultBrowser: "auto" },
    { ok: true, destination: { mode: "photos" }, save_types: ["auto", "photo", "video", "article"] },
    { ok: true, domain: "x.com", engine: "gallery-dl" },
  );
  assert.deepEqual(routed, {
    destination: "default", articleFolder: path.join(os.homedir(), "Downloads"),
  });
  assert.deepEqual(buildDownloadArgs("https://x.com/user/status/1", { mode: "auto", ...routed }), [
    "https://x.com/user/status/1", "--article-folder", path.join(os.homedir(), "Downloads"),
    "--non-interactive", "--json",
  ]);
});

test("buildDownloadArgs forwards --live-photos for Photos and default destinations, never for article/audio/folder", () => {
  assert.deepEqual(
    buildDownloadArgs("https://xhslink.com/o/live", {
      mode: "auto",
      destination: "photos",
      livePhotos: true,
    }),
    [
      "https://xhslink.com/o/live",
      "--photos",
      "--live-photos",
      "--non-interactive",
      "--json",
    ],
  );
  assert.deepEqual(
    buildDownloadArgs("https://x.com/u/status/1", {
      mode: "auto",
      destination: "default",
      livePhotos: true,
    }),
    [
      "https://x.com/u/status/1",
      "--live-photos",
      "--non-interactive",
      "--json",
    ],
  );
  assert.deepEqual(
    buildDownloadArgs("https://x.com/u/article/1", {
      mode: "article",
      destination: "default",
      folder: "/tmp/Downloads",
      livePhotos: true,
    }),
    [
      "https://x.com/u/article/1",
      "--article",
      "--folder",
      "/tmp/Downloads",
      "--non-interactive",
      "--json",
    ],
  );
  assert.deepEqual(
    buildDownloadArgs("https://x.com/u/article/1", {
      mode: "article",
      folder: "/tmp/Downloads",
      clip: "0:10-0:30",
      maxDuration: "5m",
      maxSize: "100MB",
      livePhotos: true,
    }),
    [
      "https://x.com/u/article/1",
      "--article",
      "--folder",
      "/tmp/Downloads",
      "--non-interactive",
      "--json",
    ],
  );
  assert.deepEqual(
    buildDownloadArgs("https://xhslink.com/o/live", {
      mode: "media",
      destination: "photos",
      clip: "0:05-0:15",
      livePhotos: true,
    }),
    [
      "https://xhslink.com/o/live",
      "--photos",
      "--clip",
      "0:05-0:15",
      "--non-interactive",
      "--json",
    ],
  );
});

test("task-format helpers produce human-readable titles, paths, and dates", () => {
  assert.equal(
    isPhotosLibraryFile("/Users/forge/Pictures/Photos Library.photoslibrary/originals/6/626B5B59-54AD-41A9-9922-F538BEBF6D3B.jpeg"),
    true,
  );
  assert.equal(isPhotosLibraryFile("/Users/forge/Downloads/video.mp4"), false);

  const summary = summarizeMediaFiles([
    "/Users/forge/Pictures/Photos Library.photoslibrary/originals/6/626B5B59-54AD-41A9-9922-F538BEBF6D3B.jpeg",
    "/Users/forge/Pictures/Photos Library.photoslibrary/originals/B/B0F09614-FB1F-4EE9-AB1D-890A41476AF1.jpeg",
  ]);
  assert.equal(summary.photoCount, 2);
  assert.deepEqual(summary.extensions, [".jpeg"]);
  assert.equal(summary.label, "2 Photos");

  const folder = detectArticleFolderFromFiles([
    "/tmp/linkget-v032-smoke/2026-11-22-404jsh-一个月-0-美元-全球20-节点/source/x-article.json",
  ]);
  assert.equal(folder, "/tmp/linkget-v032-smoke/2026-11-22-404jsh-一个月-0-美元-全球20-节点");
  assert.equal(
    inferTitleFromArchiveFolder(folder),
    "一个月 0 美元 全球20 节点",
  );
  assert.equal(
    inferTitleFromArchiveFolder("/tmp/2026-11-24-404jsh-how-to-build-a-raycast-extension"),
    "how to build a raycast extension",
  );
  assert.equal(
    inferTitleFromArchiveFolder("/tmp/2026-11-24-alice-my-2026-年终总结"),
    "my 2026 年终总结",
  );
  assert.equal(
    detectArticleFolderFromFiles(["/tmp/2026-11-24-clips/douyin_123.mp4"]),
    null,
  );

  assert.deepEqual(
    formatMachineFilenameTitle(["/tmp/douyin_7710575727527169280_001_clean.mp4"]),
    { platform: "Douyin", title: "Douyin Video" },
  );
  assert.deepEqual(
    formatMachineFilenameTitle(["/tmp/bilibili_BV1NSy8z4EZc.mp4"]),
    { platform: "Bilibili", title: "Bilibili · BV1NSy8z4EZc" },
  );
  assert.equal(formatMachineFilenameTitle(["/tmp/vimeo_12345.mp4"]).platform, "Vimeo");
  assert.equal(formatMachineFilenameTitle(["/tmp/dailymotion_x123abc.mp4"]).platform, "Dailymotion");
  assert.equal(formatMachineFilenameTitle(["/tmp/pornhub_phexample.mp4"]).platform, "Pornhub");

  const now = new Date(2026, 10, 24, 21, 0, 0); // local 2026-11-24
  assert.match(formatCompactTaskDate("20261124T123540-77a6892bf380", now) || "", /^\d{2}:\d{2}$/);
  assert.equal(formatCompactTaskDate("20261121T040130-abcdef123456", now), "Nov 21");
  assert.equal(formatCompactTaskDate("20251015T040130-abcdef123456", now), "2025-10-15");
  assert.equal(shortenHomePath(path.join(os.homedir(), "Downloads", "a.mp4")), "~/Downloads/a.mp4");
});

test("runLinkgetJsonDetached detaches running child on abort without killing it", async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "linkget-raycast-detach-"));
  const markerPath = path.join(tmpDir, "finished.txt");
  const scriptBin = path.join(tmpDir, "detached-stub");
  fs.writeFileSync(
    scriptBin,
    `#!/bin/sh
if [ "$1" = "fast" ]; then
  printf '{"ok":true,"status":"completed"}\\n'
  exit 0
fi
sleep 0.25
printf 'done' > "${markerPath}"
printf '{"ok":true,"status":"completed"}\\n'
`,
    { mode: 0o755 },
  );

  try {
    const quick = await runLinkgetJsonDetached(["fast"], {
      binaryPath: scriptBin,
      detachOnAbort: true,
    });
    assert.equal(quick.ok, true);

    const ac = new AbortController();
    setTimeout(() => ac.abort(), 50);
    await assert.rejects(
      () =>
        runLinkgetJsonDetached(["slow"], {
          binaryPath: scriptBin,
          signal: ac.signal,
          detachOnAbort: true,
        }),
      { name: "AbortError" },
    );

    const deadline = Date.now() + 2000;
    while (!fs.existsSync(markerPath) && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 30));
    }
    assert.equal(fs.existsSync(markerPath), true);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

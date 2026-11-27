import { type ChildProcess, spawn } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type {
  AudioFormat,
  BatchDownloadResponse,
  BrowserOption,
  CleanResponse,
  ConfigResponse,
  DestinationChoice,
  DoctorResponse,
  DownloadResponse,
  ExtensionPreferences,
  InspectResponse,
  LinkgetErrorInfo,
  ReadSubmitResponse,
  RetryResponse,
  SaveMode,
  TasksResponse,
  UpgradeCheckResponse,
} from "./types";

export const DEFAULT_LINKGET_PATH = "/opt/homebrew/bin/linkget";
const HOMEBREW_PATHS = ["/opt/homebrew/bin", "/usr/local/bin"];
const DEFAULT_FAST_TIMEOUT_MS = 30_000;
const DEFAULT_DOWNLOAD_TIMEOUT_MS = 10 * 60_000;
const MAX_OUTPUT_BYTES = 16 * 1024 * 1024;

export function expandHome(inputPath: string): string {
  const trimmed = inputPath.trim();
  if (!trimmed) {
    return "";
  }
  const home = os.homedir();
  if (trimmed === "~") {
    return home;
  }
  if (trimmed.startsWith("~/")) {
    return path.join(home, trimmed.slice(2));
  }
  return trimmed;
}

export function augmentedEnv(
  baseEnv: Record<string, string | undefined> = process.env,
): Record<string, string> {
  const env: Record<string, string> = {};
  for (const [key, value] of Object.entries(baseEnv)) {
    if (value !== undefined) {
      env[key] = value;
    }
  }
  const existingParts = (env.PATH || "")
    .split(path.delimiter)
    .filter((entry) => entry.length > 0);
  const combined: string[] = [];
  const seen = new Set<string>();
  for (const dir of [...HOMEBREW_PATHS, ...existingParts]) {
    if (!seen.has(dir)) {
      seen.add(dir);
      combined.push(dir);
    }
  }
  env.PATH = combined.join(path.delimiter);
  env.LINKGET_NO_UPDATE_CHECK = "1";
  return env;
}

export function resolveLinkgetBinary(preferredPath?: string): string {
  const custom = expandHome(preferredPath || "");
  if (custom && custom !== DEFAULT_LINKGET_PATH) {
    return custom;
  }
  for (const candidate of [
    "/opt/homebrew/bin/linkget",
    "/usr/local/bin/linkget",
  ]) {
    try {
      if (fs.statSync(candidate).isFile()) {
        return candidate;
      }
    } catch {
      // try next candidate
    }
  }
  return "linkget";
}

function stripTrailingUrlPunctuation(rawUrl: string): string {
  let result = rawUrl.replace(/[.,;!?，。！？、'"`]+$/g, "");
  while (result.endsWith(")") || result.endsWith("）")) {
    const open = result.endsWith("）") ? "（" : "(";
    const close = result.slice(-1);
    const openCount = result.split(open).length - 1;
    const closeCount = result.split(close).length - 1;
    if (closeCount > openCount) {
      result = result.slice(0, -1);
    } else {
      break;
    }
  }
  return result;
}

export function extractUrlInputs(raw?: string | null): string[] {
  const text = (raw || "").trim();
  if (!text) {
    return [];
  }
  const matches = text.match(/https?:\/\/[^\s<>"'`，。！？、】【「」]+/gi);
  if (!matches || matches.length === 0) {
    return [];
  }
  const urls: string[] = [];
  const seen = new Set<string>();
  for (const rawMatch of matches) {
    const candidate = stripTrailingUrlPunctuation(rawMatch);
    try {
      const parsed = new URL(candidate);
      if (
        (parsed.protocol === "http:" || parsed.protocol === "https:") &&
        !seen.has(candidate)
      ) {
        seen.add(candidate);
        urls.push(candidate);
      }
    } catch {
      // ignore invalid URL token
    }
  }
  return urls;
}

export function extractUrlInput(raw?: string | null): string | null {
  return extractUrlInputs(raw)[0] ?? null;
}

export function resolveInputTarget(raw?: string | null): string | null {
  const url = extractUrlInput(raw);
  if (url) {
    return url;
  }
  const trimmed = (raw || "").trim();
  if (!trimmed) {
    return null;
  }
  if (trimmed.startsWith("/") || trimmed.startsWith("~/") || trimmed === "~") {
    return expandHome(trimmed);
  }
  return null;
}

export function isArticleInspectTarget(res?: InspectResponse | null): boolean {
  if (!res || !res.ok) {
    return false;
  }
  if (
    res.engine === "article" ||
    res.engine === "web_article" ||
    res.engine === "x_article"
  ) {
    return true;
  }
  if (res.metadata?.is_article === true) {
    return true;
  }
  if (
    res.domain === "toutiao.com" &&
    Boolean(res.canonical_url?.includes("/article/"))
  ) {
    return true;
  }
  return false;
}

export function resolveReadOutputFolder(
  parentFolder: string,
  now: Date = new Date(),
): string {
  const parent = ensureFolderExists(parentFolder);
  const stamp = now
    .toISOString()
    .replace(/[-:]/g, "")
    .replace(/\.\d+Z$/, "");
  for (let attempt = 0; attempt < 16; attempt += 1) {
    const suffix = Math.random().toString(36).slice(2, 8);
    const candidate = path.join(parent, `linkget-read-${stamp}-${suffix}`);
    if (!fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return path.join(parent, `linkget-read-${stamp}-${Date.now()}`);
}

export function resolveEffectiveArticleFolder(
  prefs: ExtensionPreferences,
  config?: ConfigResponse | null,
): string {
  if (
    config?.article_destination?.mode === "folder" &&
    config.article_destination.path
  ) {
    return expandHome(config.article_destination.path);
  }
  if (config?.destination?.mode === "folder" && config.destination.path) {
    return expandHome(config.destination.path);
  }
  if (prefs.defaultArticleFolder && prefs.defaultArticleFolder.trim()) {
    return expandHome(prefs.defaultArticleFolder);
  }
  return path.join(os.homedir(), "Downloads");
}

export function resolveEffectiveAudioFolder(
  prefs: ExtensionPreferences,
  config?: ConfigResponse | null,
): string {
  if (
    config?.audio_destination?.mode === "folder" &&
    config.audio_destination.path
  ) {
    return expandHome(config.audio_destination.path);
  }
  if (config?.destination?.mode === "folder" && config.destination.path) {
    return expandHome(config.destination.path);
  }
  if (prefs.defaultMediaFolder && prefs.defaultMediaFolder.trim()) {
    return expandHome(prefs.defaultMediaFolder);
  }
  return path.join(os.homedir(), "Downloads");
}

export function resolveDefaultDownloadTarget(
  effectiveMode: Exclude<SaveMode, "read">,
  prefs: ExtensionPreferences,
  config?: ConfigResponse | null,
  inspect?: InspectResponse | null,
): { destination: DestinationChoice; folder?: string; articleFolder?: string } {
  if (
    effectiveMode === "article" ||
    (effectiveMode === "auto" && isArticleInspectTarget(inspect))
  ) {
    if (
      config?.x_article_photos &&
      (inspect?.supports_article_photos || inspect?.engine === "x_article")
    ) {
      return { destination: "photos" };
    }
    return {
      destination: "default",
      folder: resolveEffectiveArticleFolder(prefs, config),
    };
  }
  if (
    effectiveMode === "audio" ||
    (effectiveMode === "auto" &&
      Boolean(inspect?.effective_audio_mode || inspect?.audio_only))
  ) {
    return {
      destination: "default",
      folder: resolveEffectiveAudioFolder(prefs, config),
    };
  }
  if (prefs.defaultMediaFolder && prefs.defaultMediaFolder.trim()) {
    return {
      destination: "folder",
      folder: expandHome(prefs.defaultMediaFolder),
    };
  }
  if (
    effectiveMode === "auto" &&
    inspect?.domain === "x.com" &&
    config?.destination?.mode === "photos" &&
    config.save_types
  ) {
    if (config.x_article_photos) {
      return { destination: "default" };
    }
    return {
      destination: "default",
      articleFolder: resolveEffectiveArticleFolder(prefs, config),
    };
  }
  if (
    effectiveMode === "auto" &&
    inspect?.domain === "x.com" &&
    config?.destination?.mode === "photos" &&
    config?.article_destination?.mode === "folder" &&
    Boolean(config.article_destination.path)
  ) {
    return { destination: "default" };
  }
  return { destination: "photos" };
}

export function effectiveSaveMode(
  mode: SaveMode,
  inspect?: InspectResponse | null,
): SaveMode {
  if (mode !== "auto" || !inspect?.ok) return mode;
  if (isArticleInspectTarget(inspect)) return "article";
  if (inspect.effective_audio_mode || inspect.audio_only) return "audio";
  return "auto";
}

export interface InspectOptions {
  audio?: boolean;
  article?: boolean;
  saveAs?: "photo" | "video";
  noNetwork?: boolean;
  browser?: BrowserOption;
}

export function buildInspectArgs(
  target: string,
  opts: InspectOptions = {},
): string[] {
  const args = ["inspect", target];
  if (opts.audio) {
    args.push("--audio");
  }
  if (opts.article) {
    args.push("--article");
  }
  if (opts.saveAs) {
    args.push("--as", opts.saveAs);
  }
  if (opts.noNetwork) {
    args.push("--no-network");
  }
  if (opts.browser) {
    args.push("--browser", opts.browser);
  }
  args.push("--json");
  return args;
}

export interface DownloadOptions {
  mode: Exclude<SaveMode, "read">;
  destination?: DestinationChoice;
  folder?: string;
  articleFolder?: string;
  allowArticlePhotos?: boolean;
  noAssets?: boolean;
  audioFormat?: AudioFormat;
  clip?: string;
  maxDuration?: string;
  maxSize?: string;
  livePhotos?: boolean;
  browser?: BrowserOption;
}

export interface BatchDownloadOptions extends DownloadOptions {
  jobs?: number;
}

export function buildDownloadArgs(
  url: string,
  opts: DownloadOptions,
): string[] {
  const args = [url];
  if (opts.mode === "article") {
    args.push("--article");
  } else if (opts.mode === "photo" || opts.mode === "video") {
    args.push("--as", opts.mode);
  } else if (opts.mode === "audio") {
    if (opts.audioFormat && opts.audioFormat !== "original") {
      args.push("--audio-format", opts.audioFormat);
    } else {
      args.push("--audio");
    }
  }
  if (opts.noAssets && (opts.mode === "article" || opts.mode === "auto")) {
    args.push("--no-assets");
  }
  const allowPhotos =
    opts.mode !== "audio" &&
    (opts.mode !== "article" || Boolean(opts.allowArticlePhotos));
  if (opts.destination === "photos" && allowPhotos) {
    args.push("--photos");
  } else if (opts.folder && opts.folder.trim()) {
    args.push("--folder", expandHome(opts.folder));
  }
  if (opts.mode === "auto" && opts.articleFolder) {
    args.push("--article-folder", expandHome(opts.articleFolder));
  }
  if (opts.mode !== "article" && opts.mode !== "photo") {
    if (opts.clip && opts.clip.trim()) {
      args.push("--clip", opts.clip.trim());
    }
    if (opts.maxDuration && opts.maxDuration.trim()) {
      args.push("--max-duration", opts.maxDuration.trim());
    }
  }
  if (opts.mode !== "article" && opts.maxSize?.trim()) {
    args.push("--max-size", opts.maxSize.trim());
  }
  if (
    opts.livePhotos &&
    (opts.mode === "auto" || opts.mode === "media") &&
    !opts.clip?.trim() &&
    (opts.destination === "photos" ||
      (opts.destination === "default" && !opts.folder?.trim()))
  ) {
    args.push("--live-photos");
  }
  if (opts.browser) {
    args.push("--browser", opts.browser);
  }
  args.push("--non-interactive", "--json");
  return args;
}

export function buildBatchDownloadArgs(
  urls: string[],
  opts: BatchDownloadOptions,
): string[] {
  if (urls.length === 0) {
    throw new Error("At least one URL is required for batch download.");
  }
  const tail = buildDownloadArgs(urls[0], opts).slice(1);
  const args = ["batch", ...urls];
  if (
    typeof opts.jobs === "number" &&
    Number.isFinite(opts.jobs) &&
    opts.jobs >= 1
  ) {
    args.push("--jobs", String(Math.floor(opts.jobs)));
  }
  args.push(...tail);
  return args;
}

export interface ReadOptions {
  folder?: string;
  frames?: boolean;
  frameOcr?: boolean;
  noAssets?: boolean;
  browser?: BrowserOption;
}

export function buildReadArgs(
  target: string,
  opts: ReadOptions = {},
): string[] {
  const args = ["read", target];
  if (opts.folder && opts.folder.trim()) {
    args.push("--folder", expandHome(opts.folder));
  }
  if (opts.frameOcr) {
    args.push("--frame-ocr");
  } else if (opts.frames) {
    args.push("--frames");
  }
  if (opts.noAssets) {
    args.push("--no-assets");
  }
  if (opts.browser) {
    args.push("--browser", opts.browser);
  }
  args.push("--non-interactive", "--json");
  return args;
}

export interface TasksQueryOptions {
  limit?: number;
  kind?: "download" | "read";
}

export function buildTasksArgs(opts: TasksQueryOptions = {}): string[] {
  const args = ["tasks"];
  if (opts.kind) {
    args.push("--kind", opts.kind);
  }
  if (typeof opts.limit === "number" && Number.isFinite(opts.limit)) {
    args.push("--limit", String(Math.max(1, Math.floor(opts.limit))));
  }
  args.push("--json", "--non-interactive");
  return args;
}

export interface CleanOptions {
  olderThan?: string;
  apply?: boolean;
}

export function buildCleanArgs(opts: CleanOptions = {}): string[] {
  const args = ["clean"];
  if (opts.olderThan && opts.olderThan.trim()) {
    args.push("--older-than", opts.olderThan.trim());
  }
  args.push(opts.apply ? "--apply" : "--dry-run");
  args.push("--json", "--non-interactive");
  return args;
}

function tryParseJsonLine(text: string): unknown | undefined {
  const trimmed = text.trim();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) {
    return undefined;
  }
  try {
    return JSON.parse(trimmed);
  } catch {
    return undefined;
  }
}

function scanJsonFromStream(stream: string): unknown | undefined {
  const trimmed = stream.trim();
  if (!trimmed) {
    return undefined;
  }
  const whole = tryParseJsonLine(trimmed);
  if (whole !== undefined) {
    return whole;
  }
  const lines = trimmed.split(/\r?\n/);
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const lineCandidate = tryParseJsonLine(lines[i]);
    if (lineCandidate !== undefined) {
      return lineCandidate;
    }
    if (lines[i].trimStart().startsWith("{")) {
      const sliceCandidate = tryParseJsonLine(lines.slice(i).join("\n"));
      if (sliceCandidate !== undefined) {
        return sliceCandidate;
      }
    }
  }
  return undefined;
}

export function parseLinkgetJson<T>(
  stdout: string,
  stderr: string,
  exitCode: number | null,
): T {
  const fromStdout = scanJsonFromStream(stdout);
  if (
    fromStdout !== undefined &&
    typeof fromStdout === "object" &&
    fromStdout !== null
  ) {
    return fromStdout as T;
  }
  const fromStderr = scanJsonFromStream(stderr);
  if (
    fromStderr !== undefined &&
    typeof fromStderr === "object" &&
    fromStderr !== null
  ) {
    return fromStderr as T;
  }
  const rawSnippet =
    stderr.trim().split(/\r?\n/).filter(Boolean).pop() ||
    stdout.trim().split(/\r?\n/).filter(Boolean).pop() ||
    `linkget exited with code ${exitCode ?? "unknown"} without JSON output`;
  const snippet =
    rawSnippet.length > 400 ? `…${rawSnippet.slice(-400)}` : rawSnippet;
  throw new Error(snippet);
}

export function normalizeLinkgetError(
  err: LinkgetErrorInfo | string | null | undefined,
  fallbackTitle = "Command Failed",
): {
  title: string;
  message: string;
  nextStep?: string;
  category?: string;
  retryable: boolean;
} {
  if (!err) {
    return { title: fallbackTitle, message: fallbackTitle, retryable: false };
  }
  if (typeof err === "string") {
    return { title: fallbackTitle, message: err, retryable: false };
  }
  return {
    title: err.label || fallbackTitle,
    message: err.message || fallbackTitle,
    nextStep: err.next_step || undefined,
    category: err.category || undefined,
    retryable: Boolean(err.retryable),
  };
}

export function isArticleFolderRequiredError(
  err: LinkgetErrorInfo | string | null | undefined,
): boolean {
  if (!err) {
    return false;
  }
  const message = (
    typeof err === "string" ? err : err.message || ""
  ).toLowerCase();
  return (
    message.includes("article downloads require a folder") ||
    message.includes("articles cannot be imported into photos") ||
    message.includes("cannot be used with article downloads")
  );
}

export interface RunCommandOptions {
  binaryPath?: string;
  timeoutMs?: number;
  signal?: AbortSignal;
  cwd?: string;
  env?: Record<string, string | undefined>;
}

function sendSignalToChild(
  child: ChildProcess,
  signal: NodeJS.Signals,
  processGroup: boolean,
): void {
  if (processGroup && process.platform !== "win32" && child.pid) {
    try {
      process.kill(-child.pid, signal);
      return;
    } catch {
      // process group may not exist yet or already exited; fall back to child.kill
    }
  }
  child.kill(signal);
}

function terminateChildWithFallback(
  child: ChildProcess,
  processGroup = false,
): void {
  try {
    sendSignalToChild(child, "SIGTERM", processGroup);
  } catch {
    return;
  }
  const killTimer = setTimeout(() => {
    try {
      sendSignalToChild(child, "SIGKILL", processGroup);
    } catch {
      // already exited
    }
  }, 1500);
  killTimer.unref?.();
  child.once("exit", () => clearTimeout(killTimer));
}

export async function runLinkgetJson<T>(
  args: string[],
  options: RunCommandOptions = {},
): Promise<T> {
  const binary = resolveLinkgetBinary(options.binaryPath);
  const timeoutMs = options.timeoutMs ?? DEFAULT_FAST_TIMEOUT_MS;
  const cwd = options.cwd || os.homedir();
  const env = augmentedEnv(options.env ?? process.env);

  return new Promise<T>((resolve, reject) => {
    if (options.signal?.aborted) {
      const abortErr = new Error("Aborted");
      abortErr.name = "AbortError";
      reject(abortErr);
      return;
    }

    const child = spawn(binary, args, {
      cwd,
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    let stdoutBytes = 0;
    let stderrBytes = 0;
    let settled = false;
    let timeoutTimer: ReturnType<typeof setTimeout> | undefined;

    const cleanup = () => {
      if (timeoutTimer) {
        clearTimeout(timeoutTimer);
      }
      options.signal?.removeEventListener("abort", onAbort);
    };

    const onAbort = () => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      terminateChildWithFallback(child);
      const abortErr = new Error("Command aborted");
      abortErr.name = "AbortError";
      reject(abortErr);
    };

    options.signal?.addEventListener("abort", onAbort, { once: true });

    if (timeoutMs > 0) {
      timeoutTimer = setTimeout(() => {
        if (settled) {
          return;
        }
        settled = true;
        cleanup();
        terminateChildWithFallback(child);
        reject(
          new Error(`linkget timed out after ${Math.round(timeoutMs / 1000)}s`),
        );
      }, timeoutMs);
    }

    child.stdout?.setEncoding("utf8");
    child.stdout?.on("data", (chunk: string) => {
      stdoutBytes += Buffer.byteLength(chunk, "utf8");
      if (stdoutBytes <= MAX_OUTPUT_BYTES) {
        stdout += chunk;
      }
    });

    child.stderr?.setEncoding("utf8");
    child.stderr?.on("data", (chunk: string) => {
      stderrBytes += Buffer.byteLength(chunk, "utf8");
      if (stderrBytes <= MAX_OUTPUT_BYTES) {
        stderr += chunk;
      }
    });

    child.on("error", (error: NodeJS.ErrnoException) => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      if (error.code === "ENOENT") {
        reject(
          new Error(
            `linkget binary not found at "${binary}". Install via Homebrew (brew install Popcornnnnnnnn/tap/linkget) or set Linkget Binary Path in Extension Preferences.`,
          ),
        );
        return;
      }
      reject(error);
    });

    child.on("close", (code) => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      try {
        resolve(parseLinkgetJson<T>(stdout, stderr, code));
      } catch (error) {
        reject(error);
      }
    });
  });
}

export async function runLinkgetText(
  args: string[],
  options: RunCommandOptions = {},
): Promise<{ stdout: string; stderr: string; exitCode: number | null }> {
  const binary = resolveLinkgetBinary(options.binaryPath);
  const timeoutMs = options.timeoutMs ?? DEFAULT_FAST_TIMEOUT_MS;
  const cwd = options.cwd || os.homedir();
  const env = augmentedEnv(options.env ?? process.env);

  return new Promise((resolve, reject) => {
    if (options.signal?.aborted) {
      const abortErr = new Error("Aborted");
      abortErr.name = "AbortError";
      reject(abortErr);
      return;
    }

    const child = spawn(binary, args, {
      cwd,
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    let timeoutTimer: ReturnType<typeof setTimeout> | undefined;

    const cleanup = () => {
      if (timeoutTimer) clearTimeout(timeoutTimer);
      options.signal?.removeEventListener("abort", onAbort);
    };

    const onAbort = () => {
      if (settled) return;
      settled = true;
      cleanup();
      terminateChildWithFallback(child);
      const abortErr = new Error("Command aborted");
      abortErr.name = "AbortError";
      reject(abortErr);
    };

    options.signal?.addEventListener("abort", onAbort, { once: true });

    if (timeoutMs > 0) {
      timeoutTimer = setTimeout(() => {
        if (!settled) {
          settled = true;
          cleanup();
          terminateChildWithFallback(child);
          reject(
            new Error(
              `linkget timed out after ${Math.round(timeoutMs / 1000)}s`,
            ),
          );
        }
      }, timeoutMs);
    }

    child.stdout?.setEncoding("utf8");
    child.stdout?.on("data", (c: string) => {
      stdout += c;
    });
    child.stderr?.setEncoding("utf8");
    child.stderr?.on("data", (c: string) => {
      stderr += c;
    });
    child.on("error", (err: NodeJS.ErrnoException) => {
      if (!settled) {
        settled = true;
        cleanup();
        if (err.code === "ENOENT") {
          reject(
            new Error(
              `linkget binary not found at "${binary}". Install via Homebrew (brew install Popcornnnnnnnn/tap/linkget) or set Linkget Binary Path in Extension Preferences.`,
            ),
          );
          return;
        }
        reject(err);
      }
    });
    child.on("close", (exitCode) => {
      if (!settled) {
        settled = true;
        cleanup();
        resolve({ stdout, stderr, exitCode });
      }
    });
  });
}

export function ensureFolderExists(folderPath: string): string {
  const resolved = expandHome(folderPath);
  fs.mkdirSync(resolved, { recursive: true });
  return resolved;
}

export function inspectUrl(
  target: string,
  opts: InspectOptions = {},
  runOpts: RunCommandOptions = {},
): Promise<InspectResponse> {
  return runLinkgetJson<InspectResponse>(buildInspectArgs(target, opts), {
    ...runOpts,
    timeoutMs: runOpts.timeoutMs ?? (opts.noNetwork ? 12_000 : 35_000),
  });
}

const MAX_DETACHED_FILES_SCAN = 200;

export function cleanupStaleDetachedOutputFiles(
  maxAgeMs = 60 * 60 * 1000,
): void {
  try {
    const tmpDir = os.tmpdir();
    const now = Date.now();
    let checked = 0;
    for (const name of fs.readdirSync(tmpDir)) {
      if (!/^linkget-raycast-[a-z0-9-]+\.(out|err)$/i.test(name)) {
        continue;
      }
      checked += 1;
      if (checked > MAX_DETACHED_FILES_SCAN) {
        break;
      }
      const fullPath = path.join(tmpDir, name);
      try {
        const stat = fs.lstatSync(fullPath);
        if (stat.isFile() && now - stat.mtimeMs >= maxAgeMs) {
          fs.unlinkSync(fullPath);
        }
      } catch {
        // ignore individual temp file errors
      }
    }
  } catch {
    // ignore tmpdir scan errors
  }
}

function readBoundedFileUtf8(filePath: string, maxBytes: number): string {
  try {
    const stat = fs.lstatSync(filePath);
    if (!stat.isFile() || stat.size === 0) {
      return "";
    }
    const fd = fs.openSync(filePath, "r");
    try {
      const length = Math.min(stat.size, maxBytes);
      const offset = Math.max(0, stat.size - length);
      const buffer = Buffer.alloc(length);
      let totalRead = 0;
      while (totalRead < length) {
        const bytesRead = fs.readSync(
          fd,
          buffer,
          totalRead,
          length - totalRead,
          offset + totalRead,
        );
        if (bytesRead <= 0) {
          break;
        }
        totalRead += bytesRead;
      }
      return buffer.toString("utf8", 0, totalRead);
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return "";
  }
}

function tryUnlink(filePath: string): void {
  try {
    fs.unlinkSync(filePath);
  } catch {
    // ignore cleanup failures
  }
}

export async function runLinkgetJsonDetached<T>(
  args: string[],
  options: RunCommandOptions & { detachOnAbort?: boolean } = {},
): Promise<T> {
  const binary = resolveLinkgetBinary(options.binaryPath);
  const timeoutMs = options.timeoutMs ?? DEFAULT_DOWNLOAD_TIMEOUT_MS;
  const cwd = options.cwd || os.homedir();
  const env = augmentedEnv(options.env ?? process.env);
  const detachOnAbort = options.detachOnAbort !== false;

  if (options.signal?.aborted) {
    const abortErr = new Error("Aborted");
    abortErr.name = "AbortError";
    throw abortErr;
  }

  cleanupStaleDetachedOutputFiles();

  const runId = crypto.randomUUID();
  const tmpDir = os.tmpdir();
  const outPath = path.join(tmpDir, `linkget-raycast-${runId}.out`);
  const errPath = path.join(tmpDir, `linkget-raycast-${runId}.err`);

  let outFd: number | undefined;
  let errFd: number | undefined;
  let child: ChildProcess;
  try {
    outFd = fs.openSync(outPath, "w", 0o600);
    errFd = fs.openSync(errPath, "w", 0o600);
    child = spawn(binary, args, {
      cwd,
      env,
      detached: true,
      stdio: ["ignore", outFd, errFd],
    });
  } catch (error) {
    tryUnlink(outPath);
    tryUnlink(errPath);
    throw error;
  } finally {
    if (outFd !== undefined) {
      fs.closeSync(outFd);
    }
    if (errFd !== undefined) {
      fs.closeSync(errFd);
    }
  }
  child.unref();

  return new Promise<T>((resolve, reject) => {
    let settled = false;
    let detachedToBackground = false;
    let timeoutTimer: ReturnType<typeof setTimeout> | undefined;

    const cleanup = () => {
      if (timeoutTimer) {
        clearTimeout(timeoutTimer);
      }
      options.signal?.removeEventListener("abort", onAbort);
    };

    const onAbort = () => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      if (detachOnAbort) {
        detachedToBackground = true;
      } else {
        terminateChildWithFallback(child, true);
        tryUnlink(outPath);
        tryUnlink(errPath);
      }
      const abortErr = new Error(
        detachOnAbort ? "Detached to background" : "Command aborted",
      );
      abortErr.name = "AbortError";
      reject(abortErr);
    };

    options.signal?.addEventListener("abort", onAbort, { once: true });

    if (timeoutMs > 0) {
      timeoutTimer = setTimeout(() => {
        if (settled) {
          return;
        }
        settled = true;
        cleanup();
        terminateChildWithFallback(child, true);
        tryUnlink(outPath);
        tryUnlink(errPath);
        reject(
          new Error(`linkget timed out after ${Math.round(timeoutMs / 1000)}s`),
        );
      }, timeoutMs);
    }

    child.on("error", (error: NodeJS.ErrnoException) => {
      if (settled) {
        tryUnlink(outPath);
        tryUnlink(errPath);
        return;
      }
      settled = true;
      cleanup();
      tryUnlink(outPath);
      tryUnlink(errPath);
      if (error.code === "ENOENT") {
        reject(
          new Error(
            `linkget binary not found at "${binary}". Install via Homebrew (brew install Popcornnnnnnnn/tap/linkget) or set Linkget Binary Path in Extension Preferences.`,
          ),
        );
        return;
      }
      reject(error);
    });

    child.on("close", (code) => {
      if (detachedToBackground) {
        tryUnlink(outPath);
        tryUnlink(errPath);
        return;
      }
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      const stdout = readBoundedFileUtf8(outPath, MAX_OUTPUT_BYTES);
      const stderr = readBoundedFileUtf8(errPath, MAX_OUTPUT_BYTES);
      tryUnlink(outPath);
      tryUnlink(errPath);
      try {
        resolve(parseLinkgetJson<T>(stdout, stderr, code));
      } catch (error) {
        reject(error);
      }
    });
  });
}

function spawnBackgroundCommand(
  args: string[],
  runOpts: RunCommandOptions = {},
): Promise<{ pid?: number }> {
  const binary = resolveLinkgetBinary(runOpts.binaryPath);
  const cwd = runOpts.cwd || os.homedir();
  const env = augmentedEnv(runOpts.env ?? process.env);

  return new Promise<{ pid?: number }>((resolve, reject) => {
    let settled = false;
    const child = spawn(binary, args, {
      cwd,
      env,
      detached: true,
      stdio: "ignore",
    });

    child.once("error", (error: NodeJS.ErrnoException) => {
      if (settled) {
        return;
      }
      settled = true;
      if (error.code === "ENOENT") {
        reject(
          new Error(
            `linkget binary not found at "${binary}". Install via Homebrew (brew install Popcornnnnnnnn/tap/linkget) or set Linkget Binary Path in Extension Preferences.`,
          ),
        );
        return;
      }
      reject(error);
    });

    child.once("spawn", () => {
      if (settled) {
        return;
      }
      settled = true;
      child.unref();
      resolve({ pid: child.pid });
    });
  });
}

export function startBackgroundDownload(
  url: string,
  opts: DownloadOptions,
  runOpts: RunCommandOptions = {},
): Promise<{ pid?: number }> {
  return spawnBackgroundCommand(buildDownloadArgs(url, opts), runOpts);
}

export function startBackgroundBatchDownload(
  urls: string[],
  opts: BatchDownloadOptions,
  runOpts: RunCommandOptions = {},
): Promise<{ pid?: number }> {
  return spawnBackgroundCommand(buildBatchDownloadArgs(urls, opts), runOpts);
}

export function downloadLink(
  url: string,
  opts: DownloadOptions,
  runOpts: RunCommandOptions & { detachOnAbort?: boolean } = {},
): Promise<DownloadResponse> {
  return runLinkgetJsonDetached<DownloadResponse>(
    buildDownloadArgs(url, opts),
    {
      detachOnAbort: true,
      ...runOpts,
      timeoutMs: runOpts.timeoutMs ?? DEFAULT_DOWNLOAD_TIMEOUT_MS,
    },
  );
}

export function downloadBatch(
  urls: string[],
  opts: BatchDownloadOptions,
  runOpts: RunCommandOptions & { detachOnAbort?: boolean } = {},
): Promise<BatchDownloadResponse> {
  return runLinkgetJsonDetached<BatchDownloadResponse>(
    buildBatchDownloadArgs(urls, opts),
    {
      detachOnAbort: true,
      ...runOpts,
      timeoutMs: runOpts.timeoutMs ?? DEFAULT_DOWNLOAD_TIMEOUT_MS,
    },
  );
}

export function submitReadTask(
  target: string,
  opts: ReadOptions = {},
  runOpts: RunCommandOptions = {},
): Promise<ReadSubmitResponse> {
  return runLinkgetJson<ReadSubmitResponse>(buildReadArgs(target, opts), {
    ...runOpts,
    timeoutMs: runOpts.timeoutMs ?? 45_000,
  });
}

export function listTasks(
  opts: TasksQueryOptions = {},
  runOpts: RunCommandOptions = {},
): Promise<TasksResponse> {
  return runLinkgetJson<TasksResponse>(buildTasksArgs(opts), {
    ...runOpts,
    timeoutMs: runOpts.timeoutMs ?? 20_000,
  });
}

export function retryTask(
  taskId: string,
  runOpts: RunCommandOptions = {},
): Promise<RetryResponse> {
  return runLinkgetJson<RetryResponse>(
    ["retry", taskId, "--non-interactive", "--json"],
    {
      ...runOpts,
      timeoutMs: runOpts.timeoutMs ?? DEFAULT_DOWNLOAD_TIMEOUT_MS,
    },
  );
}

export function cancelReadTask(
  taskId: string,
  runOpts: RunCommandOptions = {},
): Promise<{ stdout: string; stderr: string; exitCode: number | null }> {
  return runLinkgetText(["read", "cancel", taskId], {
    ...runOpts,
    timeoutMs: runOpts.timeoutMs ?? 15_000,
  });
}

export function getConfig(
  runOpts: RunCommandOptions = {},
): Promise<ConfigResponse> {
  return runLinkgetJson<ConfigResponse>(["config", "--json"], runOpts);
}

export function setArticleFolderConfig(
  folderPath: string,
  runOpts: RunCommandOptions = {},
): Promise<ConfigResponse> {
  const resolved = ensureFolderExists(folderPath);
  return runLinkgetJson<ConfigResponse>(
    ["config", "--article-folder", resolved, "--json"],
    runOpts,
  );
}

export function clearArticleFolderConfig(
  runOpts: RunCommandOptions = {},
): Promise<ConfigResponse> {
  return runLinkgetJson<ConfigResponse>(
    ["config", "--clear-article-folder", "--json"],
    runOpts,
  );
}

export function setDefaultDestinationConfig(
  mode: "photos" | "folder",
  folderPath?: string,
  runOpts: RunCommandOptions = {},
): Promise<ConfigResponse> {
  const args =
    mode === "photos"
      ? ["config", "--photos", "--json"]
      : [
          "config",
          "--folder",
          ensureFolderExists(folderPath || "~/Downloads"),
          "--json",
        ];
  return runLinkgetJson<ConfigResponse>(args, runOpts);
}

export function getDoctor(
  runOpts: RunCommandOptions = {},
): Promise<DoctorResponse> {
  return runLinkgetJson<DoctorResponse>(["doctor", "--json"], runOpts);
}

export function runClean(
  opts: CleanOptions = {},
  runOpts: RunCommandOptions = {},
): Promise<CleanResponse> {
  return runLinkgetJson<CleanResponse>(buildCleanArgs(opts), runOpts);
}

export function checkUpgrade(
  runOpts: RunCommandOptions = {},
): Promise<UpgradeCheckResponse> {
  return runLinkgetJson<UpgradeCheckResponse>(
    ["upgrade", "--check", "--json"],
    {
      ...runOpts,
      timeoutMs: runOpts.timeoutMs ?? 20_000,
    },
  );
}

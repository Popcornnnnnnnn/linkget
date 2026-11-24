import { type ChildProcess, spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type {
  AudioFormat,
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

export function extractUrlInput(raw?: string | null): string | null {
  const text = (raw || "").trim();
  if (!text) {
    return null;
  }
  const matches = text.match(/https?:\/\/[^\s<>"'`，。！？、】【「」]+/gi);
  if (!matches || matches.length === 0) {
    return null;
  }
  const candidate = stripTrailingUrlPunctuation(matches[0]);
  try {
    const parsed = new URL(candidate);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return candidate;
    }
  } catch {
    return null;
  }
  return null;
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
  return path.join(os.homedir(), "Downloads", "Linkget Articles");
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

export function resolveEffectiveMediaFallbackFolder(
  prefs: ExtensionPreferences,
  config?: ConfigResponse | null,
): string | null {
  if (config?.destination?.mode === "folder" && config.destination.path) {
    return expandHome(config.destination.path);
  }
  if (prefs.defaultMediaFolder && prefs.defaultMediaFolder.trim()) {
    return expandHome(prefs.defaultMediaFolder);
  }
  if (config?.destination?.mode === "cwd") {
    return path.join(os.homedir(), "Downloads");
  }
  return null;
}

export interface InspectOptions {
  audio?: boolean;
  article?: boolean;
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
  noAssets?: boolean;
  audioFormat?: AudioFormat;
  clip?: string;
  maxDuration?: string;
  maxSize?: string;
  livePhotos?: boolean;
  browser?: BrowserOption;
}

export function buildDownloadArgs(
  url: string,
  opts: DownloadOptions,
): string[] {
  const args = [url];
  if (opts.mode === "article") {
    args.push("--article");
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
  if (
    opts.destination === "photos" &&
    opts.mode !== "article" &&
    opts.mode !== "audio"
  ) {
    args.push("--photos");
  } else if (opts.folder && opts.folder.trim()) {
    args.push("--folder", expandHome(opts.folder));
  }
  if (opts.clip && opts.clip.trim()) {
    args.push("--clip", opts.clip.trim());
  }
  if (opts.maxDuration && opts.maxDuration.trim()) {
    args.push("--max-duration", opts.maxDuration.trim());
  }
  if (opts.maxSize && opts.maxSize.trim()) {
    args.push("--max-size", opts.maxSize.trim());
  }
  if (opts.livePhotos && opts.destination === "photos") {
    args.push("--live-photos");
  }
  if (opts.browser) {
    args.push("--browser", opts.browser);
  }
  args.push("--non-interactive", "--json");
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
  const snippet =
    stderr.trim().split(/\r?\n/).filter(Boolean).pop() ||
    stdout.trim().split(/\r?\n/).filter(Boolean).pop() ||
    `linkget exited with code ${exitCode ?? "unknown"} without JSON output`;
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

export interface RunCommandOptions {
  binaryPath?: string;
  timeoutMs?: number;
  signal?: AbortSignal;
  cwd?: string;
  env?: Record<string, string | undefined>;
}

function terminateChildWithFallback(child: ChildProcess): void {
  try {
    child.kill("SIGTERM");
  } catch {
    return;
  }
  const killTimer = setTimeout(() => {
    try {
      child.kill("SIGKILL");
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

export function downloadLink(
  url: string,
  opts: DownloadOptions,
  runOpts: RunCommandOptions = {},
): Promise<DownloadResponse> {
  return runLinkgetJson<DownloadResponse>(buildDownloadArgs(url, opts), {
    ...runOpts,
    timeoutMs: runOpts.timeoutMs ?? DEFAULT_DOWNLOAD_TIMEOUT_MS,
  });
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

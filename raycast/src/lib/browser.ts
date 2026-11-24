import { getFrontmostApplication } from "@raycast/api";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

interface BrowserScriptEntry {
  bundleIds: string[];
  names: string[];
  script: string;
}

const BROWSER_SCRIPTS: BrowserScriptEntry[] = [
  {
    bundleIds: ["com.apple.Safari"],
    names: ["Safari"],
    script:
      'tell application "Safari" to if (count of documents) > 0 then return URL of front document',
  },
  {
    bundleIds: ["com.google.Chrome"],
    names: ["Google Chrome"],
    script:
      'tell application "Google Chrome" to if (count of windows) > 0 then return URL of active tab of front window',
  },
  {
    bundleIds: ["com.google.Chrome.canary"],
    names: ["Google Chrome Canary"],
    script:
      'tell application "Google Chrome Canary" to if (count of windows) > 0 then return URL of active tab of front window',
  },
  {
    bundleIds: ["company.thebrowser.Browser"],
    names: ["Arc"],
    script:
      'tell application "Arc" to if (count of windows) > 0 then return URL of active tab of front window',
  },
  {
    bundleIds: ["com.brave.Browser"],
    names: ["Brave Browser"],
    script:
      'tell application "Brave Browser" to if (count of windows) > 0 then return URL of active tab of front window',
  },
  {
    bundleIds: ["com.microsoft.edgemac"],
    names: ["Microsoft Edge"],
    script:
      'tell application "Microsoft Edge" to if (count of windows) > 0 then return URL of active tab of front window',
  },
  {
    bundleIds: ["org.chromium.Chromium"],
    names: ["Chromium"],
    script:
      'tell application "Chromium" to if (count of windows) > 0 then return URL of active tab of front window',
  },
  {
    bundleIds: ["com.browseros.BrowserClaw"],
    names: ["BrowserOS neo", "BrowserOS"],
    script:
      'tell application "BrowserOS neo" to if (count of windows) > 0 then return URL of active tab of front window',
  },
];

function findMatchingBrowser(
  bundleId?: string,
  appName?: string,
): BrowserScriptEntry | undefined {
  const normalizedBundle = (bundleId || "").toLowerCase();
  const normalizedName = (appName || "").toLowerCase();
  return BROWSER_SCRIPTS.find(
    (entry) =>
      (normalizedBundle &&
        entry.bundleIds.some((id) => id.toLowerCase() === normalizedBundle)) ||
      (normalizedName &&
        entry.names.some((name) => name.toLowerCase() === normalizedName)),
  );
}

export async function getFrontmostBrowserUrl(): Promise<string | null> {
  try {
    const frontApp = await getFrontmostApplication();
    const matched = findMatchingBrowser(frontApp.bundleId, frontApp.name);
    if (!matched) {
      return null;
    }
    const { stdout } = await execFileAsync(
      "/usr/bin/osascript",
      ["-e", matched.script],
      { timeout: 1500 },
    );
    const trimmed = stdout.trim();
    if (!trimmed) {
      return null;
    }
    const parsed = new URL(trimmed);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return trimmed;
    }
    return null;
  } catch {
    return null;
  }
}

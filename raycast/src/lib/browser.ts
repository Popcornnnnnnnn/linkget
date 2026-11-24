import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const FRONTMOST_URL_SCRIPT = `
tell application "System Events"
  set frontApp to name of first application process whose frontmost is true
end tell
if frontApp is "Safari" then
  tell application "Safari" to return URL of front document
else if frontApp is "Google Chrome" or frontApp is "Google Chrome Canary" or frontApp is "Chromium" or frontApp is "Brave Browser" or frontApp is "Microsoft Edge" or frontApp is "BrowserOS" then
  tell application frontApp to return URL of active tab of front window
else if frontApp is "Arc" then
  tell application "Arc" to return URL of active tab of front window
end if
return ""
`;

export async function getFrontmostBrowserUrl(): Promise<string | null> {
  try {
    const { stdout } = await execFileAsync(
      "/usr/bin/osascript",
      ["-e", FRONTMOST_URL_SCRIPT],
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

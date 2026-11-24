/// <reference types="@raycast/api">

/* 🚧 🚧 🚧
 * This file is auto-generated from the extension's manifest.
 * Do not modify manually. Instead, update the `package.json` file.
 * 🚧 🚧 🚧 */

/* eslint-disable @typescript-eslint/ban-types */

type ExtensionPreferences = {
  /** Linkget Binary Path - Path to the installed linkget CLI binary (default: /opt/homebrew/bin/linkget). */
  "linkgetPath": string,
  /** Default Article Archive Folder - Default folder for offline article archives when linkget config --article-folder is not set (falls back to ~/Downloads/Linkget Articles). */
  "defaultArticleFolder"?: string,
  /** Default Media & Audio Folder - Optional folder override for media and audio downloads (blank follows linkget config; audio falls back to ~/Downloads). */
  "defaultMediaFolder"?: string,
  /** Default Browser Session - Browser session source passed to linkget. */
  "defaultBrowser": "auto" | "none" | "chrome" | "safari" | "firefox" | "edge" | "brave",
  /** Auto-Detect Clipboard URL - Automatically pre-fill the URL field from the clipboard when opening Save Link. */
  "autoDetectClipboard": boolean
}

/** Preferences accessible in all the extension's commands */
declare type Preferences = ExtensionPreferences

declare namespace Preferences {
  /** Preferences accessible in the `save-link` command */
  export type SaveLink = ExtensionPreferences & {}
  /** Preferences accessible in the `quick-save-article` command */
  export type QuickSaveArticle = ExtensionPreferences & {}
  /** Preferences accessible in the `browse-tasks` command */
  export type BrowseTasks = ExtensionPreferences & {}
  /** Preferences accessible in the `status-and-tools` command */
  export type StatusAndTools = ExtensionPreferences & {}
}

declare namespace Arguments {
  /** Arguments passed to the `save-link` command */
  export type SaveLink = {}
  /** Arguments passed to the `quick-save-article` command */
  export type QuickSaveArticle = {
  /** Article URL (optional — uses clipboard or active browser tab) */
  "url": string
}
  /** Arguments passed to the `browse-tasks` command */
  export type BrowseTasks = {}
  /** Arguments passed to the `status-and-tools` command */
  export type StatusAndTools = {}
}


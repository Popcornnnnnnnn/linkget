/// <reference types="@raycast/api">

/* 🚧 🚧 🚧
 * This file is auto-generated from the extension's manifest.
 * Do not modify manually. Instead, update the `package.json` file.
 * 🚧 🚧 🚧 */

/* eslint-disable @typescript-eslint/ban-types */

type ExtensionPreferences = {
  /** Linkget Binary Path - Path to the installed linkget CLI binary (default: /opt/homebrew/bin/linkget). */
  "linkgetPath": string,
  /** Default Article Archive Folder - Default folder for offline article archives (defaults to ~/Downloads when unset). */
  "defaultArticleFolder"?: string,
  /** Default Media & Audio Folder - Optional folder for photos and videos (leave blank to save pics and videos to Apple Photos; audio defaults to ~/Downloads). */
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
  /** Preferences accessible in the `status-board` command */
  export type StatusBoard = ExtensionPreferences & {}
}

declare namespace Arguments {
  /** Arguments passed to the `save-link` command */
  export type SaveLink = {}
  /** Arguments passed to the `status-board` command */
  export type StatusBoard = {}
}


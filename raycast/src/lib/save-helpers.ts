import os from "node:os";
import path from "node:path";
import type {
  ConfigResponse,
  DestinationChoice,
  ExtensionPreferences,
  InspectResponse,
  SaveMode,
} from "./types";

function shortenHome(targetPath: string): string {
  const home = os.homedir();
  if (targetPath === home) {
    return "~";
  }
  if (targetPath.startsWith(home + path.sep)) {
    return `~/${targetPath.slice(home.length + 1)}`;
  }
  return targetPath;
}

const X_HOSTS = new Set([
  "x.com",
  "www.x.com",
  "mobile.x.com",
  "twitter.com",
  "www.twitter.com",
  "mobile.twitter.com",
]);

export const BATCH_ALLOWED_MODES: SaveMode[] = [
  "auto",
  "photo",
  "video",
  "article",
  "audio",
];

export function isXUrl(rawUrl?: string | null): boolean {
  if (!rawUrl) {
    return false;
  }
  try {
    const parsed = new URL(rawUrl);
    return X_HOSTS.has(parsed.hostname.toLowerCase());
  } catch {
    return false;
  }
}

export function isXArticlePhotoCapable(
  target?: string | null,
  inspect?: InspectResponse | null,
): boolean {
  return Boolean(
    inspect?.supports_article_photos ||
    inspect?.engine === "x_article" ||
    isXUrl(target),
  );
}

export function allowsPhotosDestination(
  previewMode: SaveMode,
  options: {
    target?: string | null;
    inspect?: InspectResponse | null;
    batchUrls?: string[];
  } = {},
): boolean {
  if (["auto", "media", "photo", "video"].includes(previewMode)) {
    return true;
  }
  if (previewMode === "article") {
    if (options.batchUrls && options.batchUrls.length > 1) {
      return options.batchUrls.every((url) => isXUrl(url));
    }
    return isXArticlePhotoCapable(options.target, options.inspect);
  }
  return false;
}

export function summarizeUrlHosts(urls: string[], maxHosts = 3): string {
  const hosts: string[] = [];
  const seen = new Set<string>();
  for (const raw of urls) {
    try {
      const host = new URL(raw).hostname.replace(/^www\./, "");
      if (host && !seen.has(host)) {
        seen.add(host);
        hosts.push(host);
      }
    } catch {
      // ignore unparseable entry
    }
  }
  if (hosts.length === 0) {
    return "";
  }
  const head = hosts.slice(0, maxHosts).join(", ");
  const remaining = hosts.length - maxHosts;
  return remaining > 0 ? `${head} +${remaining} more` : head;
}

export function buildSavePreviewText(params: {
  isBatchMode: boolean;
  batchUrls: string[];
  previewMode: SaveMode;
  visibleDestination: DestinationChoice;
  destinationName: string;
  mediaType: string;
  inspectResult: InspectResponse | null;
  defaultTarget: {
    destination: DestinationChoice;
    folder?: string;
    articleFolder?: string;
  } | null;
  effectiveArticleFolder: string;
  xArticlePhotosEnabled: boolean;
}): string {
  const {
    isBatchMode,
    batchUrls,
    previewMode,
    visibleDestination,
    destinationName,
    mediaType,
    inspectResult,
    defaultTarget,
    effectiveArticleFolder,
    xArticlePhotosEnabled,
  } = params;

  if (isBatchMode) {
    const modeLabel = previewMode === "auto" ? "auto" : mediaType;
    const hostSummary = summarizeUrlHosts(batchUrls);
    const headline = `Batch save ${batchUrls.length} links (${modeLabel}) to ${destinationName}`;
    return hostSummary ? `${headline}\n${hostSummary}` : headline;
  }

  if (previewMode === "auto" && !inspectResult) {
    return "Detecting what to save…";
  }

  if (
    previewMode === "article" &&
    (visibleDestination === "photos" ||
      (visibleDestination === "default" &&
        defaultTarget?.destination === "photos"))
  ) {
    return "Save X Article image to Apple Photos";
  }

  if (
    previewMode === "auto" &&
    visibleDestination === "default" &&
    defaultTarget?.destination === "default" &&
    !defaultTarget.folder
  ) {
    if (xArticlePhotosEnabled && inspectResult?.domain === "x.com") {
      return "Save media or X Article image to Apple Photos";
    }
    return `Save media to Apple Photos or archive the article in ${shortenHome(effectiveArticleFolder)}`;
  }

  return `Save ${mediaType === "read" ? "extracted text" : mediaType} to ${destinationName}`;
}

export function resolveBatchArticleFallbackFolder(
  mode: Exclude<SaveMode, "read">,
  destination: DestinationChoice,
  batchUrls: string[],
  prefs: ExtensionPreferences,
  config: ConfigResponse | null,
  resolveArticleFolderFn: (
    p: ExtensionPreferences,
    c?: ConfigResponse | null,
  ) => string,
  options?: { noAssets?: boolean },
): string | undefined {
  if (mode !== "auto" || destination !== "default") {
    return undefined;
  }
  if (
    !options?.noAssets &&
    batchUrls.some((url) => isXUrl(url)) &&
    config?.x_article_photos
  ) {
    return undefined;
  }
  return resolveArticleFolderFn(prefs, config);
}

export function resolveBatchDefaultTarget(params: {
  effectiveMode: Exclude<SaveMode, "read">;
  batchUrls: string[];
  allowArticlePhotos: boolean;
  noAssets?: boolean;
  prefs: ExtensionPreferences;
  config?: ConfigResponse | null;
  resolveDefaultTargetFn: (
    effectiveMode: Exclude<SaveMode, "read">,
    prefs: ExtensionPreferences,
    config?: ConfigResponse | null,
    inspect?: InspectResponse | null,
    options?: { noAssets?: boolean },
  ) => {
    destination: DestinationChoice;
    folder?: string;
    articleFolder?: string;
  };
  resolveArticleFolderFn: (
    p: ExtensionPreferences,
    c?: ConfigResponse | null,
  ) => string;
}): {
  destination: DestinationChoice;
  folder?: string;
  articleFolder?: string;
} {
  const {
    effectiveMode,
    batchUrls,
    allowArticlePhotos,
    noAssets,
    prefs,
    config,
    resolveDefaultTargetFn,
    resolveArticleFolderFn,
  } = params;
  const batchInspect: InspectResponse | null =
    effectiveMode === "article" && allowArticlePhotos
      ? { ok: true, supports_article_photos: true, engine: "x_article" }
      : null;
  const resolved = resolveDefaultTargetFn(
    effectiveMode,
    prefs,
    config,
    batchInspect,
    { noAssets },
  );
  if (effectiveMode === "auto") {
    const articleFolder = resolveBatchArticleFallbackFolder(
      effectiveMode,
      "default",
      batchUrls,
      prefs,
      config ?? null,
      resolveArticleFolderFn,
      { noAssets },
    );
    if (!prefs.defaultMediaFolder?.trim()) {
      return articleFolder
        ? { destination: "default", articleFolder }
        : { destination: "default" };
    }
    return articleFolder ? { ...resolved, articleFolder } : resolved;
  }
  return resolved;
}

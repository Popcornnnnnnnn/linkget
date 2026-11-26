import os from "node:os";
import path from "node:path";

const PHOTO_EXTENSIONS = new Set([
  ".jpg",
  ".jpeg",
  ".png",
  ".webp",
  ".heic",
  ".heif",
  ".gif",
  ".avif",
]);

const VIDEO_EXTENSIONS = new Set([".mp4", ".mov", ".mkv", ".webm", ".m4v"]);

const AUDIO_EXTENSIONS = new Set([
  ".mp3",
  ".m4a",
  ".flac",
  ".wav",
  ".opus",
  ".aac",
]);

const UUID_FILENAME_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(\.[a-z0-9]+)?$/i;

const PLATFORM_PREFIX_MAP: [RegExp, string][] = [
  [/^douyin_/i, "Douyin"],
  [/^bilibili_/i, "Bilibili"],
  [/^xhs_|^xiaohongshu_/i, "Xiaohongshu"],
  [/^twitter_|^x_/i, "X"],
  [/^youtube_|^yt_/i, "YouTube"],
  [/^vimeo_/i, "Vimeo"],
  [/^dailymotion_/i, "Dailymotion"],
  [/^pornhub_/i, "Pornhub"],
  [/^instagram_|^ig_/i, "Instagram"],
  [/^tiktok_/i, "TikTok"],
  [/^weibo_/i, "Weibo"],
  [/^reddit_/i, "Reddit"],
  [/^threads_/i, "Threads"],
  [/^kuaishou_/i, "Kuaishou"],
];

export interface MediaSummary {
  photoCount: number;
  videoCount: number;
  audioCount: number;
  otherCount: number;
  extensions: string[];
  label: string;
}

export function isPhotosLibraryFile(filePath: string): boolean {
  if (!filePath) {
    return false;
  }
  if (filePath.includes(".photoslibrary/")) {
    return true;
  }
  return UUID_FILENAME_RE.test(path.basename(filePath));
}

export function summarizeMediaFiles(
  files: string[],
  isAudio = false,
): MediaSummary {
  let photoCount = 0;
  let videoCount = 0;
  let audioCount = 0;
  let otherCount = 0;
  const extensions: string[] = [];
  const seenExt = new Set<string>();

  for (const filePath of files) {
    const ext = path.extname(filePath).toLowerCase();
    if (ext && !seenExt.has(ext)) {
      seenExt.add(ext);
      extensions.push(ext);
    }
    if (PHOTO_EXTENSIONS.has(ext)) {
      photoCount += 1;
    } else if (VIDEO_EXTENSIONS.has(ext)) {
      videoCount += 1;
    } else if (AUDIO_EXTENSIONS.has(ext)) {
      audioCount += 1;
    } else {
      otherCount += 1;
    }
  }

  const parts: string[] = [];
  if (photoCount > 0) {
    parts.push(`${photoCount} ${photoCount === 1 ? "Photo" : "Photos"}`);
  }
  if (videoCount > 0) {
    parts.push(`${videoCount} ${videoCount === 1 ? "Video" : "Videos"}`);
  }
  if (audioCount > 0) {
    parts.push(
      audioCount === 1 ? "1 Audio Track" : `${audioCount} Audio Tracks`,
    );
  }
  if (otherCount > 0 && parts.length === 0) {
    parts.push(`${otherCount} ${otherCount === 1 ? "Item" : "Items"}`);
  }

  let label = parts.join(", ");
  if (!label) {
    label = isAudio ? "Audio to Photos" : "Apple Photos Import";
  }

  return {
    photoCount,
    videoCount,
    audioCount,
    otherCount,
    extensions,
    label,
  };
}

export function inferTitleFromArchiveFolder(folderPath: string): string | null {
  const base = path.basename(folderPath).trim();
  if (!base) {
    return null;
  }
  let stripped = base.replace(/^\d{4}-\d{2}-\d{2}-/, "");
  // Strip single leading tag/handle token (up to first hyphen) when followed by title text
  const handleMatch = stripped.match(/^([a-z0-9_.]+)-(.+)$/i);
  if (handleMatch && handleMatch[2].trim()) {
    stripped = handleMatch[2];
  }
  const readable = stripped.replace(/-/g, " ").replace(/\s+/g, " ").trim();
  return readable || base;
}

export function detectArticleFolderFromFiles(
  savedFiles: string[] = [],
): string | null {
  for (const f of savedFiles) {
    const base = path.basename(f).toLowerCase();
    if (
      base === "article.html" ||
      base === "article.md" ||
      base === "content.md" ||
      base === "content.json"
    ) {
      return path.dirname(f);
    }
    if (
      (base === "x-article.json" || base === "original.html") &&
      path.basename(path.dirname(f)).toLowerCase() === "source"
    ) {
      return path.dirname(path.dirname(f));
    }
  }
  return null;
}

export function formatMachineFilenameTitle(
  files: string[],
  isAudio = false,
): { title: string; platform?: string } {
  if (files.length === 0) {
    return { title: isAudio ? "Audio Download" : "Media Download" };
  }

  const firstBase = path.basename(files[0]);
  let platform: string | undefined;
  let matchedPattern: RegExp | undefined;
  for (const [pattern, name] of PLATFORM_PREFIX_MAP) {
    if (pattern.test(firstBase)) {
      platform = name;
      matchedPattern = pattern;
      break;
    }
  }

  if (platform && matchedPattern) {
    const summary = summarizeMediaFiles(files, isAudio);
    const stem = path
      .basename(firstBase, path.extname(firstBase))
      .replace(matchedPattern, "");
    const firstToken = stem.split("_")[0] || "";
    const isShortAlphaCode =
      /^[a-z0-9]{4,16}$/i.test(firstToken) && /[a-z]/i.test(firstToken);

    if (files.length === 1 && isShortAlphaCode) {
      return { platform, title: `${platform} · ${firstToken}` };
    }
    if (files.length === 1) {
      if (summary.videoCount === 1) {
        return { platform, title: `${platform} Video` };
      }
      if (summary.photoCount === 1) {
        return { platform, title: `${platform} Photo` };
      }
      if (summary.audioCount === 1) {
        return { platform, title: `${platform} Audio` };
      }
    }
    return { platform, title: `${platform} · ${summary.label}` };
  }

  const title =
    files.length > 1 ? `${firstBase} (+${files.length - 1})` : firstBase;
  return { title };
}

export function shortenHomePath(targetPath: string): string {
  const home = os.homedir();
  if (targetPath === home) {
    return "~";
  }
  if (targetPath.startsWith(`${home}${path.sep}`)) {
    return `~/${targetPath.slice(home.length + 1)}`;
  }
  return targetPath;
}

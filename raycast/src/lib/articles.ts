import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import type { ArticleArchiveInfo, ArticleLocalMeta } from "./types";

const MAX_CONTENT_JSON_BYTES = 2 * 1024 * 1024;
const MAX_EXCERPT_READ_BYTES = 24 * 1024;
const DEFAULT_MAX_READER_BYTES = 512 * 1024;

function isRegularFile(filePath: string): boolean {
  try {
    return fs.lstatSync(filePath).isFile();
  } catch {
    return false;
  }
}

function isRealDirectory(dirPath: string): boolean {
  try {
    return fs.lstatSync(dirPath).isDirectory();
  } catch {
    return false;
  }
}

export function detectArticleArchive(
  savedFiles: string[] = [],
  fallbackFolder?: string | null,
): ArticleArchiveInfo | null {
  const htmlFile = savedFiles.find(
    (f) => path.basename(f).toLowerCase() === "article.html",
  );
  const mdFile =
    savedFiles.find((f) => path.basename(f).toLowerCase() === "article.md") ||
    savedFiles.find((f) => path.basename(f).toLowerCase() === "content.md");
  const jsonFile = savedFiles.find(
    (f) => path.basename(f).toLowerCase() === "content.json",
  );
  const txtFile = savedFiles.find(
    (f) => path.basename(f).toLowerCase() === "content.txt",
  );
  const sourceFile =
    savedFiles.find((f) => {
      const base = path.basename(f).toLowerCase();
      return base === "x-article.json" || base === "original.html";
    }) || null;

  const folderPath =
    (htmlFile && path.dirname(htmlFile)) ||
    (mdFile && path.dirname(mdFile)) ||
    (jsonFile && path.dirname(jsonFile)) ||
    (fallbackFolder && fallbackFolder !== "Photos" ? fallbackFolder : null);

  if (!htmlFile && !mdFile) {
    if (!folderPath || !isRealDirectory(folderPath)) {
      return null;
    }
    const candidateHtml = path.join(folderPath, "article.html");
    const candidateMd = path.join(folderPath, "article.md");
    const candidateContentMd = path.join(folderPath, "content.md");
    if (
      !isRegularFile(candidateHtml) &&
      !isRegularFile(candidateMd) &&
      !isRegularFile(candidateContentMd)
    ) {
      return null;
    }
  }

  if (!folderPath) {
    return null;
  }

  const resolvedHtml =
    htmlFile ||
    (isRegularFile(path.join(folderPath, "article.html"))
      ? path.join(folderPath, "article.html")
      : null);
  const resolvedMd =
    mdFile ||
    (isRegularFile(path.join(folderPath, "article.md"))
      ? path.join(folderPath, "article.md")
      : isRegularFile(path.join(folderPath, "content.md"))
        ? path.join(folderPath, "content.md")
        : null);
  const resolvedJson =
    jsonFile ||
    (isRegularFile(path.join(folderPath, "content.json"))
      ? path.join(folderPath, "content.json")
      : null);
  const resolvedTxt =
    txtFile ||
    (isRegularFile(path.join(folderPath, "content.txt"))
      ? path.join(folderPath, "content.txt")
      : null);

  const assetPaths = savedFiles.filter((f) => {
    const rel = path.relative(folderPath as string, f);
    return !rel.startsWith("..") && rel.startsWith(`assets${path.sep}`);
  });
  const coverFromList =
    assetPaths.find((f) => /^cover\./i.test(path.basename(f))) || null;

  return {
    folderPath,
    htmlPath: resolvedHtml,
    mdPath: resolvedMd,
    jsonPath: resolvedJson,
    txtPath: resolvedTxt,
    sourcePath: sourceFile,
    coverPath: coverFromList,
    assetPaths,
  };
}

function cleanMarkdownExcerpt(raw: string, maxChars = 4000): string {
  let body = raw.replace(/\r\n/g, "\n");
  if (body.startsWith("---\n")) {
    const endIdx = body.indexOf("\n---\n", 4);
    if (endIdx !== -1) {
      body = body.slice(endIdx + 5);
    }
  }
  const trimmed = body.trim();
  if (trimmed.length <= maxChars) {
    return trimmed;
  }
  return `${trimmed.slice(0, maxChars).trimEnd()}\n\n…`;
}

async function readPrefixUtf8(
  filePath: string,
  maxBytes: number,
): Promise<string> {
  const handle = await fsp.open(filePath, "r");
  try {
    const stat = await handle.stat();
    const toRead = Math.min(stat.size, maxBytes);
    const buffer = Buffer.alloc(toRead);
    const { bytesRead } = await handle.read(buffer, 0, toRead, 0);
    return buffer.toString("utf8", 0, bytesRead);
  } finally {
    await handle.close();
  }
}

export async function readArticleLocalMeta(
  archive: ArticleArchiveInfo,
): Promise<ArticleLocalMeta> {
  const folderExists = isRealDirectory(archive.folderPath);
  const htmlExists = archive.htmlPath ? isRegularFile(archive.htmlPath) : false;
  const mdExists = archive.mdPath ? isRegularFile(archive.mdPath) : false;
  const jsonExists = archive.jsonPath ? isRegularFile(archive.jsonPath) : false;

  const meta: ArticleLocalMeta = {
    exists: folderExists && (htmlExists || mdExists || jsonExists),
    title: null,
    author: null,
    authorHandle: null,
    site: null,
    publishedAt: null,
    excerpt: null,
    coverPath:
      archive.coverPath && isRegularFile(archive.coverPath)
        ? archive.coverPath
        : null,
    htmlPath: htmlExists ? archive.htmlPath : null,
    mdPath: mdExists ? archive.mdPath : null,
    jsonPath: jsonExists ? archive.jsonPath : null,
    folderPath: archive.folderPath,
    assetCount: archive.assetPaths.length,
    assetsDownloaded: null,
    assetsFailed: null,
    totalBytes: 0,
    reference: null,
    canonicalUrl: null,
  };

  if (!meta.exists) {
    return meta;
  }

  if (jsonExists && archive.jsonPath) {
    try {
      const stat = await fsp.lstat(archive.jsonPath);
      if (stat.isFile() && stat.size <= MAX_CONTENT_JSON_BYTES) {
        const raw = await fsp.readFile(archive.jsonPath, "utf8");
        const parsed = JSON.parse(raw) as Record<string, unknown>;
        if (typeof parsed.title === "string" && parsed.title.trim()) {
          meta.title = parsed.title.trim();
        }
        if (typeof parsed.author === "string" && parsed.author.trim()) {
          meta.author = parsed.author.trim();
        }
        if (
          typeof parsed.author_handle === "string" &&
          parsed.author_handle.trim()
        ) {
          meta.authorHandle = parsed.author_handle.trim();
        }
        if (typeof parsed.site === "string" && parsed.site.trim()) {
          meta.site = parsed.site.trim();
        }
        if (
          typeof parsed.published_at === "string" &&
          parsed.published_at.trim()
        ) {
          meta.publishedAt = parsed.published_at.trim();
        }
        if (typeof parsed.reference === "string" && parsed.reference.trim()) {
          meta.reference = parsed.reference.trim();
        }
        if (
          typeof parsed.canonical_url === "string" &&
          parsed.canonical_url.trim()
        ) {
          meta.canonicalUrl = parsed.canonical_url.trim();
        }
        if (typeof parsed.assets_downloaded === "number") {
          meta.assetsDownloaded = parsed.assets_downloaded;
        }
        if (typeof parsed.assets_failed === "number") {
          meta.assetsFailed = parsed.assets_failed;
        }
        if (
          !meta.coverPath &&
          typeof parsed.cover_local_path === "string" &&
          parsed.cover_local_path.trim()
        ) {
          const candidateCover = path.resolve(
            archive.folderPath,
            parsed.cover_local_path,
          );
          const rel = path.relative(archive.folderPath, candidateCover);
          if (
            !rel.startsWith("..") &&
            !path.isAbsolute(rel) &&
            isRegularFile(candidateCover)
          ) {
            meta.coverPath = candidateCover;
          }
        }
        if (Array.isArray(parsed.assets)) {
          const downloadedCount = parsed.assets.filter(
            (a) =>
              typeof a === "object" &&
              a !== null &&
              (a as Record<string, unknown>).status === "downloaded",
          ).length;
          if (meta.assetsDownloaded === null) {
            meta.assetsDownloaded = downloadedCount;
          }
          meta.assetCount = downloadedCount || meta.assetCount;
        }
        if (
          !meta.excerpt &&
          typeof parsed.text === "string" &&
          parsed.text.trim()
        ) {
          meta.excerpt = cleanMarkdownExcerpt(parsed.text, 2000);
        }
      }
    } catch {
      // corrupt or oversized content.json should not crash listing
    }
  }

  if (!meta.coverPath) {
    const assetsDir = path.join(archive.folderPath, "assets");
    if (isRealDirectory(assetsDir)) {
      try {
        const entries = await fsp.readdir(assetsDir);
        const coverName = entries.find((e) => /^cover\./i.test(e));
        if (coverName) {
          const p = path.join(assetsDir, coverName);
          if (isRegularFile(p)) {
            meta.coverPath = p;
          }
        }
        if (meta.assetCount === 0) {
          meta.assetCount = entries.filter((e) => !e.startsWith(".")).length;
        }
      } catch {
        // ignore asset scan error
      }
    }
  }

  if (mdExists && archive.mdPath) {
    try {
      const mdPrefix = await readPrefixUtf8(
        archive.mdPath,
        MAX_EXCERPT_READ_BYTES,
      );
      const excerpt = cleanMarkdownExcerpt(mdPrefix, 3000);
      if (excerpt) {
        meta.excerpt = excerpt;
      }
      if (!meta.title) {
        const headingMatch = mdPrefix.match(/^#\s+(.+)$/m);
        if (headingMatch?.[1]) {
          meta.title = headingMatch[1].trim();
        }
      }
    } catch {
      // ignore read failure
    }
  }

  for (const file of [
    archive.htmlPath,
    archive.mdPath,
    archive.jsonPath,
    meta.coverPath,
  ]) {
    if (file && isRegularFile(file)) {
      try {
        meta.totalBytes += fs.lstatSync(file).size;
      } catch {
        // ignore
      }
    }
  }

  return meta;
}

export async function readArticleMarkdown(
  mdPath: string,
  maxBytes = DEFAULT_MAX_READER_BYTES,
): Promise<{ markdown: string; truncated: boolean }> {
  const stat = await fsp.lstat(mdPath);
  if (!stat.isFile()) {
    throw new Error(`Not a regular file: ${mdPath}`);
  }
  const truncated = stat.size > maxBytes;
  let content = await readPrefixUtf8(mdPath, maxBytes);
  content = content.replace(/\r\n/g, "\n");
  if (truncated) {
    content = `${content.trimEnd()}\n\n---\n\n> *Truncated at ${formatBytes(maxBytes)} in Raycast preview. Open \`article.html\` or \`article.md\` for the full file.*`;
  }
  return { markdown: content, truncated };
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let idx = 0;
  while (value >= 1024 && idx < units.length - 1) {
    value /= 1024;
    idx += 1;
  }
  const formatted =
    idx === 0
      ? String(Math.round(value))
      : value.toFixed(1).replace(/\.0$/, "");
  return `${formatted} ${units[idx]}`;
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return "0:00";
  }
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = h > 0 ? String(m).padStart(2, "0") : String(m);
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

export function formatTaskDate(taskId: string): string | null {
  const match = taskId.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})-/);
  if (!match) {
    return null;
  }
  const iso = `${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:${match[6]}Z`;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  const y = date.getFullYear();
  const mon = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  const hh = String(date.getHours()).padStart(2, "0");
  const mm = String(date.getMinutes()).padStart(2, "0");
  return `${y}-${mon}-${d} ${hh}:${mm}`;
}

export function rewriteArticleImageLinksForRaycast(
  markdown: string,
  folderPath: string,
): string {
  let realRoot = folderPath;
  try {
    realRoot = fs.realpathSync(folderPath);
  } catch {
    // fallback to provided path
  }
  return markdown.replace(
    /!\[((?:\\.|[^\]])*)\]\((assets\/[^)\s]+)\)/g,
    (_match, alt: string, relPath: string) => {
      const abs = path.resolve(realRoot, relPath);
      const relative = path.relative(realRoot, abs);
      if (relative.startsWith("..") || path.isAbsolute(relative)) {
        return `![${alt}](${relPath})`;
      }
      try {
        const realAbs = fs.realpathSync(abs);
        const realRel = path.relative(realRoot, realAbs);
        if (realRel.startsWith("..") || path.isAbsolute(realRel)) {
          return `![${alt}](${relPath})`;
        }
        return `![${alt}](${pathToFileURL(realAbs).href})`;
      } catch {
        return `![${alt}](${pathToFileURL(abs).href})`;
      }
    },
  );
}

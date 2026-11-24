import {
  Action,
  ActionPanel,
  Color,
  Detail,
  Icon,
  Keyboard,
} from "@raycast/api";
import { useEffect, useState } from "react";
import { pathToFileURL } from "node:url";
import {
  readArticleMarkdown,
  rewriteArticleImageLinksForRaycast,
} from "../lib/articles";
import type { ArticleLocalMeta } from "../lib/types";

export interface ArticleReaderDetailProps {
  meta: ArticleLocalMeta;
}

export function ArticleReaderDetail({ meta }: ArticleReaderDetailProps) {
  const [markdown, setMarkdown] = useState<string>("Loading article…");
  const [rawMarkdown, setRawMarkdown] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setIsLoading(true);
      try {
        if (meta.mdPath) {
          const res = await readArticleMarkdown(meta.mdPath);
          if (cancelled) return;
          setRawMarkdown(res.markdown);
          let rendered = rewriteArticleImageLinksForRaycast(
            res.markdown,
            meta.folderPath,
          );
          if (
            meta.coverPath &&
            !rendered.includes(pathToFileURL(meta.coverPath).href)
          ) {
            const safeTitle = (meta.title || "Cover").replace(/[[\]]/g, "");
            rendered = `![${safeTitle}](${pathToFileURL(meta.coverPath).href})\n\n${rendered}`;
          }
          setMarkdown(rendered);
        } else {
          const fallbackParts: string[] = [];
          if (meta.coverPath) {
            const safeTitle = (meta.title || "Cover").replace(/[[\]]/g, "");
            fallbackParts.push(
              `![${safeTitle}](${pathToFileURL(meta.coverPath).href})`,
            );
          }
          fallbackParts.push(`# ${meta.title || "Saved Article"}`);
          if (meta.excerpt) {
            fallbackParts.push(meta.excerpt);
          } else {
            fallbackParts.push(
              "*No Markdown file found in this archive. Open `article.html` in your browser to view the full saved page.*",
            );
          }
          if (!cancelled) {
            setMarkdown(fallbackParts.join("\n\n"));
          }
        }
      } catch (error) {
        if (!cancelled) {
          setMarkdown(
            `# ${meta.title || "Unable to Load Article"}\n\n${error instanceof Error ? error.message : String(error)}`,
          );
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [meta]);

  const navTitle = meta.title || "Offline Article";
  const authorText = meta.authorHandle
    ? meta.author
      ? `${meta.author} (${meta.authorHandle})`
      : meta.authorHandle
    : meta.author || "Unknown";

  const assetLabel =
    meta.assetsDownloaded !== null
      ? meta.assetsFailed && meta.assetsFailed > 0
        ? `${meta.assetsDownloaded} downloaded, ${meta.assetsFailed} failed`
        : `${meta.assetsDownloaded} local asset${meta.assetsDownloaded === 1 ? "" : "s"}`
      : `${meta.assetCount} asset${meta.assetCount === 1 ? "" : "s"}`;

  return (
    <Detail
      isLoading={isLoading}
      navigationTitle={navTitle}
      markdown={markdown}
      metadata={
        <Detail.Metadata>
          <Detail.Metadata.Label title="Title" text={navTitle} />
          <Detail.Metadata.Label title="Author" text={authorText} />
          {meta.site && <Detail.Metadata.Label title="Site" text={meta.site} />}
          {meta.publishedAt && (
            <Detail.Metadata.Label title="Published" text={meta.publishedAt} />
          )}
          <Detail.Metadata.TagList title="Archive">
            <Detail.Metadata.TagList.Item
              text={assetLabel}
              color={meta.assetsFailed ? Color.Orange : Color.Green}
            />
          </Detail.Metadata.TagList>
          {meta.canonicalUrl && (
            <Detail.Metadata.Link
              title="Original URL"
              target={meta.canonicalUrl}
              text={meta.reference || meta.canonicalUrl}
            />
          )}
          <Detail.Metadata.Separator />
          <Detail.Metadata.Label title="Folder" text={meta.folderPath} />
        </Detail.Metadata>
      }
      actions={
        <ActionPanel>
          <ActionPanel.Section title="Open Archive">
            {meta.htmlPath && (
              <Action.Open
                title="Open HTML Article in Browser"
                icon={Icon.Globe}
                target={meta.htmlPath}
              />
            )}
            <Action.ShowInFinder
              title="Show Archive Folder in Finder"
              path={meta.folderPath}
              shortcut={Keyboard.Shortcut.Common.Open}
            />
            {meta.mdPath && (
              <Action.Open
                title="Open Markdown File"
                icon={Icon.Document}
                target={meta.mdPath}
                shortcut={{ modifiers: ["cmd"], key: "m" }}
              />
            )}
          </ActionPanel.Section>
          <ActionPanel.Section title="Copy">
            {rawMarkdown && (
              <Action.CopyToClipboard
                title="Copy Markdown"
                content={rawMarkdown}
                shortcut={Keyboard.Shortcut.Common.Copy}
              />
            )}
            <Action.CopyToClipboard
              title="Copy Archive Folder Path"
              content={meta.folderPath}
              shortcut={Keyboard.Shortcut.Common.CopyPath}
            />
            {meta.canonicalUrl && (
              <Action.CopyToClipboard
                title="Copy Original URL"
                content={meta.canonicalUrl}
              />
            )}
          </ActionPanel.Section>
        </ActionPanel>
      }
    />
  );
}

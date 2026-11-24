import {
  Clipboard,
  getPreferenceValues,
  LaunchProps,
  open,
  showInFinder,
  showToast,
  Toast,
} from "@raycast/api";
import fs from "node:fs";
import path from "node:path";
import { detectArticleArchive } from "./lib/articles";
import { getFrontmostBrowserUrl } from "./lib/browser";
import {
  downloadLink,
  ensureFolderExists,
  extractUrlInput,
  getConfig,
  normalizeLinkgetError,
  resolveEffectiveArticleFolder,
} from "./lib/cli";
import type { ExtensionPreferences } from "./lib/types";

export default async function QuickSaveArticleCommand(
  props: LaunchProps<{ arguments: { url?: string } }>,
) {
  const prefs = getPreferenceValues<ExtensionPreferences>();

  let targetUrl = extractUrlInput(props.arguments?.url);
  if (!targetUrl) {
    try {
      const clipText = await Clipboard.readText();
      targetUrl = extractUrlInput(clipText);
    } catch {
      // clipboard unavailable
    }
  }
  if (!targetUrl) {
    targetUrl = await getFrontmostBrowserUrl();
  }

  if (!targetUrl) {
    await showToast({
      style: Toast.Style.Failure,
      title: "No Article URL Found",
      message: "Pass a URL argument, copy a link, or focus a browser tab.",
    });
    return;
  }

  const toast = await showToast({
    style: Toast.Style.Animated,
    title: "Archiving Article Offline…",
    message: targetUrl,
  });

  try {
    const config = await getConfig({ binaryPath: prefs.linkgetPath }).catch(
      () => null,
    );
    const baseFolder = ensureFolderExists(
      resolveEffectiveArticleFolder(prefs, config),
    );
    const res = await downloadLink(
      targetUrl,
      {
        mode: "article",
        folder: baseFolder,
        browser: prefs.defaultBrowser || "auto",
      },
      { binaryPath: prefs.linkgetPath },
    );

    if (!res.ok) {
      const err = normalizeLinkgetError(res.error, "Article Archive Failed");
      toast.style = Toast.Style.Failure;
      toast.title = err.title;
      toast.message = err.message;
      return;
    }

    const archive = detectArticleArchive(
      res.saved_files || [],
      res.article_folder || res.destination || baseFolder,
    );
    const folderToReveal =
      archive?.folderPath || res.article_folder || baseFolder;
    const htmlToOpen =
      archive?.htmlPath && fs.existsSync(archive.htmlPath)
        ? archive.htmlPath
        : null;

    toast.style = Toast.Style.Success;
    toast.title = "Article Archived";
    toast.message = res.title || path.basename(folderToReveal);

    if (htmlToOpen) {
      toast.primaryAction = {
        title: "Open HTML in Browser",
        onAction: () => {
          void open(htmlToOpen);
        },
      };
    } else {
      toast.primaryAction = {
        title: "Reveal in Finder",
        onAction: () => {
          void showInFinder(folderToReveal);
        },
      };
    }

    toast.secondaryAction = {
      title: "Reveal in Finder",
      onAction: () => {
        void showInFinder(folderToReveal);
      },
    };
  } catch (error) {
    toast.style = Toast.Style.Failure;
    toast.title = "Article Archive Failed";
    toast.message = error instanceof Error ? error.message : String(error);
  }
}

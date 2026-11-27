export type BrowserOption =
  "auto" | "none" | "chrome" | "firefox" | "safari" | "edge" | "brave";

export type SaveType = "auto" | "photo" | "video" | "article";
export type SaveMode = SaveType | "media" | "audio" | "read";

export type DestinationChoice = "default" | "photos" | "folder";

export type AudioFormat = "original" | "mp3" | "m4a" | "flac" | "wav" | "opus";

export interface ExtensionPreferences {
  linkgetPath?: string;
  defaultArticleFolder?: string;
  defaultMediaFolder?: string;
  defaultBrowser: BrowserOption;
  autoDetectClipboard?: boolean;
}

export interface LinkgetErrorInfo {
  category?: string;
  label?: string;
  message: string;
  next_step?: string;
  phase?: string | null;
  retryable?: boolean;
}

export interface InspectMetadata {
  title?: string | null;
  author?: string | null;
  artist?: string | null;
  uploader?: string | null;
  site?: string | null;
  media_type?: string | null;
  duration?: number | null;
  resolution?: string | null;
  filesize?: number | null;
  size_bytes?: number | null;
  audio?: boolean | null;
  is_live?: boolean;
  is_article?: boolean;
  word_count?: number | null;
  image_count?: number | null;
  video_count?: number | null;
  [key: string]: unknown;
}

export interface InspectResponse {
  schema_version?: number;
  ok: boolean;
  command?: string;
  supported?: boolean;
  kind?: string;
  site?: string | null;
  engine?: string | null;
  domain?: string | null;
  canonical_url?: string | null;
  reference?: string | null;
  short_link_unresolved?: boolean;
  audio_only?: boolean;
  effective_audio_mode?: boolean;
  supports_read?: boolean;
  supports_article_download?: boolean;
  supports_auth?: boolean;
  session_state?: string | null;
  tools_required?: string[];
  tools_missing?: string[];
  destination_preview?: string | null;
  destination_error?: string | null;
  probe_status?: string | null;
  metadata?: InspectMetadata | null;
  login_required?: boolean;
  path?: string | null;
  error?: LinkgetErrorInfo | string | null;
}

export interface SavedFileMeta {
  path?: string;
  file?: string;
  bytes?: number;
  width?: number;
  height?: number;
  duration?: number | string | null;
}

export interface DownloadResponse {
  schema_version?: number;
  ok: boolean;
  command?: string;
  task_id?: string | null;
  exit_code?: number;
  item?: number;
  status?: string;
  site?: string | null;
  reference?: string | null;
  destination?: string | null;
  audio_mode?: boolean;
  saved_count?: number;
  skipped_count?: number;
  saved_files?: string[];
  files?: SavedFileMeta[];
  article_folder?: string | null;
  title?: string | null;
  author?: string | null;
  assets_downloaded?: number;
  assets_failed?: number;
  error?: LinkgetErrorInfo | string | null;
}

export interface ReadSubmitResponse {
  schema_version?: number;
  ok: boolean;
  command?: string;
  mode?: string;
  task_id?: string;
  kind?: string;
  status?: string;
  status_command?: string;
  wait_command?: string;
  show_command?: string;
  log_command?: string;
  outputs?: string[];
  content_path?: string | null;
  error?: LinkgetErrorInfo | string | null;
}

export interface RetryResponse {
  schema_version?: number;
  ok: boolean;
  command?: string;
  task_id?: string | null;
  exit_code?: number;
  items?: DownloadResponse[];
  error?: LinkgetErrorInfo | string | null;
}

export interface DownloadTaskItem {
  item: number;
  status: string;
  destination?: string | null;
  failure?: {
    category?: string | null;
    message?: string;
    next_step?: string;
    phase?: string | null;
  } | null;
  retained_paths?: string[];
  saved_files?: string[];
}

export interface DownloadTask {
  kind: "download";
  id: string;
  status: string;
  completed_items: number;
  total_items: number;
  destination: string;
  audio: boolean;
  audio_format?: string | null;
  items: DownloadTaskItem[];
}

export interface ReadTask {
  kind: "read";
  id: string;
  task_kind: string;
  status: string;
  phase: string;
  outputs: string[];
  source_folder?: string | null;
  failure?: string | null;
}

export type TaskEntry = DownloadTask | ReadTask;

export interface TasksResponse {
  schema_version?: number;
  ok: boolean;
  command?: string;
  tasks?: TaskEntry[];
  warnings?: string[];
  error?: LinkgetErrorInfo | string | null;
}

export interface DestinationSetting {
  mode: "photos" | "cwd" | "folder";
  path?: string;
}

export interface ConfigResponse {
  schema_version?: number;
  ok: boolean;
  command?: string;
  save_types?: SaveType[];
  destination?: DestinationSetting;
  audio_destination?: DestinationSetting | null;
  article_destination?: DestinationSetting | null;
  reader_browser?: string;
  page_controller?: {
    browser: string;
    source: string;
    supported: boolean;
    enabled: boolean;
  };
  login_browsers?: string[];
  saved_browser?: string | null;
  changed?: boolean;
  error?: LinkgetErrorInfo | string | null;
}

export interface ToolStatus {
  available: boolean;
  path?: string | null;
  version?: string | null;
}

export interface SessionStatus {
  domain: string;
  state: string;
  modified?: string;
  detail?: string;
}

export interface DoctorResponse {
  schema_version?: number;
  ok: boolean;
  command?: string;
  version?: string;
  python?: string;
  platform?: string;
  tools?: Record<string, ToolStatus>;
  missing_tools?: string[];
  page_controller?: {
    browser: string;
    source: string;
    supported: boolean;
    enabled: boolean;
  };
  sessions?: Record<string, SessionStatus>;
  error?: LinkgetErrorInfo | string | null;
}

export interface CleanEntry {
  task_id: string;
  item: number;
  path: string;
  bytes?: number;
  file_count?: number;
  reason?: string;
  message?: string;
}

export interface CleanResponse {
  schema_version?: number;
  ok: boolean;
  command?: string;
  mode: "dry_run" | "apply" | string;
  older_than: string;
  older_than_seconds?: number;
  eligible_count: number;
  reclaimable_bytes: number;
  deleted_count: number;
  freed_bytes: number;
  kept_count: number;
  eligible?: CleanEntry[];
  deleted?: CleanEntry[];
  kept?: CleanEntry[];
  errors?: unknown[];
  error?: LinkgetErrorInfo | string | null;
}

export interface UpgradeCheckResponse {
  ok?: boolean;
  current?: string;
  latest?: string;
  update_available?: boolean;
  install_method?: string;
  release_url?: string;
  upgrade_command?: string;
  error?: LinkgetErrorInfo | string | null;
}

export interface ArticleArchiveInfo {
  folderPath: string;
  htmlPath: string | null;
  mdPath: string | null;
  jsonPath: string | null;
  txtPath: string | null;
  sourcePath: string | null;
  coverPath: string | null;
  assetPaths: string[];
}

export interface ArticleLocalMeta {
  exists: boolean;
  title: string | null;
  author: string | null;
  authorHandle: string | null;
  site: string | null;
  publishedAt: string | null;
  excerpt: string | null;
  coverPath: string | null;
  htmlPath: string | null;
  mdPath: string | null;
  jsonPath: string | null;
  folderPath: string;
  assetCount: number;
  assetsDownloaded: number | null;
  assetsFailed: number | null;
  totalBytes: number;
  reference: string | null;
  canonicalUrl: string | null;
}

// Linkget Showcase Interactivity: 2-state theme toggle, EN/zh-Hans switcher,
// constraint-safe command/route builder, accessible tabs & toolbar,
// clipboard copy with aria-live feedback, and progressive scroll reveal fallback.

const PRESETS = {
  xarticle: {
    url: 'https://x.com/USER/article/ID',
    site: 'x',
    defaultDest: 'photos',
    defaultMode: 'auto',
    defaultKind: 'article',
    allowedDests: ['photos', 'folder'],
    allowedModes: ['auto', 'article'],
    engines: {
      photos: 'x-article-photos (offline Chromium)',
      folder: 'x-article-archive',
    },
    notes: {
      en: {
        photos: 'Renders the X Article locally inside a network-disabled Chromium window and imports a high-resolution PNG into Apple Photos.',
        folder: 'Exports the X Article into an offline folder with article.md, standalone article.html, content.json, and downloaded local media.',
      },
      'zh-Hans': {
        photos: '在禁网 Headless Chromium 中本地渲染 X Article 长文，并将高清长图 PNG 导入 Apple 照片。',
        folder: '将 X Article 导出为完整离线文件夹，包含 article.md、独立 article.html、content.json 与本地图片附件。',
      },
    },
  },
  markdown: {
    url: 'https://mp.weixin.qq.com/s/ARTICLE_ID',
    site: 'wechat / web',
    defaultDest: 'folder',
    defaultMode: 'auto',
    defaultKind: 'article',
    allowedDests: ['folder'],
    allowedModes: ['auto', 'article'],
    engines: {
      folder: 'native-article',
    },
    notes: {
      en: {
        folder: 'Writes Obsidian-ready article.md, standalone article.html, content.txt, content.json, and local assets/ in a titled subfolder.',
      },
      'zh-Hans': {
        folder: '自动生成 Obsidian 可直接读取的 article.md、独立 article.html、content.txt、content.json 及 assets/ 附件目录。',
      },
    },
  },
  live: {
    url: 'https://v.douyin.com/EXAMPLE/',
    site: 'douyin',
    defaultDest: 'photos',
    defaultMode: 'auto',
    defaultKind: 'photo',
    allowedDests: ['photos', 'folder'],
    allowedModes: ['auto', 'photo', 'video', 'audio'],
    engines: {
      photos: 'native-douyin + LivePhoto',
      folder: 'native-douyin',
    },
    notes: {
      en: {
        photos: 'Downloads still frames + paired motion tracks, verifies complete motion metadata, packages Live Photos, and checks Photos library bytes.',
        folder: 'Saves still frames and videos into your local folder (add --live-photos to package Live Photos/ pairs on disk).',
        photo: 'Saves still images only via --as photo (videos and Live Photo pairing are excluded; use Auto for Live Photos).',
        video: 'Routes explicitly through single-video resolution (--as video) without packaging Live Photos.',
        audio: 'Extracts audio into a local folder via ffmpeg (--audio cannot target Apple Photos).',
      },
      'zh-Hans': {
        photos: '下载静图与配对动态视频、校验实况元数据完整性后封装为实况照片，并在字节级核对写入 Apple 照片后清理临时目录。',
        folder: '将静图与视频保存至本地目录（若需同时生成本地实况配对目录可附加 --live-photos）。',
        photo: '通过 --as photo 仅保存静态图片（不含视频与实况照片封装；如需实况照片请使用自动识别）。',
        video: '通过 --as video 显式按视频方式下载清理后的播放源，不做实况封装。',
        audio: '通过 ffmpeg 提取本地音频文件（音频文件不支持导入 Apple 照片）。',
      },
    },
  },
  video: {
    url: 'https://www.bilibili.com/video/BV1bK411W797/',
    site: 'bilibili / youtube',
    defaultDest: 'folder',
    defaultMode: 'video',
    defaultKind: 'video',
    allowedDests: ['photos', 'folder'],
    allowedModes: ['auto', 'video', 'audio'],
    engines: {
      photos: 'yt-dlp -> Photos import',
      folder: 'yt-dlp + ffprobe',
    },
    notes: {
      en: {
        photos: 'Downloads with yt-dlp, validates video streams with ffprobe, imports to Apple Photos, and retains staging only on verification failure.',
        folder: 'Saves the highest compatible video stream into ~/Downloads (or custom --folder) with completeness checks.',
        video: 'Explicit --as video skips ambiguity and routes straight through single-video extractors.',
        audio: 'Extracts tagged audio into ~/Music via --audio (YouTube Music URLs switch to audio mode automatically; --photos is disabled for audio).',
      },
      'zh-Hans': {
        photos: '调用 yt-dlp 下载并经 ffprobe 校验码流完整性，确认写入 Apple 照片后自动清理临时文件。',
        folder: '将兼容格式的高清视频保存至 ~/Downloads（或自定义 --folder），保留完整容器与音画流。',
        video: '加上 --as video 直接走视频下载链路，避免交互选择。',
        audio: '加上 --audio 提取音轨到 ~/Music（music.youtube.com 链接会自动识别为纯音频；音频不能导出到照片图库）。',
      },
    },
  },
  batch: {
    url: "'https://x.com/USER/status/1' 'https://www.youtube.com/watch?v=ID'",
    site: 'multi-site batch',
    defaultDest: 'folder',
    defaultMode: 'auto',
    defaultKind: 'auto',
    isBatch: true,
    allowedDests: ['photos', 'folder'],
    allowedModes: ['auto', 'video', 'audio'],
    engines: {
      photos: 'batch worker pool (max 3)',
      folder: 'batch worker pool (max 3)',
    },
    notes: {
      en: {
        photos: 'Runs up to 3 workers (1 active per site with a 1s spacing), checkpoints per-item state, and preserves --photos on linkget retry.',
        folder: 'Concurrently saves multiple URLs to your target folder; unfinished items resume via linkget retry keeping original flags.',
        video: 'Applies --as video across all batch entries when overridden explicitly.',
        audio: 'Applies --audio across all batch links and writes audio files into your local folder (--photos is disabled for audio).',
      },
      'zh-Hans': {
        photos: '批量任务默认并发 3 个 worker（同站点同一时间仅 1 个并间隔 1 秒），断连项可用 linkget retry 原参数续传进 Apple 照片。',
        folder: '并发下载多个链接至目标目录；单条失败不影响已成功条目，随后直接执行 linkget retry 续传。',
        video: '为整批链接统一指定 --as video 按视频规则处理。',
        audio: '为整批链接开启 --audio 统一导出本地音频（音频不支持 --photos）。',
      },
    },
  },
};

const I18N = {
  en: {
    'meta.title': 'Linkget — Save Social Media, Live Photos & Long Reads Locally on macOS',
    'nav.skip': 'Skip to main content',
    'nav.router': 'Router',
    'nav.demos': 'Demos',
    'nav.surfaces': 'Surfaces',
    'nav.sites': 'Sites',
    'nav.install': 'Install',
    'nav.studio': 'Studio',
    'controls.langLabel': '中文',
    'controls.langAria': 'Switch interface language to Chinese',
    'controls.toLight': 'Switch to light theme',
    'controls.toDark': 'Switch to dark theme',
    'copy.btn': 'Copy',
    'copy.done': 'Copied!',
    'copy.fail': 'Clipboard permission unavailable',
    'hero.eyebrow': 'LOCAL-FIRST macOS ARCHIVER · MIT',
    'hero.title': 'Save posts, Live Photos, and long reads straight to Photos or Markdown.',
    'hero.sub': 'Linkget resolves social links and articles on your own Mac, checks media completeness with ffprobe, and keeps staged copies until Apple Photos imports are verified byte-for-byte.',
    'hero.ctaPrimary': 'Try Command Router',
    'hero.ctaRepo': 'GitHub Repo',
    'hero.ctaRelease': 'v0.4.0 Release',
    'hl.h1Title': 'Apple Photos + Live Photos',
    'hl.h1Sub': 'Byte-verified imports & paired motion',
    'hl.h2Title': 'X & Web Articles',
    'hl.h2Sub': 'Offline Markdown folder or long PNG',
    'hl.h3Title': 'CLI · Raycast · MCP',
    'hl.h3Sub': 'One toolset across terminal, launcher, and agents',
    'hl.h4Title': 'Zero Telemetry',
    'hl.h4Sub': 'Local browser sessions & disk receipts only',
    'flow.k1': '01 · Input & Mode Resolution',
    'flow.v1': 'Paste a single URL, share text, or a multi-link batch. Override detection with --as auto, photo, video, article, or --audio.',
    'flow.k2': '02 · Local Engines & Checks',
    'flow.v2': 'Routes across gallery-dl, yt-dlp, native site resolvers, or offline headless Chromium, reusing local browser logins only when allowed.',
    'flow.k3': '03 · Verified Destination',
    'flow.v3': 'Commits into macOS Apple Photos after byte-for-byte library checks, or writes Obsidian-ready Markdown, HTML, JSON, and local assets.',
    'router.heading': 'Interactive Command & Route Builder',
    'router.sub': 'Pick a real workflow and switch destination or save type to see the exact CLI invocation, inspect summary, and output layout.',
    'router.pXArticle': 'X Article → Photos',
    'router.pMarkdown': 'Article → Obsidian',
    'router.pLive': 'Douyin / XHS → Live Photos',
    'router.pVideo': 'Bilibili / YouTube',
    'router.pBatch': 'Batch + Retry',
    'router.destLabel': 'Destination',
    'router.destPhotos': 'Apple Photos',
    'router.destFolder': 'Local Folder',
    'router.modeLabel': 'Save As',
    'router.modeAuto': 'Auto',
    'router.modePhoto': 'Photo',
    'router.modeVideo': 'Video',
    'router.modeArticle': 'Article',
    'router.modeAudio': 'Audio',
    'router.cmdTitle': 'Generated Shell Command',
    'router.inspectTitle': 'Inspect Route & Expected Output',
    'router.previewTag': 'Simulated Preview',
    'router.kSite': 'Site',
    'router.kEngine': 'Engine',
    'router.kDest': 'Destination',
    'router.kSafety': 'Safety Gate',
    'demos.heading': 'Live Terminal Recordings',
    'demos.sub': 'Recorded from real runs saving a five-second Vimeo clip and archiving a web page offline.',
    'demos.vTitle': 'Save a video clip to a folder',
    'demos.vDesc': "Downloads Blender's Big Buck Bunny test clip via yt-dlp, verifies streams with ffprobe, and saves a standalone MP4.",
    'demos.aTitle': 'Archive a web article offline',
    'demos.aDesc': 'Extracts article content, downloads embedded media into local assets/, and writes article.md alongside standalone article.html.',
    'surfaces.heading': 'Three Ways to Run Linkget',
    'surfaces.sub': 'Use the same local engine from your shell, through the bundled two-command Raycast extension, or inside AI coding agents over stdio MCP.',
    'surfaces.tCli': 'CLI (linkget)',
    'surfaces.tRay': 'Raycast (lg / lgs)',
    'surfaces.tMcp': 'MCP Server (linkget mcp)',
    'surf.tagDownload': 'DOWNLOAD & OVERRIDE',
    'surf.tagInspect': 'PRE-FLIGHT CHECK',
    'surf.tagBatch': 'BATCH & RECOVERY',
    'surf.tagRead': 'READ & CLEANUP',
    'surf.tagLg': 'COMMAND · lg',
    'surf.tagLgs': 'COMMAND · lgs',
    'surf.tagVault': 'OBSIDIAN & PHOTOS',
    'surf.tagDiag': 'DIAGNOSTICS',
    'surf.tagMcp1': 'INSPECT & HEALTH',
    'surf.tagMcp2': 'MEDIA & BATCH',
    'surf.tagMcp3': 'CONTENT READING',
    'surf.tagMcp4': 'TASKS & CLEANUP',
    'surf.cli1Title': 'Single Post, Audio & Articles',
    'surf.cli1Desc': 'Detects photos, videos, audio tracks, and articles automatically, with explicit --as photo|video|article and --photos or --folder overrides.',
    'surf.cli2Title': 'Side-Effect-Free Inspect',
    'surf.cli2Desc': 'Checks canonical URL routing, supported article/photo flags, tool availability, and saved session status without touching disk receipts.',
    'surf.cli3Title': 'Batch Queue, Retry & Recover',
    'surf.cli3Desc': 'Downloads multiple links concurrently via batch (--jobs N), resumes unfinished items with retry while preserving flags, and salvages verified staged files via recover.',
    'surf.cli4Title': 'Transcripts, Keyframes & Stage Clean',
    'surf.cli4Desc': 'Extracts subtitles, Whisper transcripts, OCR keyframes, or offline articles via read, and safely prunes aged temp directories with clean.',
    'surf.ray1Title': 'Save Link',
    'surf.ray1Desc': 'Auto-reads clipboard links or accepts multi-line batch pastes and share text. Switch between Auto, Photos, Video, Article, Audio, or Transcript before saving, or launch in background (⌘B).',
    'surf.ray2Title': 'Status Board',
    'surf.ray2Desc': 'Browse recent downloads and archived reads with cover previews, toggle the detail pane (⌘⇧D), open the inline Markdown reader (⌘⇧R, with ⌘C Copy Markdown), open article.html, or retry failed jobs.',
    'surf.ray3Title': 'Vault Folder & X Article PNG',
    'surf.ray3Desc': 'Point Default Article Archive Folder at your Obsidian Vault Clippings folder, or render X Articles as high-resolution PNG images into Apple Photos.',
    'surf.ray4Title': 'Engines, Sessions & Maintenance',
    'surf.ray4Desc': 'Inspect gallery-dl, yt-dlp, ffmpeg, and site session status inside Status Board, trigger update checks, and confirm linkget clean runs.',
    'surf.mcp1Desc': 'Read-only pre-flight routing, remote metadata probes (no_network option), browser controller selection (neo or chrome), and folder write checks.',
    'surf.mcp2Desc': 'Saves single posts, 1 to 50 batch URLs, audio extractions (audio_format), and offline articles into explicit target folders with duration and size caps.',
    'surf.mcp3Desc': 'Extracts subtitles, speech transcripts, video keyframes (max_frames), macOS Vision OCR, and X/web/WeChat/Toutiao articles synchronously or in background.',
    'surf.mcp4Desc': 'Polls download or read tasks (with optional content.json payload), retries unfinished items without losing parameters, and previews temporary cleanup.',
    'guards.heading': 'Four Local-First Guarantees',
    'guards.sub': 'Built so temporary downloads never vanish mid-failure and browser sessions stay on your disk.',
    'guards.g1Tag': 'APPLE PHOTOS',
    'guards.g1Title': 'Byte-Verified Stage Cleanup',
    'guards.g1Desc': 'Media is staged under ~/Library/Application Support/linkget/ and verified inside the managed Apple Photos library byte-for-byte before staging files are deleted.',
    'guards.g2Tag': 'X ARTICLES',
    'guards.g2Title': 'Offline Headless Chromium PNGs',
    'guards.g2Desc': 'Pass --photos or set linkget config --x-article-photos to archive an X Article or promoted long post as a high-resolution PNG into Photos using a network-disabled browser pass.',
    'guards.g3Tag': 'RECOVERY',
    'guards.g3Title': 'Checkpointed Retry & Dry-Run Clean',
    'guards.g3Desc': 'Unfinished downloads reuse verified staged media under linkget retry while keeping original --photos or --folder targets intact, and clean defaults to preview unless --apply is set.',
    'guards.g4Tag': 'PRIVACY',
    'guards.g4Title': 'Local Browser Reuse & Zero Telemetry',
    'guards.g4Desc': 'Linkget has no hosted backend or analytics. Saved session cookies and receipts remain readable only by your user account, or pass --browser none to disable browser reads completely.',
    'sites.heading': 'Supported Sites',
    'sites.sub': 'Single-post resolvers, article archivers, and audio extractors verified in Linkget v0.4.0. Filter by what you want to save.',
    'sites.tableAria': 'Supported sites table',
    'sites.fAll': 'All Sites',
    'sites.fPhotos': 'Photos',
    'sites.fVideos': 'Videos',
    'sites.fArticles': 'Articles',
    'sites.fAudio': 'Audio',
    'sites.caption': 'Single-post, article, and music routes in Linkget v0.4.0',
    'sites.colSite': 'Site',
    'sites.colOutputs': 'Supported Media',
    'sites.colStatus': 'Tier',
    'sites.colNotes': 'Notes',
    'sites.sStable': 'Stable',
    'sites.sTrial': 'Trial',
    'sites.sDev': 'Development',
    'sites.sWebName': 'WeChat / Toutiao / Zhihu / Web',
    'sites.oIg': 'Photos, videos, Reels',
    'sites.nIg': 'Completeness check counts source photos and videos across carousels and Reels.',
    'sites.oX': 'Photos, videos, X Articles',
    'sites.nX': 'Detects Articles on /status/ID & /article/ID; saves Markdown/HTML folder or renders PNG via --photos.',
    'sites.oWeb': 'Offline articles',
    'sites.nWeb': 'Writes article.md, standalone article.html, content.json, and assets/; pass --no-assets to skip media.',
    'sites.oBili': 'Video (BV/av), Opus, Audio (au)',
    'sites.nBili': 'Optional browser login unlocks higher streams; handles one part or audio track per URL.',
    'sites.oTt': 'Photo posts, videos',
    'sites.nTt': 'Supports short share links.',
    'sites.oDy': 'Photo posts, Live Photos, videos',
    'sites.nDy': 'Ranks clean playback streams, pairs still + motion into Live Photos, supports BrowserOS neo / Chrome fallback.',
    'sites.oXhs': 'Image sets, videos',
    'sites.nXhs': 'Keep full URL including xsec_token query parameters; checkpoints multi-image posts on retry.',
    'sites.oWb': 'Photos, videos',
    'sites.nWb': 'Heuristic selection prefers watermark-free JPEG originals when available.',
    'sites.oYt': 'Single videos, Shorts, Music tracks',
    'sites.nYt': 'Playlist-only URLs and live streams excluded; music.youtube.com defaults to audio extraction.',
    'sites.oVimeo': 'Public embeddable videos',
    'sites.nVimeo': 'Accepts vimeo.com/ID and player.vimeo.com/video/ID.',
    'sites.oDm': 'Single videos',
    'sites.nDm': 'Accepts dailymotion.com/video/ID and dai.ly/ID.',
    'sites.oSc': 'Single tracks',
    'sites.nSc': 'Public tracks and on.soundcloud.com shares; sets excluded.',
    'sites.oBc': 'Single tracks',
    'sites.nBc': 'Public /track/ links with metadata tags; full albums excluded.',
    'sites.oRd': 'Photos, galleries, videos',
    'sites.nRd': 'Handled through gallery-dl.',
    'sites.oKs': '/short-video/ videos',
    'sites.nKs': 'Prefers HEVC streams at equal resolution.',
    'sites.oLf': 'Photos, videos',
    'sites.nLf': 'Handled through gallery-dl.',
    'sites.oPx': 'Static artworks',
    'sites.nPx': 'Static artworks; Ugoira and collections excluded.',
    'sites.oPin': 'Pins, boards',
    'sites.nPin': 'Handled through gallery-dl.',
    'sites.footnote': 'Video mode can also attempt additional sites supported by your installed yt-dlp. See the repository README for the full compatibility reference.',
    'install.heading': 'Install & Configure',
    'install.sub': 'One Homebrew command installs Python, gallery-dl, yt-dlp, ffmpeg, and deno. Set your default Photos and Obsidian paths once, then connect Raycast or MCP if needed.',
    'install.tBrew': 'Homebrew Install',
    'install.tDefaults': 'Photos & Obsidian',
    'install.tMcp': 'MCP Client Config',
    'install.tRay': 'Raycast Local Build',
    'footer.brand': 'Linkget v0.4.0 · MIT License',
    'footer.note': ' — Runs locally on macOS. Zero cloud services or telemetry.',
    'footer.home': 'popcornnn.xyz',
    'footer.repo': 'GitHub',
    'footer.release': 'Releases',
    'footer.raycast': 'Raycast',
    'footer.issues': 'Issues',
  },
  'zh-Hans': {
    'meta.title': 'Linkget — 把社交媒体图片、视频、实况照片与长文保存到本地或 Apple 照片',
    'nav.skip': '跳到正文',
    'nav.router': '命令路由',
    'nav.demos': '实际录屏',
    'nav.surfaces': '使用入口',
    'nav.sites': '支持站点',
    'nav.install': '安装配置',
    'nav.studio': '主页',
    'controls.langLabel': 'EN',
    'controls.langAria': '切换界面语言为英文',
    'controls.toLight': '切换到浅色模式',
    'controls.toDark': '切换到深色模式',
    'copy.btn': '复制',
    'copy.done': '已复制',
    'copy.fail': '剪贴板权限不可用',
    'hero.eyebrow': '本地优先 macOS 归档工具 · MIT',
    'hero.title': '把帖子图片、视频、实况照片与长文，直接存进 Apple 照片或本地 Markdown。',
    'hero.sub': 'Linkget 在你的 Mac 上本地解析社媒与网页链接，调用 ffprobe 做流完整性检查，并只在 Apple 照片完成字节级核对后才清理临时文件。',
    'hero.ctaPrimary': '试用命令路由器',
    'hero.ctaRepo': 'GitHub 仓库',
    'hero.ctaRelease': 'v0.4.0 发布页',
    'hl.h1Title': 'Apple 照片 + 实况图',
    'hl.h1Sub': '字节校验入库与静动图配对',
    'hl.h2Title': 'X Article 与网页长文',
    'hl.h2Sub': '离线 Markdown 目录或渲染长图 PNG',
    'hl.h3Title': 'CLI · Raycast · MCP',
    'hl.h3Sub': '终端、启动器与 AI Agent 共用一套引擎',
    'hl.h4Title': '无服务端遥测',
    'hl.h4Sub': '会话与任务记录保存在仅限当前用户可读的本地目录',
    'flow.k1': '01 · 链接输入与保存类型识别',
    'flow.v1': '支持单个链接、App 分享文本或批量粘贴。可用 --as auto、photo、video、article 或 --audio 明确覆盖默认识别结果。',
    'flow.k2': '02 · 本地引擎路由与校验',
    'flow.v2': '自动调度 gallery-dl、yt-dlp、原生站点适配或禁网 Headless Chromium，仅在需要且允许时复用本机浏览器登录态。',
    'flow.k3': '03 · 安全落盘与图库入库',
    'flow.v3': '导入 macOS 照片并通过托管副本字节校验，或导出包含 article.md、article.html、content.json 与本地资源的归档目录。',
    'router.heading': '交互式命令与路由生成器',
    'router.sub': '选择常见场景并切换目标位置或保存类型，实时查看完整的 CLI 命令、路由摘要与预期文件布局。',
    'router.pXArticle': 'X 长文 → 照片长图',
    'router.pMarkdown': '长文 → Obsidian 库',
    'router.pLive': '抖音 / 小红书实况',
    'router.pVideo': 'B 站 / YouTube',
    'router.pBatch': '批量与断点续传',
    'router.destLabel': '保存目标',
    'router.destPhotos': 'Apple 照片',
    'router.destFolder': '本地文件夹',
    'router.modeLabel': '保存类型',
    'router.modeAuto': '自动识别',
    'router.modePhoto': '图片',
    'router.modeVideo': '视频',
    'router.modeArticle': '文章',
    'router.modeAudio': '音频',
    'router.cmdTitle': '生成的 Shell 命令',
    'router.inspectTitle': '路由摘要与预期输出',
    'router.previewTag': '效果预览',
    'router.kSite': '站点',
    'router.kEngine': '路由引擎',
    'router.kDest': '目标位置',
    'router.kSafety': '安全校验',
    'demos.heading': '真实终端录屏',
    'demos.sub': '以下两条录屏直接来自实际终端运行：分别保存五秒 Vimeo 片段与离线归档网页。',
    'demos.vTitle': '将视频片段保存到本地文件夹',
    'demos.vDesc': '通过 yt-dlp 下载 Blender 《Big Buck Bunny》五秒测试片段，经 ffprobe 检查音画流后写入 MP4。',
    'demos.aTitle': '将网页文章离线归档',
    'demos.aDesc': '抽取正文结构，将插图下载至本地 assets/，并生成 article.md 与独立 article.html。',
    'surfaces.heading': '三种日常使用方式',
    'surfaces.sub': '无论是终端命令行、内置的双命令 Raycast 扩展，还是通过 stdio MCP 接入 AI Agent，底层都是同一套本地逻辑。',
    'surfaces.tCli': 'CLI 命令行 (linkget)',
    'surfaces.tRay': 'Raycast 扩展 (lg / lgs)',
    'surfaces.tMcp': 'MCP 服务端 (linkget mcp)',
    'surf.tagDownload': '下载与参数覆盖',
    'surf.tagInspect': '无副作用预检',
    'surf.tagBatch': '批量与恢复',
    'surf.tagRead': '转读与清理',
    'surf.tagLg': '命令 · lg',
    'surf.tagLgs': '命令 · lgs',
    'surf.tagVault': 'Obsidian 与照片',
    'surf.tagDiag': '运行诊断',
    'surf.tagMcp1': '预检与健康检查',
    'surf.tagMcp2': '媒体与批量下载',
    'surf.tagMcp3': '内容与全文提取',
    'surf.tagMcp4': '任务查询与清理',
    'surf.cli1Title': '单链接下载与参数覆盖',
    'surf.cli1Desc': '自动分辨图片、视频、音乐和长文，可用 --as photo|video|article 以及 --photos / --folder 灵活切换输出方式。',
    'surf.cli2Title': '无副作用预检 (inspect)',
    'surf.cli2Desc': '不触发下载也不生成任务目录，预先返回规范化 URL、站点引擎、文章支持情况与本地登录态。',
    'surf.cli3Title': '并发批量、续传与恢复',
    'surf.cli3Desc': '用 batch 并发处理多条链接，通过 retry 保留 --photos 等原参数续传，或用 recover 恢复已验证暂存媒体。',
    'surf.cli4Title': '音视频转读与安全清理',
    'surf.cli4Desc': '使用 read 提取字幕、Whisper 转写、OCR 关键帧或文章全文；用 clean 预览并清理超期已完成的暂存目录。',
    'surf.ray1Title': 'Save Link (lg)',
    'surf.ray1Desc': '打开即读取剪贴板链接，支持多行批量粘贴或分享文本；保存前可手动改为图片、视频、文章、音频或转写，⌘B 放入后台执行。',
    'surf.ray2Title': 'Status Board (lgs)',
    'surf.ray2Desc': '浏览最近下载与归档长文，支持封面预览、切换详情面板（⌘⇧D）、打开扩展内 Markdown 阅读器（⌘⇧R，配合 ⌘C 复制正文）、打开 article.html 与失败重试。',
    'surf.ray3Title': '直连笔记库与 X 长文转长图',
    'surf.ray3Desc': '在偏好设置中把默认文章目录指向 Obsidian Clippings，或把 X Article 渲染为高清长图导入 Apple 照片。',
    'surf.ray4Title': '引擎、登录态与空间维护',
    'surf.ray4Desc': '在 Status Board 内查看 gallery-dl、yt-dlp、ffmpeg 与站点会话状态，检查更新并交互确认清理临时暂存目录。',
    'surf.mcp1Desc': '只读预检路由与本地工具检查，支持 no_network 参数跳过远端探测、指定浏览器控制器（neo/chrome）并检测目录可写性。',
    'surf.mcp2Desc': '向指定目录下载单个帖子、1–50 条批量链接、音频抽取（audio_format）或离线文章，支持片段裁剪、时长上限与体积上限保护。',
    'surf.mcp3Desc': '同步或后台提取音视频字幕、语音转写、关键帧（max_frames）、macOS Vision OCR 以及 X/网页/公众号/头条文章正文。',
    'surf.mcp4Desc': '查询下载或阅读任务进度（可选附带 content.json 正文），原参数续传未完成条目，并先以只读预览模式检查暂存清理。',
    'guards.heading': '四项本地安全保证',
    'guards.sub': '避免半途失败丢失已下载媒体，也让登录凭据与任务数据始终留在你的设备内。',
    'guards.g1Tag': 'APPLE 照片',
    'guards.g1Title': '字节级核对后清理暂存',
    'guards.g1Desc': '媒体先保存在 ~/Library/Application Support/linkget/ 暂存区，确认与 Apple 照片托管库内文件字节一致后，才会删除本地临时副本。',
    'guards.g2Tag': 'X ARTICLE 长文',
    'guards.g2Title': '禁网无头 Chromium 渲染长图',
    'guards.g2Desc': '传入 --photos 或开启 linkget config --x-article-photos，会在禁用网络的 Chromium 页面中本地排版渲染，再将高清 PNG 存进系统相册。',
    'guards.g3Tag': '断点续传',
    'guards.g3Title': '检查点复用与预览清理',
    'guards.g3Desc': '失败或中断的下载可通过 linkget retry 复用已校验暂存文件并保留原 --photos / --folder 参数；clean 默认只做只读预览，显式传入 --apply 才删除。',
    'guards.g4Tag': '隐私边界',
    'guards.g4Title': '复用本机浏览器会话 · 零遥测',
    'guards.g4Desc': '没有任何云端后端或埋点上报；保存的 Cookie 与回执保存在仅限当前用户可读的本地目录，也可随时用 --browser none 完全关闭浏览器读取。',
    'sites.heading': '支持站点范围',
    'sites.sub': 'Linkget v0.4.0 已验证的单条帖子、离线长文与音频解析路线，可按保存类型筛选查看。',
    'sites.tableAria': '支持站点明细表',
    'sites.fAll': '全部站点',
    'sites.fPhotos': '图片',
    'sites.fVideos': '视频',
    'sites.fArticles': '长文归档',
    'sites.fAudio': '音频 / 音乐',
    'sites.caption': 'Linkget v0.4.0 支持的帖子、文章与单曲路由明细',
    'sites.colSite': '站点 / 平台',
    'sites.colOutputs': '支持内容',
    'sites.colStatus': '稳定度',
    'sites.colNotes': '说明',
    'sites.sStable': '稳定',
    'sites.sTrial': '试验支持',
    'sites.sDev': '开发中',
    'sites.sWebName': '微信公众号 / 头条 / 知乎 / 网页',
    'sites.oIg': '图片、视频、Reels',
    'sites.nIg': '图集与 Reels 会在下载前后核对源条目数量，防止漏图。',
    'sites.oX': '图片、视频、X Articles 长文',
    'sites.nX': '自动识别 /status/ID 与 /article/ID 长文：默认导出 Markdown/HTML 目录，加 --photos 渲染长图进 Apple 照片。',
    'sites.oWeb': '离线文章存档',
    'sites.nWeb': '生成 article.md、自包含 article.html、content.json 与本地 assets/；加 --no-assets 可跳过媒体下载。',
    'sites.oBili': '视频 (BV/av)、专栏 Opus、音频 (au)',
    'sites.nBili': '携带本地浏览器登录态可获取更高清晰度；每个 URL 处理对应单 P 或单曲。',
    'sites.oTt': '图文帖、短视频',
    'sites.nTt': '直接支持客户端复制出的短分享链接。',
    'sites.oDy': '图集、实况照片、视频',
    'sites.nDy': '自动筛选无水印干净播放流，静动图严格配对后生成实况照片，支持 BrowserOS neo / Chrome 回退解析。',
    'sites.oXhs': '图文、视频',
    'sites.nXhs': '请保留包含 xsec_token 的完整链接；多图帖按资源设检查点以支持续传。',
    'sites.oWb': '图片、视频',
    'sites.nWb': '按启发式规则优先挑选无水印 JPEG 原图。',
    'sites.oYt': '单个视频、Shorts、YouTube Music 单曲',
    'sites.nYt': '排除纯播放列表和直播流；music.youtube.com 链接会自动以纯音频模式提取。',
    'sites.oVimeo': '公开可嵌入视频',
    'sites.nVimeo': '支持 vimeo.com/ID 与 player.vimeo.com/video/ID。',
    'sites.oDm': '单个视频',
    'sites.nDm': '支持 dailymotion.com/video/ID 与 dai.ly/ID。',
    'sites.oSc': '单曲音频',
    'sites.nSc': '支持公开曲目与 on.soundcloud.com 分享链接（不含歌单合集）。',
    'sites.oBc': '单曲音频',
    'sites.nBc': '支持带元数据的 /track/ 单曲链接（不含整张专辑页面）。',
    'sites.oRd': '图片、图集、视频',
    'sites.nRd': '通过 gallery-dl 解析下载。',
    'sites.oKs': '/short-video/ 短视频',
    'sites.nKs': '同分辨率下优先保留 HEVC 编码视频。',
    'sites.oLf': '图文、视频',
    'sites.nLf': '通过 gallery-dl 解析下载。',
    'sites.oPx': '静态插画',
    'sites.nPx': '支持静态作品（不含动图 Ugoira 与合集）。',
    'sites.oPin': 'Pin 图钉、画板媒体',
    'sites.nPin': '通过 gallery-dl 解析下载。',
    'sites.footnote': '视频模式也可尝试本机 yt-dlp 支持的其他网站，实际可用性取决于站点的 MP4 流与登录要求。完整列表请参阅仓库 README。',
    'install.heading': '安装与常用配置',
    'install.sub': '一条 Homebrew 命令即可装好 Python 环境与 gallery-dl、yt-dlp、ffmpeg、deno；设置好默认图库或笔记目录后即可调用 CLI、Raycast 或 MCP。',
    'install.tBrew': 'Homebrew 安装',
    'install.tDefaults': '图库与 Obsidian 默认值',
    'install.tMcp': 'MCP 客户端配置',
    'install.tRay': 'Raycast 扩展本地构建',
    'footer.brand': 'Linkget v0.4.0 · MIT 开源协议',
    'footer.note': ' — 完全在 macOS 本地运行，没有任何云端服务或数据上报。',
    'footer.home': 'popcornnn.xyz',
    'footer.repo': 'GitHub',
    'footer.release': '版本发布',
    'footer.raycast': 'Raycast',
    'footer.issues': '反馈 Issue',
  },
};

let activeLang = 'en';
const routerState = {
  preset: 'xarticle',
  dest: 'photos',
  mode: 'auto',
};

function allowedDestsForState(presetKey, mode) {
  const preset = PRESETS[presetKey] || PRESETS.xarticle;
  if (mode === 'audio') {
    return preset.allowedDests.filter((d) => d !== 'photos');
  }
  return preset.allowedDests.slice();
}

function allowedModesForState(presetKey) {
  const preset = PRESETS[presetKey] || PRESETS.xarticle;
  return preset.allowedModes.slice();
}

function announce(msg) {
  const region = document.getElementById('live-status');
  if (!region) return;
  region.textContent = '';
  window.setTimeout(() => {
    region.textContent = msg;
  }, 20);
}

function resolveRouterNote(presetKey, dict, dest, mode) {
  if (mode === 'photo' || mode === 'video' || mode === 'audio') {
    return dict[mode] || dict[dest] || dict.folder;
  }
  if (presetKey === 'xarticle' || presetKey === 'live' || presetKey === 'video' || presetKey === 'batch') {
    return dest === 'photos' ? dict.photos : dict.folder;
  }
  return dict.folder;
}

function buildCommandAndPreview() {
  const preset = PRESETS[routerState.preset] || PRESETS.xarticle;
  const lang = activeLang === 'zh-Hans' ? 'zh-Hans' : 'en';
  const isZh = lang === 'zh-Hans';

  // Enforce valid constraints defensively
  const validModes = allowedModesForState(routerState.preset);
  if (!validModes.includes(routerState.mode)) {
    routerState.mode = preset.defaultMode;
  }
  const validDests = allowedDestsForState(routerState.preset, routerState.mode);
  if (!validDests.includes(routerState.dest)) {
    routerState.dest = validDests[0] || 'folder';
  }

  const mode = routerState.mode;
  const dest = routerState.dest;
  const effectiveMode = mode === 'auto' ? (preset.defaultKind || 'auto') : mode;
  const folderPath = effectiveMode === 'article' ? '~/Vault/Clippings' : effectiveMode === 'audio' ? '~/Music' : '~/Downloads';

  let cmd = '';
  if (preset.isBatch) {
    const parts = ['linkget batch', preset.url];
    if (mode === 'audio') parts.push('--audio');
    else if (mode !== 'auto') parts.push(`--as ${mode}`);
    parts.push(dest === 'photos' ? '--photos' : `--folder ${folderPath}`);
    cmd = parts.join(' ') + '\n# If any link times out, resume with preserved flags:\nlinkget retry';
  } else if (routerState.preset === 'live' && dest === 'photos' && mode === 'auto') {
    cmd = `linkget '${preset.url}' --photos --live-photos`;
  } else {
    const parts = ['linkget', `'${preset.url}'`];
    if (mode === 'audio') {
      parts.push('--audio');
    } else if (mode !== 'auto') {
      parts.push(`--as ${mode}`);
    }
    if (dest === 'photos') {
      parts.push('--photos');
    } else {
      parts.push(`--folder ${folderPath}`);
    }
    cmd = parts.join(' ');
  }

  const cmdEl = document.getElementById('router-command');
  const noteEl = document.getElementById('router-note');
  const siteEl = document.getElementById('router-site');
  const engineEl = document.getElementById('router-engine');
  const badgeEl = document.getElementById('router-engine-badge');
  const destEl = document.getElementById('router-dest');
  const safetyEl = document.getElementById('router-safety');
  const outEl = document.getElementById('router-output');

  if (cmdEl) cmdEl.textContent = cmd;
  if (siteEl) siteEl.textContent = preset.site;

  let engineLabel = preset.engines[dest] || preset.engines.folder;
  if (mode === 'audio' && !preset.isBatch) engineLabel = 'yt-dlp + ffmpeg audio';
  else if (mode === 'video' && !preset.isBatch) {
    engineLabel = dest === 'photos' ? 'yt-dlp -> Photos import' : 'yt-dlp + ffprobe';
  } else if (routerState.preset === 'live' && mode === 'photo') {
    engineLabel = 'native-douyin';
  }
  if (engineEl) engineEl.textContent = engineLabel;
  if (badgeEl) badgeEl.textContent = `${preset.site} · ${dest === 'photos' ? '--photos' : '--folder'}`;

  if (noteEl) {
    const dict = preset.notes[lang] || preset.notes.en;
    noteEl.textContent = resolveRouterNote(routerState.preset, dict, dest, mode);
  }

  const xArticlePhotos = routerState.preset === 'xarticle' && dest === 'photos';
  const livePhotosActive = routerState.preset === 'live' && dest === 'photos' && mode === 'auto';

  if (destEl) {
    if (dest === 'photos') {
      destEl.textContent = isZh ? 'Apple 照片图库（macOS Photos）' : 'Apple Photos Library';
    } else {
      destEl.textContent = folderPath;
    }
  }

  if (safetyEl) {
    if (preset.isBatch) {
      safetyEl.textContent = isZh ? '单站限流 + 保留原参数 retry' : 'per-site rate cap + checkpointed retry';
    } else if (xArticlePhotos) {
      safetyEl.textContent = isZh ? '禁网 Chromium 渲染 + 字节校验清理' : 'network-disabled Chromium + byte-verified cleanup';
    } else if (dest === 'photos') {
      safetyEl.textContent = isZh ? '托管副本字节一致性校验' : 'byte-for-byte Photos library verification';
    } else {
      safetyEl.textContent = isZh ? 'ffprobe 完整度检查 + 独立落盘目录' : 'ffprobe completeness check + verified folder copy';
    }
  }

  if (outEl) {
    if (preset.isBatch) {
      outEl.textContent = isZh
        ? `任务回执 + 分条目检查点\n├─ ${dest === 'photos' ? 'Apple 照片导入清单' : `${folderPath}/`}\n└─ 若出现暂时性限流，linkget retry 自动沿用 ${dest === 'photos' ? '--photos' : `--folder ${folderPath}`}`
        : `Job receipt + per-item checkpoints\n├─ ${dest === 'photos' ? 'Apple Photos verified imports' : `${folderPath}/`}\n└─ Transient errors resume via linkget retry keeping ${dest === 'photos' ? '--photos' : `--folder ${folderPath}`}`;
    } else if (xArticlePhotos) {
      outEl.textContent = isZh
        ? 'Apple 照片图库\n└─ 2026-11-27-user-article.png（禁网 Chromium 本地渲染）\n暂存目录：~/Library/Application Support/linkget/（字节核对后删除）'
        : 'Apple Photos Library\n└─ 2026-11-27-user-article.png (offline Chromium render)\nStaging: ~/Library/Application Support/linkget/ (removed after byte check)';
    } else if (effectiveMode === 'article') {
      outEl.textContent = `${folderPath}/<Article Title>/\n├─ article.md\n├─ article.html\n├─ content.txt\n├─ content.json\n├─ assets/\n└─ source/`;
    } else if (effectiveMode === 'audio') {
      outEl.textContent = `${folderPath}/\n└─ Track Title (tagged audio stream)`;
    } else if (livePhotosActive) {
      outEl.textContent = isZh
        ? 'Apple 照片图库\n├─ Live_Photo_01.HEIC + .MOV（已配对实况）\n└─ Live_Photo_02.HEIC + .MOV\n暂存目录：字节校验后自动清理'
        : 'Apple Photos Library\n├─ Live_Photo_01.HEIC + .MOV (paired motion)\n└─ Live_Photo_02.HEIC + .MOV\nStaging cleared only after byte verification';
    } else if (dest === 'photos') {
      outEl.textContent = isZh
        ? 'Apple 照片图库\n└─ 已导入并完成字节级核对（~/Library/Application Support/linkget/ 已清理）'
        : 'Apple Photos Library\n└─ Imported asset verified byte-for-byte in managed library';
    } else {
      outEl.textContent = `${folderPath}/\n└─ media_file.${effectiveMode === 'video' ? 'mp4' : 'jpg'} (completeness verified)`;
    }
  }
}

function initRouter() {
  const presetTabs = Array.from(document.querySelectorAll('#router-presets [role="tab"]'));
  const destButtons = Array.from(document.querySelectorAll('[data-dest]'));
  const modeButtons = Array.from(document.querySelectorAll('[data-mode]'));
  const routerPanel = document.getElementById('router-panel');

  function syncRouterButtons() {
    const validModes = allowedModesForState(routerState.preset);
    if (!validModes.includes(routerState.mode)) {
      routerState.mode = PRESETS[routerState.preset].defaultMode;
    }
    const validDests = allowedDestsForState(routerState.preset, routerState.mode);
    if (!validDests.includes(routerState.dest)) {
      routerState.dest = validDests[0] || 'folder';
    }

    presetTabs.forEach((tab) => {
      const active = tab.getAttribute('data-preset') === routerState.preset;
      tab.setAttribute('aria-selected', active ? 'true' : 'false');
      tab.tabIndex = active ? 0 : -1;
      if (active && routerPanel) {
        routerPanel.setAttribute('aria-labelledby', tab.id);
      }
    });
    destButtons.forEach((btn) => {
      const dest = btn.getAttribute('data-dest');
      const allowed = dest ? validDests.includes(dest) : false;
      btn.disabled = !allowed;
      btn.setAttribute('aria-pressed', allowed && dest === routerState.dest ? 'true' : 'false');
    });
    modeButtons.forEach((btn) => {
      const mode = btn.getAttribute('data-mode');
      const allowed = mode ? validModes.includes(mode) : false;
      btn.disabled = !allowed;
      btn.setAttribute('aria-pressed', allowed && mode === routerState.mode ? 'true' : 'false');
    });
    buildCommandAndPreview();
  }

  presetTabs.forEach((tab, idx) => {
    tab.addEventListener('click', () => {
      const key = tab.getAttribute('data-preset');
      if (!key || !PRESETS[key]) return;
      routerState.preset = key;
      routerState.dest = PRESETS[key].defaultDest;
      routerState.mode = PRESETS[key].defaultMode;
      syncRouterButtons();
    });
    tab.addEventListener('keydown', (ev) => {
      let next = -1;
      if (ev.key === 'ArrowRight') next = (idx + 1) % presetTabs.length;
      else if (ev.key === 'ArrowLeft') next = (idx - 1 + presetTabs.length) % presetTabs.length;
      else if (ev.key === 'Home') next = 0;
      else if (ev.key === 'End') next = presetTabs.length - 1;
      if (next >= 0) {
        ev.preventDefault();
        presetTabs[next].click();
        presetTabs[next].focus();
      }
    });
  });

  destButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      if (btn.disabled) return;
      const dest = btn.getAttribute('data-dest');
      if (!dest) return;
      routerState.dest = dest;
      syncRouterButtons();
    });
  });

  modeButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      if (btn.disabled) return;
      const mode = btn.getAttribute('data-mode');
      if (!mode) return;
      routerState.mode = mode;
      syncRouterButtons();
    });
  });

  syncRouterButtons();
}

function initThemeToggle() {
  const btn = document.getElementById('theme-toggle');
  const meta = document.querySelector('meta[name="color-scheme"]');
  if (!btn || !meta) return;

  const systemDarkMQ = window.matchMedia('(prefers-color-scheme: dark)');

  function currentRenderedTheme() {
    if (meta.content === 'dark' || meta.content === 'light') {
      return meta.content;
    }
    return systemDarkMQ.matches ? 'dark' : 'light';
  }

  function syncThemeAria() {
    const dict = I18N[activeLang] || I18N.en;
    const isDark = currentRenderedTheme() === 'dark';
    btn.setAttribute('aria-label', isDark ? dict['controls.toLight'] : dict['controls.toDark']);
  }

  btn.addEventListener('click', () => {
    const sys = systemDarkMQ.matches ? 'dark' : 'light';
    const next = currentRenderedTheme() === 'dark' ? 'light' : 'dark';
    try {
      if (next !== sys) {
        localStorage.setItem('linkget.theme', next);
        meta.content = next;
      } else {
        localStorage.removeItem('linkget.theme');
        meta.content = 'light dark';
      }
    } catch {
      meta.content = next;
    }
    syncThemeAria();
    const isZh = activeLang === 'zh-Hans';
    announce(
      next === 'dark'
        ? (isZh ? '已切换到深色模式' : 'Dark theme enabled')
        : (isZh ? '已切换到浅色模式' : 'Light theme enabled')
    );
  });

  systemDarkMQ.addEventListener('change', syncThemeAria);

  window.addEventListener('storage', (ev) => {
    if (ev.key !== 'linkget.theme') return;
    meta.content = ev.newValue === 'light' || ev.newValue === 'dark' ? ev.newValue : 'light dark';
    syncThemeAria();
  });

  syncThemeAria();
}

function applyLanguage(lang, { rebuildRouter = true } = {}) {
  const next = lang === 'zh-Hans' ? 'zh-Hans' : 'en';
  activeLang = next;
  document.documentElement.lang = next;
  const dict = I18N[next];
  if (dict['meta.title']) {
    document.title = dict['meta.title'];
  }
  document.querySelectorAll('[data-i18n]').forEach((el) => {
    const key = el.getAttribute('data-i18n');
    if (key && Object.hasOwn(dict, key)) {
      el.textContent = dict[key];
    }
  });
  const langBtn = document.getElementById('lang-toggle');
  if (langBtn && dict['controls.langAria']) {
    langBtn.setAttribute('aria-label', dict['controls.langAria']);
  }
  const tableRegion = document.getElementById('sites-table-region');
  if (tableRegion && dict['sites.tableAria']) {
    tableRegion.setAttribute('aria-label', dict['sites.tableAria']);
  }
  const themeBtn = document.getElementById('theme-toggle');
  const meta = document.querySelector('meta[name="color-scheme"]');
  if (themeBtn && meta) {
    const sysDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const isDark = meta.content === 'dark' || (meta.content !== 'light' && sysDark);
    themeBtn.setAttribute('aria-label', isDark ? dict['controls.toLight'] : dict['controls.toDark']);
  }
  if (rebuildRouter) {
    buildCommandAndPreview();
  }
}

function initLanguageToggle() {
  const btn = document.getElementById('lang-toggle');
  applyLanguage(document.documentElement.lang, { rebuildRouter: false });
  if (!btn) return;
  btn.addEventListener('click', () => {
    const target = activeLang === 'zh-Hans' ? 'en' : 'zh-Hans';
    try {
      localStorage.setItem('linkget.lang', target);
    } catch {}
    applyLanguage(target, { rebuildRouter: true });
    announce(target === 'zh-Hans' ? '已切换为中文' : 'Switched to English');
  });
}

function initTabs() {
  document.querySelectorAll('[data-tabs-widget]').forEach((widget) => {
    const tabs = Array.from(widget.querySelectorAll('[role="tab"]'));
    const panels = Array.from(widget.querySelectorAll(':scope > [role="tabpanel"]'));

    function selectTab(targetTab, { focusTab = false } = {}) {
      if (!targetTab) return;
      tabs.forEach((tab) => {
        const isSel = tab === targetTab;
        tab.setAttribute('aria-selected', isSel ? 'true' : 'false');
        tab.tabIndex = isSel ? 0 : -1;
        tab.toggleAttribute('focusgroupstart', isSel);
      });
      panels.forEach((panel) => {
        const match = panel.getAttribute('aria-labelledby') === targetTab.id;
        if (match) {
          panel.removeAttribute('hidden');
          panel.tabIndex = 0;
        } else {
          panel.setAttribute('hidden', 'until-found');
          panel.removeAttribute('tabindex');
        }
      });
      if (focusTab) {
        targetTab.focus();
      }
    }

    tabs.forEach((tab, idx) => {
      tab.addEventListener('click', () => selectTab(tab));
      tab.addEventListener('keydown', (ev) => {
        let nextIdx = -1;
        if (ev.key === 'ArrowRight') nextIdx = (idx + 1) % tabs.length;
        else if (ev.key === 'ArrowLeft') nextIdx = (idx - 1 + tabs.length) % tabs.length;
        else if (ev.key === 'Home') nextIdx = 0;
        else if (ev.key === 'End') nextIdx = tabs.length - 1;
        if (nextIdx >= 0) {
          ev.preventDefault();
          selectTab(tabs[nextIdx], { focusTab: true });
        }
      });
    });

    panels.forEach((panel) => {
      panel.addEventListener('beforematch', () => {
        const ownerTab = tabs.find((t) => t.id === panel.getAttribute('aria-labelledby'));
        if (ownerTab) selectTab(ownerTab);
      });
    });
  });
}

function initSitesFilter() {
  const bar = document.getElementById('sites-filters');
  const rows = Array.from(document.querySelectorAll('#sites-body tr'));
  if (!bar || !rows.length) return;
  const buttons = Array.from(bar.querySelectorAll('button[data-filter]'));

  function applyFilter(targetBtn, { focusBtn = false } = {}) {
    const filter = targetBtn.getAttribute('data-filter') || 'all';
    buttons.forEach((b) => {
      const active = b === targetBtn;
      b.setAttribute('aria-pressed', active ? 'true' : 'false');
      b.tabIndex = active ? 0 : -1;
    });
    rows.forEach((row) => {
      const cats = (row.getAttribute('data-cat') || '').split(/\s+/);
      row.hidden = filter !== 'all' && !cats.includes(filter);
    });
    if (focusBtn) {
      targetBtn.focus();
    }
  }

  buttons.forEach((btn, idx) => {
    btn.addEventListener('click', () => applyFilter(btn));
    btn.addEventListener('keydown', (ev) => {
      let nextIdx = -1;
      if (ev.key === 'ArrowRight') nextIdx = (idx + 1) % buttons.length;
      else if (ev.key === 'ArrowLeft') nextIdx = (idx - 1 + buttons.length) % buttons.length;
      else if (ev.key === 'Home') nextIdx = 0;
      else if (ev.key === 'End') nextIdx = buttons.length - 1;
      if (nextIdx >= 0) {
        ev.preventDefault();
        applyFilter(buttons[nextIdx], { focusBtn: true });
      }
    });
  });
}

function initCopyButtons() {
  const timers = new WeakMap();
  document.querySelectorAll('[data-copy-target]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const targetId = btn.getAttribute('data-copy-target');
      const target = targetId ? document.getElementById(targetId) : null;
      if (!target) return;
      const text = target.textContent || '';
      const dict = I18N[activeLang] || I18N.en;
      const origKey = btn.getAttribute('data-i18n') || 'copy.btn';
      try {
        await navigator.clipboard.writeText(text);
        btn.textContent = dict['copy.done'];
        announce(dict['copy.done']);
        const prevTimer = timers.get(btn);
        if (prevTimer) window.clearTimeout(prevTimer);
        const timer = window.setTimeout(() => {
          btn.textContent = (I18N[activeLang] || I18N.en)[origKey];
          timers.delete(btn);
        }, 1600);
        timers.set(btn, timer);
      } catch {
        announce(dict['copy.fail']);
      }
    });
  });
}

function initScrollRevealFallback() {
  const nativeSupported =
    typeof CSS !== 'undefined' &&
    typeof CSS.supports === 'function' &&
    CSS.supports('(animation-timeline: view()) and (animation-range: entry)');
  if (nativeSupported) return;
  if (!window.matchMedia('(prefers-reduced-motion: no-preference)').matches) return;

  const blocks = document.querySelectorAll('.reveal-block');
  if (!blocks.length || typeof IntersectionObserver === 'undefined') return;

  blocks.forEach((el) => el.classList.add('reveal-fallback'));
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible');
          observer.unobserve(entry.target);
        }
      }
    },
    { threshold: 0.12 }
  );
  blocks.forEach((el) => observer.observe(el));
}

document.addEventListener('DOMContentLoaded', () => {
  initLanguageToggle();
  initThemeToggle();
  initRouter();
  initTabs();
  initSitesFilter();
  initCopyButtons();
  initScrollRevealFallback();
});

function (expected) {
    // Read normal rendered post content. This runs only in an owned temporary
    // tab; no cookie, storage, account globals or off-page API is accessed.
    const identity = raw => {
        try {
            const u = new URL(raw), post = /^\/(article|video)\/([0-9]+)\/?$/.exec(u.pathname);
            if (u.protocol !== 'https:' || u.hostname !== 'www.toutiao.com' || u.username || u.password || u.port || !post) return null;
            return 'https://www.toutiao.com/' + post[1] + '/' + post[2] + '/';
        } catch (_) { return null; }
    };
    const actual = identity(location.href);
    expected = expected || actual;
    if (!actual || actual !== expected) return {diagnostic: {reason: 'post_mismatch'}};
    const title = (document.querySelector('h1')?.innerText || '').replace(/^\s*(?:\.\.\.\s*)?展开\s*/, '').trim();
    if (!title || title.length > 4000) return null;
    const kind = new URL(actual).pathname.split('/')[1];
    const result = {url: expected, final_url: actual, title, kind, acquisition: 'chrome_rendered_page'};
    if (kind === 'article') {
        const article = document.querySelector('article'), text = article?.innerText?.trim();
        if (!text || text.length < 100) return null;
        let scopeProof;
        if (document.readyState !== 'complete') {
            // Neo observes the actual paragraph range, not unrelated player or
            // advertising requests that can keep the whole page interactive.
            // A new page without this read's exact quiet-range evidence waits.
            const scope = article.__linkgetArticleScopeReady;
            const paragraphs = [...article.querySelectorAll('p')].map(p => p.innerText.trim()).filter(Boolean);
            const owner = document.documentElement?.dataset.linkgetNeoReadOwner;
            if (document.readyState !== 'interactive' || !owner || !scope || scope.owner !== owner ||
                scope.stable_ms !== 1500 || scope.paragraph_count !== paragraphs.length ||
                paragraphs.length < 2 || scope.paragraph_text !== paragraphs.join('\n\n')) return null;
            scopeProof = {basis: 'stable_rendered_article_range', document_state: 'interactive',
                stable_ms: scope.stable_ms, paragraph_count: paragraphs.length,
                paragraph_chars: scope.paragraph_text.length, pending_images: 0,
                visible_loaders: 0, visible_expand_controls: 0, clipped_article: false,
                end_excerpt: [...text].slice(-512).join('')};
        }
        if (new TextEncoder().encode(text).length > 200000) return {diagnostic: {reason: 'article_scope_size_out_of_bounds'}};
        if (/登录后查看全部|阅读全文需|继续阅读需/.test(text)) return {diagnostic: {reason: 'article_access_gate'}};
        return {...result, text, content_complete: true, ...(scopeProof ? {article_dom_proof: scopeProof} : {})};
    }
    const player = document.querySelector('video');
    if (!player) return null;
    // Muted playback exposes the existing site's player resources. Loading a
    // MediaSource blob would detach its split buffers, so only direct files
    // receive the one-time load request.
    player.muted = true;
    if (!player.loop) player.loop = true;
    if (player.readyState === 0 && player.currentSrc?.startsWith('https://') && player.dataset.readerLoaded !== '1') {
        player.dataset.readerLoaded = '1';
        player.load();
    }
    if (player.paused) player.play().catch(() => {});
    const duration = Number.isFinite(player.duration) && player.duration > 0 ? player.duration : null;
    const allowedMedia = raw => {
        try {
            const u = new URL(raw);
            return u.protocol === 'https:' && !u.username && !u.password && !u.port &&
                u.hostname.endsWith('.toutiaovod.com') ? u : null;
        } catch (_) { return null; }
    };
    if (allowedMedia(player.currentSrc)) {
        const tracks = Array.from(player.textTracks || []).filter(track => ['subtitles', 'captions'].includes(track.kind));
        const captions = duration === null ? [] : tracks.flatMap(track => Array.from(track.cues || []).map(cue =>
            ({text: cue.text, start: cue.startTime, end: cue.endTime})));
        return {...result, duration, duration_pending: duration === null, media_url: player.currentSrc,
            media_binding: 'player_current_src', captions};
    }
    if (!player.currentSrc?.startsWith('blob:')) return null;
    const resources = performance.getEntriesByType('resource').map(entry => allowedMedia(entry.name)).filter(Boolean);
    if (resources.length > 256) return {diagnostic: {reason: 'ambiguous_media_resources'}};
    // Keep the last observation of each byte-range path. Adaptive playback can
    // expose several video variants, but only one audio path from one exact
    // page request batch may accompany them.
    const paths = new Map(resources.map((url, index) => [url.origin + url.pathname, {url, index}]));
    if (paths.size >= 2 && paths.size <= 5) {
        const role = u => u.pathname.includes('/video/tos/') &&
            (u.pathname.endsWith('/media-video-avc1/') ? 'video' :
             u.pathname.endsWith('/media-audio-und-mp4a/') ? 'audio' : null);
        const entries = [...paths.values()];
        const videos = entries.filter(entry => role(entry.url) === 'video');
        const audios = entries.filter(entry => role(entry.url) === 'audio');
        const audio = audios.length === 1 ? audios[0].url : null;
        // Validate every observed URL before deduplication: an older request
        // from another batch must not disappear behind a newer identical path.
        const sameBatch = audio && resources.every(u => role(u) && u.origin === audio.origin &&
            ['l', 'dy_q'].every(key => u.searchParams.getAll(key).length === 1 &&
                audio.searchParams.getAll(key).length === 1 && u.searchParams.get(key) &&
                u.searchParams.get(key) === audio.searchParams.get(key)));
        if (videos.length >= 1 && videos.length <= 4 && sameBatch) {
            const selected = videos.reduce((latest, entry) => entry.index > latest.index ? entry : latest);
            return {...result, duration, duration_pending: duration === null,
                media_streams: {video: selected.url.href, audio: audio.href},
                media_binding: 'paired_page_vod_resources', captions: [],
                media_selection: {strategy: 'latest_observed_video_variant', video_variant_count: videos.length,
                    audio_resource_count: 1, selected_video_resource_index: selected.index, same_request_batch: true}};
        }
    }
    if (paths.size === 1 && duration !== null) return {...result, duration,
        media_url: [...paths.values()][0].url.href, media_binding: 'single_page_vod_resource', captions: []};
    return null;
}

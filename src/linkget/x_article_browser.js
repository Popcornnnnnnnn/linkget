function (expected) {
    // Bounded X Article DOM reader; no cookies, storage, or off-page requests.
    const normalize = raw => {
        try {
            const u = new URL(raw);
            if (u.protocol !== 'https:' || u.username || u.password || u.port || u.search) return null;
            if (!/^(?:www\.|mobile\.)?(?:x|twitter)\.com$/.test(u.hostname)) return null;
            const path = u.pathname.replace(/\/$/, '');
            if (/^\/(?:[A-Za-z0-9_]{1,15}\/(?:status|article)|i\/web\/status)\/[0-9]{1,25}$/.test(path)) {
                return 'https://x.com' + path;
            }
        } catch (_) {}
        return null;
    };
    const tidy = value => String(value || '').replace(/\r/g, '').replace(/[ \t\u00a0\u202f]+/g, ' ')
        .replace(/\n{3,}/g, '\n\n').trim();
    const upgradeMediaUrl = raw => {
        try {
            const u = new URL(raw);
            if (u.hostname !== 'pbs.twimg.com' || !u.pathname.startsWith('/media/')) return raw;
            const extMatch = /^(\/media\/[A-Za-z0-9_-]+)\.(jpg|jpeg|png|webp|gif)$/i.exec(u.pathname);
            if (extMatch) {
                const fmt = extMatch[2].toLowerCase() === 'jpeg' ? 'jpg' : extMatch[2].toLowerCase();
                return `https://pbs.twimg.com${extMatch[1]}?format=${fmt}&name=orig`;
            }
            if (u.searchParams.has('format')) {
                u.searchParams.set('name', 'orig');
                return `https://pbs.twimg.com${u.pathname}?${u.searchParams.toString()}`;
            }
        } catch (_) {}
        return raw;
    };

    if (/^\/i\/(?:flow\/login|jf\/onboarding)/.test(location.pathname)) {
        return {diagnostic: {reason: 'x_article_login_required'}};
    }
    const actual = normalize(location.href);
    if (!actual || actual !== expected) return {diagnostic: {reason: 'public_article_mismatch'}};

    const readView = document.querySelector('[data-testid="twitterArticleReadView"]');
    if (!readView) {
        const bodyText = document.body?.innerText || '';
        if (document.querySelector('[data-testid="loginButton"]') && /sign in|log in|登录/i.test(bodyText)) {
            return {diagnostic: {reason: 'x_article_login_required'}};
        }
        return null;
    }

    const title = tidy(
        readView.querySelector('[data-testid="twitter-article-title"]')?.textContent ||
        document.querySelector('[data-testid="twitter-article-title"]')?.textContent
    );
    const richView = readView.querySelector('[data-testid="twitterArticleRichTextView"]');
    if (!title || !richView) return null;

    const scopeRoot = readView.closest('article') || readView.parentElement || document;
    let author = null;
    let authorHandle = null;
    const userEl = scopeRoot.querySelector('[data-testid="User-Name"]');
    if (userEl) {
        const rawUser = tidy(userEl.innerText || userEl.textContent || '');
        const handleMatch = /@([A-Za-z0-9_]{1,32})/.exec(rawUser);
        if (handleMatch) authorHandle = handleMatch[1];
        const firstLine = rawUser.split(/\n|·/)[0]?.replace(/@[A-Za-z0-9_]{1,32}/g, '').trim();
        if (firstLine && authorHandle) author = `${firstLine} (@${authorHandle})`;
        else if (authorHandle) author = `@${authorHandle}`;
        else if (firstLine) author = firstLine;
    }

    const timeEl = scopeRoot.querySelector('time[datetime]') || document.querySelector('article time[datetime]');
    const publishedAt = timeEl?.getAttribute('datetime') || null;

    let coverImage = null;
    const inlineImages = [];
    const seenImgs = new Set();
    for (const img of readView.querySelectorAll('img')) {
        const src = String(img.getAttribute('src') || '');
        if (!src || src.includes('/emoji/')) continue;
        if (!/^https?:\/\//i.test(src)) continue;
        const upgraded = upgradeMediaUrl(src);
        if (!richView.contains(img) && !coverImage) {
            coverImage = upgraded;
        } else if (!seenImgs.has(upgraded)) {
            seenImgs.add(upgraded);
            inlineImages.push(upgraded);
        }
    }

    const videos = [];
    const seenVideos = new Set();
    for (const vid of readView.querySelectorAll('video')) {
        const vsrc = String(vid.currentSrc || vid.getAttribute('src') || '');
        if (/^https?:\/\//i.test(vsrc) && !seenVideos.has(vsrc)) {
            seenVideos.add(vsrc);
            videos.push({url: vsrc, poster: vid.getAttribute('poster') ? upgradeMediaUrl(vid.getAttribute('poster')) : null});
        }
    }

    const clone = richView.cloneNode(true);
    clone.querySelectorAll('script,style,noscript,iframe,form,button').forEach(node => node.remove());
    clone.querySelectorAll('img').forEach(img => {
        const src = String(img.getAttribute('src') || '');
        if (src.includes('/emoji/')) {
            const alt = img.getAttribute('alt') || '';
            img.replaceWith(document.createTextNode(alt));
        } else if (/^https?:\/\//i.test(src)) {
            img.setAttribute('src', upgradeMediaUrl(src));
        } else {
            img.remove();
        }
    });
    clone.querySelectorAll('*').forEach(el => {
        for (const attr of Array.from(el.attributes || [])) {
            const name = attr.name.toLowerCase();
            if (!['href', 'src', 'alt', 'poster', 'title'].includes(name)) {
                el.removeAttribute(attr.name);
            }
        }
    });

    const bodyHtml = clone.innerHTML || '';
    const textClone = clone.cloneNode(true);
    textClone.querySelectorAll('p,div,section,article,h1,h2,h3,h4,h5,h6,li,ul,ol,table,tr,blockquote,pre,br')
        .forEach(node => { node.before('\n'); node.after('\n'); });
    const text = tidy(textClone.textContent);
    if (!text) return null;

    const totalBytes = new TextEncoder().encode(JSON.stringify({title, text, bodyHtml})).length;
    if (totalBytes < 40 || totalBytes > 240000 || title.length > 300) {
        return {diagnostic: {reason: 'article_scope_size_out_of_bounds'}};
    }

    return {
        url: expected,
        final_url: actual,
        title,
        author: author || null,
        author_handle: authorHandle || null,
        published_at: publishedAt,
        cover_image: coverImage,
        images: inlineImages,
        videos,
        kind: 'article',
        text,
        body_html: bodyHtml,
        content_complete: true,
        dom_proof: {
            selector: '[data-testid="twitterArticleReadView"]',
            reader_revision: 1,
            basis: 'bounded_x_article_dom',
            http_transfer_verified: false,
            end_excerpt: [...text].slice(-512).join(''),
        },
    };
}

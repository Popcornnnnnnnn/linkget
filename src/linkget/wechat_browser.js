function (expected) {
    // Bounded WeChat Official Account DOM reader; no credentials or unrelated tabs.
    const normalize = raw => {
        try {
            const u = new URL(raw);
            if (u.protocol !== 'https:' || u.username || u.password || u.port || u.search) return null;
            if (u.hostname === 'mp.weixin.qq.com' && /^\/s\/[A-Za-z0-9_-]{8,128}\/?$/.test(u.pathname)) {
                return u.origin + u.pathname.replace(/\/$/, '');
            }
        } catch (_) {}
        return null;
    };
    const tidy = value => String(value || '').replace(/\r/g, '').replace(/[ \t\u00a0\u202f]+/g, ' ')
        .replace(/\n{3,}/g, '\n\n').trim();
    const bodyText = element => {
        const copy = element.cloneNode(true);
        copy.querySelectorAll('script,style,noscript,nav,form,iframe').forEach(node => node.remove());
        copy.querySelectorAll('p,div,section,article,h1,h2,h3,h4,h5,h6,li,ul,ol,table,tr,blockquote,pre,br')
            .forEach(node => { node.before('\n'); node.after('\n'); });
        return tidy(copy.textContent);
    };
    const actual = normalize(location.href);
    if (/secitptpage|wappoc_appmsgcaptcha/.test(location.pathname + location.search)) {
        return {diagnostic: {reason: 'article_access_gate'}};
    }
    if (!actual || actual !== expected) return {diagnostic: {reason: 'public_article_mismatch'}};
    if (document.readyState !== 'complete') return null;
    const body = document.querySelector('#js_content');
    const textBody = body ? bodyText(body) : '';
    const pageText = document.body?.innerText || document.body?.textContent || '';
    if (!textBody && /环境异常|完成验证|请在微信客户端打开|此内容因违规无法查看|该内容已被发布者删除|该公众号已迁移/.test(pageText)) {
        return {diagnostic: {reason: 'article_access_gate'}};
    }
    const title = tidy(document.querySelector('#activity-name')?.textContent) ||
        tidy(document.querySelector('meta[property="og:title"]')?.getAttribute('content'));
    if (!title || !textBody) return null;
    const author = tidy(document.querySelector('#js_name')?.textContent);
    const publishedAt = tidy(document.querySelector('#publish_time')?.textContent);
    const text = [title, textBody].join('\n\n');
    const bytes = new TextEncoder().encode(text).length;
    if (bytes < 40 || bytes > 120000 || title.length > 300) {
        return {diagnostic: {reason: 'article_scope_size_out_of_bounds'}};
    }

    const coverImage = document.querySelector('meta[property="og:image"]')?.getAttribute('content') ||
        document.querySelector('meta[name="twitter:image"]')?.getAttribute('content') || null;
    const images = [];
    const seenImgs = new Set();
    let bodyHtml = null;
    if (body && typeof body.cloneNode === 'function') {
        const htmlCopy = body.cloneNode(true);
        if (typeof htmlCopy.querySelectorAll === 'function') {
            htmlCopy.querySelectorAll('script,style,noscript,nav,form,iframe').forEach(node => node.remove());
            htmlCopy.querySelectorAll('img').forEach(img => {
                const src = String(img.getAttribute('data-src') || img.getAttribute('src') || '').trim();
                if (/^https?:\/\//i.test(src)) {
                    img.setAttribute('src', src);
                    if (!seenImgs.has(src)) {
                        seenImgs.add(src);
                        images.push(src);
                    }
                }
            });
            htmlCopy.querySelectorAll('*').forEach(el => {
                for (const attr of Array.from(el.attributes || [])) {
                    const name = attr.name.toLowerCase();
                    if (!['href', 'src', 'data-src', 'alt', 'poster', 'title', 'colspan', 'rowspan'].includes(name)) {
                        el.removeAttribute(attr.name);
                    }
                }
            });
            const candidateHtml = htmlCopy.innerHTML || '';
            if (candidateHtml && new TextEncoder().encode(candidateHtml).length <= 120000) {
                bodyHtml = candidateHtml;
            }
        }
    }

    return {
        url: expected,
        final_url: actual,
        title,
        author: author || null,
        published_at: publishedAt || null,
        cover_image: coverImage && /^https?:\/\//i.test(coverImage) ? coverImage : null,
        images,
        body_html: bodyHtml,
        kind: 'article',
        text,
        content_complete: true,
        dom_proof: {
            selector: '#js_content',
            reader_revision: 1,
            basis: 'bounded_wechat_article_dom',
            http_transfer_verified: false,
            end_excerpt: [...text].slice(-512).join(''),
        },
    };
}

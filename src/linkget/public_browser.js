function (expected) {
    // MIT reader: normal rendered article DOM only, no credentials or APIs.
    const normalize = raw => {
        try {
            const u = new URL(raw);
            if (u.protocol !== 'https:' || u.username || u.password || u.port || u.search || u.hash) return null;
            if (u.hostname === 'www.raspberrypi.com' && /^\/products\/raspberry-pi-5\/?$/.test(u.pathname))
                return 'https://www.raspberrypi.com/products/raspberry-pi-5/';
            if (u.hostname === 'zhuanlan.zhihu.com' && /^\/p\/[0-9]{1,20}\/?$/.test(u.pathname))
                return u.origin + u.pathname.replace(/\/$/, '');
        } catch (_) {}
        return null;
    };
    const tidy = value => String(value || '').replace(/\r/g, '').replace(/[ \t\u00a0\u202f]+/g, ' ')
        .replace(/\n{3,}/g, '\n\n').trim();
    const bodyText = element => {
        const copy = element.cloneNode(true);
        // main / RichText is already scoped. Section headers and footers may
        // contain specifications, FAQ titles or attribution and must survive.
        copy.querySelectorAll('script,style,noscript,nav,form,iframe').forEach(node => node.remove());
        // Preserve block boundaries in a detached DOM copy. textContent alone
        // joins headings and labels ("Tech specsFAQs") and also merges code
        // paragraphs. Do not append the copy to the live page.
        copy.querySelectorAll('p,div,section,article,h1,h2,h3,h4,h5,h6,li,ul,ol,table,tr,details,summary,br')
            .forEach(node => { node.before('\n'); node.after('\n'); });
        return tidy(copy.textContent);
    };
    const actual = normalize(location.href);
    if (!actual || actual !== expected) return {diagnostic: {reason: 'public_article_mismatch'}};
    if (document.readyState !== 'complete') return null;
    let title, text, scope;
    if (actual === 'https://www.raspberrypi.com/products/raspberry-pi-5/') {
        const main = document.querySelector('main'), h1 = main?.querySelector('h1');
        title = tidy(h1?.textContent) || tidy(h1?.querySelector('img')?.getAttribute('alt'));
        if (!main || title !== 'Raspberry Pi 5') return null;
        text = bodyText(main);
        const sections = {};
        const patterns = {specifications: /\b(?:specifications?|tech specs)\b/i,
            faq: /\bFAQs?\b|frequently asked questions|your questions answered/i};
        for (const [name, pattern] of Object.entries(patterns)) {
            const match = pattern.exec(text);
            if (!match) return null;
            const offset = [...text.slice(0, match.index)].length;
            sections[name] = {offset, excerpt: [...text].slice(offset, offset + 1000).join('')};
        }
        if (!/BCM2712/i.test(text)) return null;
        scope = {selector: 'main', sections};
    } else {
        const article = document.querySelector('article');
        title = tidy(document.querySelector('h1.Post-Title')?.textContent);
        const selectors = ['.Post-RichTextContainer .RichText', '.Post-RichText', 'article .RichText'];
        const selector = selectors.find(value => document.querySelector(value));
        const body = selector && document.querySelector(selector);
        const publication = tidy(article?.querySelector('.ContentItem-time, .Post-Time')?.textContent);
        if (!article || !title || !body || !/(发布于|编辑于).*?(?:19|20)[0-9]{2}[-年/]/.test(publication)) return null;
        if (article.querySelector('[class*="Paywall"], [class*="VipRead"], .RichContent-collapsedText') ||
            /开通会员.{0,20}(阅读全文|继续阅读)|登录后查看全部|阅读全文需|继续阅读需/.test(article.textContent || ''))
            return {diagnostic: {reason: 'article_access_gate'}};
        text = [title, bodyText(body), publication].join('\n\n');
        scope = {selector, publication_text: publication};
    }
    const bytes = new TextEncoder().encode(text).length;
    if (bytes < 200 || bytes > 120000 || title.length > 300) return {diagnostic: {reason: 'article_scope_size_out_of_bounds'}};
    return {url: expected, final_url: actual, title, kind: 'article', text, content_complete: true,
        dom_proof: {...scope, reader_revision: 2, basis: 'bounded_article_dom', http_transfer_verified: false,
            end_excerpt: [...text].slice(-512).join('')}};
}

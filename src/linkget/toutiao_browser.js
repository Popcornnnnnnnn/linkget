function () {
    // Read rendered content only; never cookies, storage, account tokens or globals.
    if (location.hostname !== 'www.toutiao.com') return null;
    const match = location.pathname.match(/^\/(video|article)\/(\d+)\/?$/);
    if (!match) return null;
    const heading = document.querySelector('h1');
    const title = (heading?.innerText || '').replace(/^\s*(?:\.\.\.\s*)?展开\s*/, '').trim();
    if (!title) return null;
    const result = {url: `https://www.toutiao.com/${match[1]}/${match[2]}/`, title,
        kind: match[1], acquisition: 'chrome_rendered_page'};
    if (match[1] === 'article') {
        const article = document.querySelector('article');
        if (!article || article.innerText.trim().length < 100) return null;
        return {...result, text: article.innerText.trim(), content_complete: true};
    }
    const video = document.querySelector('video');
    if (!video?.currentSrc || !Number.isFinite(video.duration) || video.duration <= 0) return null;
    const tracks = Array.from(video.textTracks).filter(t => t.kind === 'subtitles' || t.kind === 'captions');
    const captions = tracks.flatMap(t => t.cues ? Array.from(t.cues).map(c => ({
        text: c.text, start: c.startTime, end: c.endTime})) : []);
    return {...result, description: title, media_url: video.currentSrc,
        duration: video.duration, captions};
}

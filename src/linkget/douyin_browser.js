function snapshot() {
  const postId = location.pathname.match(/\/(?:note|video)\/(\d+)\/?$/)?.[1];
  if (!postId) return null;
  const player = document.querySelector('[data-e2e="player-container"]');
  if (!player) return null;
  const images = [...new Set(Array.from(player.querySelectorAll('.dySwiperSlide img'))
    .map(image => image.currentSrc || image.src).filter(Boolean))];
  if (images.length) {
    const counter = player.innerText.match(/(?:^|\n)(\d+)\/(\d+)(?:\n|$)/);
    if (counter) {
      if (images.length !== Number(counter[2])) return null;
    } else {
      // Single-picture posts omit the counter. Accept only the fully loaded
      // single-slide panel, not a lone loaded image from a larger gallery.
      const slides = Array.from(player.querySelectorAll('.dySwiperSlide'));
      if (slides.length !== 1 || images.length !== 1) return null;
      const slide = slides[0];
      const track = slide.parentElement;
      const panel = track && track.parentElement;
      if (!track || track.children.length !== 1 || !panel
          || !panel.classList.contains('focusPanel') || panel.children.length !== 1) return null;
      const pictures = Array.from(slide.querySelectorAll('img'));
      if (!pictures.length || pictures.some(image => !image.complete || !image.naturalWidth
          || !/^https:/.test(image.currentSrc || image.src))) return null;
    }
    const pictures = Array.from(player.querySelectorAll('.dySwiperSlide img'));
    if (pictures.some(image => !image.complete || !image.naturalWidth)) return null;
    // Absence of a rendered <video> does not prove a still is static: motion
    // can mount later or on an inactive slide. Read only this post's image
    // content model, never account/session props or top-level music media.
    const fiberKey = Object.keys(player).find(key => key.startsWith('__reactFiber$'));
    let fiber = player[fiberKey], metadata;
    for (let depth = 0; fiber && depth < 20; depth++, fiber = fiber.return) {
      const info = fiber.memoizedProps?.awemeInfo;
      if (postId && info?.awemeId === postId) { metadata = info.images; break; }
    }
    if (!Array.isArray(metadata) || metadata.length !== images.length) return null;
    const used = new Set(), items = [];
    for (const url of images) {
      const matches = metadata.filter(image => image.urlList?.includes(url));
      if (matches.length !== 1 || used.has(matches[0])) return null;
      const source = matches[0];
      used.add(source);
      // The desktop model explicitly normalizes every image's video field
      // (undefined for static images). Unknown shapes must keep polling.
      if (!Object.prototype.hasOwnProperty.call(source, 'video') && source.livePhotoType == null) return null;
      const item = {url_list: [url], live_photo_type: 0};
      if (source.video || source.livePhotoType) {
        const video = source.video;
        if (!video) return null;
        const variants = [...(video.bitRateList || []), video].filter(entry =>
          entry.width > 0 && entry.height > 0 && entry.playAddr?.some(address => /^https:/.test(address.src)));
        variants.sort((a, b) => b.width * b.height - a.width * a.height || (b.bitRate || 0) - (a.bitRate || 0));
        if (!variants.length) return null;
        const best = variants[0];
        item.live_photo_type = 1;
        item.video = {play_addr: {url_list: best.playAddr.map(address => address.src).filter(url => /^https:/.test(url))},
                      width: best.width, height: best.height};
      }
      items.push(item);
    }
    return {aweme_id: postId, images: items};
  }
  const video = Array.from(player.querySelectorAll('video')).find(v => v.videoWidth && /^https:/.test(v.currentSrc));
  if (!video) return null;
  const gears = Array.from(player.querySelectorAll('.gear .item'))
    .map(element => {
      const match = element.textContent.match(/^(?:高清|超清|标清)\s+(\d+)(P|K)$/);
      const height = !match ? 0 : match[2] === 'P' ? Number(match[1]) : ({2: 1440, 4: 2160}[match[1]] || 0);
      return {element, height};
    })
    .filter(gear => gear.height).sort((a, b) => b.height - a.height);
  const best = gears[0];
  if (best && !best.element.classList.contains('selected')) {
    best.element.click();
    return null;
  }
  if (video.readyState < 2 || player.innerText.includes('正在切换到')) return null;
  return {aweme_id: postId, video: {play_addr: {url_list: [video.currentSrc], width: video.videoWidth, height: video.videoHeight}}};
}

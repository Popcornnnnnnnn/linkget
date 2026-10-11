function snapshot() {
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
    return {images: images.map(url => ({url_list: [url]}))};
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
  return {video: {play_addr: {url_list: [video.currentSrc], width: video.videoWidth, height: video.videoHeight}}};
}

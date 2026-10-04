function snapshot() {
  const player = document.querySelector('[data-e2e="player-container"]');
  if (!player) return null;
  const images = [...new Set(Array.from(player.querySelectorAll('.dySwiperSlide img'))
    .map(image => image.currentSrc || image.src).filter(Boolean))];
  if (images.length) {
    const counter = player.innerText.match(/(?:^|\n)(\d+)\/(\d+)(?:\n|$)/);
    if (!counter || images.length !== Number(counter[2])) return null;
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

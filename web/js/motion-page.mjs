export function motionPage(clips, requestedPage) {
  const pageSize = 24;
  const pages = Math.max(1, Math.ceil(clips.length / pageSize));
  const page = Math.min(pages, Math.max(1, Math.trunc(Number(requestedPage) || 1)));
  return {page, pages, total: clips.length, items: clips.slice((page - 1) * pageSize, page * pageSize)};
}

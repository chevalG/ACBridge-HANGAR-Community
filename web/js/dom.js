export const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g,
  ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
export const jobPercent = job => Math.round(Math.max(0, Math.min(100,
  job.progress_fraction != null ? Number(job.progress_fraction) * 100 : Number(job.progress) || 0)));

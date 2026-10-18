// The server writes times in the zone named by the tz cookie, so keep it the browser's.
// Reload once when this page was written for another zone, but only if the cookie
// stuck: with cookies blocked, every load would reload again.
const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
const hasZoneCookie = () => document.cookie.split("; ").includes(`tz=${zone}`);
if (zone && !hasZoneCookie()) {
  document.cookie = `tz=${zone}; path=/; max-age=31536000; samesite=lax`;
  if (hasZoneCookie() && document.documentElement.dataset.tz !== zone) location.reload();
}

// Picking an edition fills in that edition's title and product code.
document.addEventListener("change", (event) => {
  const picker = event.target;
  if (!(picker instanceof HTMLSelectElement) || !picker.classList.contains("edition-picker")) {
    return;
  }
  const option = picker.selectedOptions[0];
  const form = picker.form;
  if (!option || !option.value || !form) return;
  form.elements.namedItem("name").value = option.dataset.title;
  form.elements.namedItem("model_number").value = option.dataset.productCode;
});

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

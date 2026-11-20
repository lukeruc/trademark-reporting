'use strict';
// 仅显示“是／否”两个选项，未填写的草稿保持未选择，避免自动记录为“是”。
document.querySelectorAll('select[data-yes-no]').forEach((select) => {
  if (!select.querySelector('option[selected]')) {
    select.selectedIndex = -1;
  }
});
document.querySelectorAll('[data-dirty-form]').forEach((form) => {
  let dirty = false;
  form.addEventListener('input', () => { dirty = true; });
  form.addEventListener('change', () => { dirty = true; });
  form.addEventListener('submit', () => { dirty = false; });
  window.addEventListener('beforeunload', (event) => {
    if (dirty) { event.preventDefault(); event.returnValue = ''; }
  });
});
document.querySelectorAll('form').forEach((form) => {
  form.addEventListener('submit', () => {
    form.querySelectorAll('button[type="submit"]').forEach((button) => {
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
    });
  });
});

const form = document.querySelector('#login-form');
const params = new URLSearchParams(location.hash.slice(1));
const token = params.get('setup');
// A fragment stays on this origin; never accept an external redirect URL.
const returnHash = !token && /^#[a-z][a-z0-9-]*(?:\/[^?#]*)?$/.test(location.hash) ? location.hash : '';
if (token) {
  form.elements.username.value = params.get('user') || '';
  form.elements.username.readOnly = true;
  form.elements.password.autocomplete = 'new-password';
  form.elements.password.minLength = 12;
  document.querySelector('#confirm-field').hidden = false;
  form.elements.confirm.required = true;
  document.querySelector('#login-title').textContent = '내 계정 시작하기';
  document.querySelector('#login-description').textContent = '12자 이상의 비밀번호를 정해 주세요. 이 링크는 한 번만 사용할 수 있어요.';
  form.querySelector('button').textContent = '비밀번호 설정하고 시작';
  history.replaceState(null, '', '/login');
}
form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('button'), error = document.querySelector('#login-error');
  error.textContent = '';
  if (token && form.elements.password.value !== form.elements.confirm.value) {
    error.textContent = '두 비밀번호가 일치하지 않아요.'; return;
  }
  button.disabled = true;
  try {
    const response = await fetch(token ? '/api/auth/activate' : '/api/auth/login', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: form.elements.username.value, password: form.elements.password.value, ...(token ? {token} : {})})
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.error || '로그인을 완료하지 못했어요.');
    location.replace('/' + returnHash);
  } catch (e) { error.textContent = e.message; button.disabled = false; }
});

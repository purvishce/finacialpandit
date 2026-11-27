const form = document.querySelector('#upload-form');
const fileInput = document.querySelector('#file');
const dropzone = document.querySelector('#dropzone');
const fileTitle = document.querySelector('#file-title');
const fileHint = document.querySelector('#file-hint');
const status = document.querySelector('#status');
const signInButton = document.querySelector('#sign-in');
const authStatus = document.querySelector('#auth-status');
const uploadUrl = 'https://func-financial-pandit-ingest-dev-4629.azurewebsites.net/api/upload_statement';
const scopes = ['api://670cf8e4-fff5-46d5-9d5b-1935021170e5/upload_statement'];
const authClient = typeof msal === 'undefined' ? null : new msal.PublicClientApplication({
  auth: {
    clientId: '670cf8e4-fff5-46d5-9d5b-1935021170e5',
    authority: 'https://login.microsoftonline.com/ef477c82-d887-4340-ac8f-180076382f27',
    redirectUri: 'http://localhost:5500/index.html',
  },
  cache: {cacheLocation: 'sessionStorage'},
});

function currentAccount() {
  return authClient?.getActiveAccount() || authClient?.getAllAccounts()[0];
}

function showAccount(account) {
  if (account) {
    authClient.setActiveAccount(account);
    authStatus.textContent = `Signed in as ${account.username}.`;
    signInButton.textContent = 'Switch account';
  }
}

showAccount(currentAccount());
signInButton.addEventListener('click', async () => {
  if (!authClient) {
    authStatus.textContent = 'Microsoft sign-in could not load. Check your connection and refresh.';
    return;
  }
  try {
    const result = await authClient.loginPopup({scopes, prompt: 'select_account'});
    showAccount(result.account);
  } catch (error) {
    authStatus.textContent = 'Sign-in failed. Check your account, consent, and popup settings.';
  }
});

function showFile(file) {
  status.textContent = '';
  status.classList.remove('success');
  if (!file) {
    fileTitle.textContent = 'Drop your CSV file here';
    fileHint.textContent = 'or browse files from your device';
    return;
  }
  if (!file.name.toLowerCase().endsWith('.csv')) {
    fileInput.value = '';
    fileTitle.textContent = 'Drop your CSV file here';
    fileHint.textContent = 'or browse files from your device';
    status.textContent = 'Please choose a CSV file.';
    return;
  }
  fileTitle.textContent = file.name;
  fileHint.textContent = 'File selected · click to choose another';
}

fileInput.addEventListener('change', () => showFile(fileInput.files[0]));
for (const eventName of ['dragenter', 'dragover']) {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.add('dragging');
  });
}
for (const eventName of ['dragleave', 'drop']) {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.remove('dragging');
  });
}
dropzone.addEventListener('drop', (event) => {
  const file = event.dataTransfer.files[0];
  if (!file) return;
  fileInput.files = event.dataTransfer.files;
  showFile(file);
});
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  status.classList.remove('success');
  status.textContent = 'Uploading statement…';
  try {
    const account = currentAccount();
    if (!account) throw new Error('Please sign in before uploading a statement.');
    let result;
    try {
      result = await authClient.acquireTokenSilent({scopes, account});
    } catch (error) {
      if (error instanceof msal.InteractionRequiredAuthError) {
        throw new Error('Your sign-in needs to be renewed. Select Sign in with Microsoft and try again.');
      }
      throw error;
    }
    const body = new FormData(form);
    body.append('file', fileInput.files[0]);
    const response = await fetch(uploadUrl, {method: 'POST', headers: {Authorization: `Bearer ${result.accessToken}`}, body});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.message || 'Upload failed. Please try again.');
    form.reset();
    showFile(null);
    status.classList.add('success');
    status.textContent = 'Your statement was uploaded successfully.';
  } catch (error) {
    status.textContent = error.message || 'Unable to upload the statement.';
  } finally {
    button.disabled = false;
  }
});

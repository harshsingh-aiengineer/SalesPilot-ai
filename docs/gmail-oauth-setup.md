# Gmail OAuth setup

SalesPilot sends and reads mail only after Google accepts an OAuth grant for your own account. Environment variables alone never show Gmail as connected.

## 1. Google Cloud project

1. Open [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project, for example `salespilot-local`.
3. Enable the **Gmail API** for that project.

## 2. OAuth consent screen

1. Configure the OAuth consent screen as **External** if this is a personal Gmail account.
2. Add your Gmail address as a test user while the app is in testing.
3. Add these scopes:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/gmail.send`
   - `https://www.googleapis.com/auth/gmail.compose`

## 3. OAuth client

1. Create an OAuth client ID of type **Web application**.
2. Add this authorized redirect URI exactly:

   `http://127.0.0.1:8000/api/v1/gmail/callback`

3. Download the client JSON.

## 4. Install the secret locally

Do not commit the file.

PowerShell, from the repository root, after the stack has been started once so the volume exists:

```powershell
docker compose cp .\google_client_secret.json api:/data/secrets/google_client_secret.json
docker compose cp .\google_client_secret.json worker:/data/secrets/google_client_secret.json
```

The API and worker share the `app_data` volume, so copying into either container's `/data/secrets/` is enough if they use the same volume. Copying once into `api` is sufficient.

Keep the downloaded JSON outside git. `.gitignore` already ignores `google_client_secret.json` and `secrets/`.

## 5. Authorize

1. Open http://127.0.0.1:5173/settings
2. Click **Authorize Gmail**.
3. Approve the consent screen with the test user.
4. The settings page runs a live `users.getProfile` call. **Connected** appears only when that call returns your email address.

## 6. Limit what gets processed

The default Gmail query is:

```text
in:inbox -category:promotions -category:social newer_than:2d
```

Newsletters and automated mail are skipped before the model runs. The local model then decides which of the remaining messages are sales enquiries, whatever words they use. You can change the query in Automation.

## 7. Optional live send test

Set `GMAIL_TEST_RECIPIENT` in `.env` to an address you control, then recreate the API and worker containers.

In Email Center:

1. Save a draft to that recipient.
2. Preview it.
3. Approve it if you want the agent to be able to send it later.
4. Click **Send** and confirm the recipient.
5. A successful send shows status **Provider accepted** and a Gmail message id.

Gmail does not give SalesPilot a reliable downstream delivery receipt. The UI does not claim "delivered" unless a provider message id was returned. If the request times out, the draft stays **Send result unknown**. Use **Reconcile** before trying again.

## 8. Revoked access

If you revoke the app in your Google Account permissions, the next profile check returns unauthorized. Use **Authorize Gmail** again. CRM still works while Gmail is disconnected.

## Later: push notifications

The worker polls on an interval. A later version could add Google Cloud Pub/Sub `users.watch`, which needs a public HTTPS endpoint. Polling is the supported local setup.

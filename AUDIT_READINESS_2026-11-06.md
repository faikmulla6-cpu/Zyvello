# Zyvello project audit — 6 November 2026

## Readiness

The local application can proceed to frontend polish. This is not a production
launch approval or a claim that the real send-and-reply workflow has been tested.

## Verified

- React/TypeScript production build passes.
- Frontend dependency audit reports zero known vulnerabilities.
- The 35 packages in the tested Python environment have compatible requirements.
- All 54 Python source modules parse successfully; dashboard JavaScript syntax passes.
- CLI help and training-provider options work, including Groq.
- Chrome checks pass for the landing page, login return, all 13 dashboard tabs,
  and all mailbox provider dialogs, with no JavaScript errors or failed HTTP resources.
- Previous focused browser checks cover signup/login/logout, cookie persistence,
  OAuth state/browser binding, callback return to Settings, responsive layouts,
  connection forms, key fallback and explicit test-send confirmation. Provider
  authorization, AI responses and mailbox writes were mocked where credentials
  would otherwise be required. No real outreach was sent for this audit.
- `.env`, account/mailbox databases, OAuth tokens and generated local config are
  excluded by ignore rules. No literal AI API keys were found in the scanned
  application source. The folder still has no Git metadata.
- Full Windows suite: **1,497 passed, 184 skipped, zero failures** (221.63 seconds).
  Skips include unavailable platform/CLI capabilities, including Windows symlink
  privileges. Ten non-blocking deprecation warnings remain in the test-client
  dependency and the analyst timestamp implementation.

## Corrected during this audit

1. Dashboard Start launched `python -m harvey`, which prints help. It now launches
   `python -m harvey run`. A regression test checks the actual spawned command.
2. A listing discovery dry run could resolve geographic coordinates over the
   network. Estimates now return before geocoding; a regression test rejects any
   geocoder call during the dry run.
3. Updated the CSV-export browser harness for the current page-session initialization
   and login redirection, preserving its authenticated-download/failure checks.
4. Replaced a fragile test that assumed approval attribution was the last database
   migration with a check of the actual migrated outbox schema.
5. A Windows symlink test now skips specifically when the OS reports missing
   symlink privileges (WinError 1314), rather than failing during fixture setup.
   The behavior remains tested on systems with symlink capability.
6. Interrupted dashboard configuration-save files are now ignored as private data.

## Remaining before live customer automation

### Reply recovery needs backend stabilization

Unexpected exceptions in `harvey/agents/handler.py` can still reach a `finally`
block that marks the inbound message processed. A failure before all operations
finish can therefore prevent a clean retry. Simply removing that marker is not
sufficient: an error after writing a conversation, queuing a response, or sending
through Instantly could produce duplicates on retry. Implement durable processing
checkpoints/idempotency and test failure at each boundary, including opt-outs.

The SMTP/IMAP adapter also catches inbox-read failures and returns an empty list,
and fetching RFC822 can mark messages seen before handling completes. Strengthen
inbox failure propagation and acknowledgement/recovery before unattended use.
These are existing backend issues, not frontend polish changes.

### Real services and business configuration are not connected

At audit time the local setup has no saved Groq key, no verified mailbox, no email
verification credentials, and incomplete business/product training configuration.
The selected AI configuration is still the compatibility Claude default; Groq is
available but requires the user's key and provider selection. The application is
running from the existing review environment, not a workspace `.venv`.

Google/Microsoft production OAuth application setup and any applicable provider
approval remain necessary. Test real AI generation, verification, sending,
receiving, opt-outs, reconnect/recovery and reply threading with controlled
mailboxes before customer automation.

### Hosted product remains a later phase

Isolated customer workspaces, durable hosted jobs, per-customer credential storage,
billing and domain deployment remain outside this local frontend milestone.

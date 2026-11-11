# Zyvello landing page

The supplied `Hero2` component is integrated into the local landing page at
`/welcome`. The dashboard is at `/`; signup and login use the same React build.

Signup and login are at `/signup` and `/login`. The first signup creates
the owner account for this local installation. After that, the dashboard and
its APIs require a valid login session. Existing workspace data stays in place.
The account is stored in `data/accounts.db`; this file is ignored by Git.
Separate customer workspaces and social OAuth are part of the later hosted edition.

## Develop and build

Use Node.js 22.12 or newer.

```powershell
cd frontend
npm ci
npm run dev
```

The development server previews the landing page at its printed URL. To test
dashboard links and authentication, build and open the Python app.

```powershell
npm run build:app
cd ..
harvey dashboard
```

Open `http://127.0.0.1:5555/welcome`. Vite compiles the page into
`harvey/web/landing/`, which is included in the Python package and Docker image.
Commit these built assets with source changes so Python users can open the
landing page without Node.js.

For Hostinger static publishing, keep root `frontend`, build command `npm run build`,
and output directory `dist`. The standalone build uses root-relative asset URLs
and copies public branding assets. Python APIs and automation require a separate
backend; this static build does not provide those services. Hosted private-beta
builds use `npm run build:hosted` and require all four public settings from
`.env.hosted.example`, including `VITE_ZYVELLO_API_URL` pointing to the
isolated Modal staging API. Do not put a database connection string or provider
secret in a `VITE_` variable. Server-side CORS must allow the preview origin;
JWT and workspace role checks remain mandatory for every API request.

## Components and styles

- `src/components/ui/hero-2-1.tsx` contains the hero and its mobile navigation.
- `src/components/ui/sign-up.tsx` contains signup and login, the glass controls,
  animated transitions, status dialog, and success confetti.
- `src/demo.tsx` composes the hero with the workflow, pricing, and resources.
- `src/styles/globals.css` contains Tailwind and the shared styles.
- `public/assets/` holds the local Unsplash office photograph and the dashboard
  screenshot generated from illustrative data.

The `@/` alias resolves to `src/`. The standard shadcn UI directory is
`src/components/ui/`, configured as `@/components/ui` in `components.json`.
Keeping this directory gives the shadcn CLI a consistent destination for added
components and preserves imports copied from its registry.

React, TypeScript, Tailwind's Vite plugin, Motion, and Lucide are installed.
The shadcn aliases and theme variables are configured. To add a shadcn component,
run `npx shadcn@latest add button` from this directory. For a fresh project,
the corresponding setup is `npm create vite@latest -- --template react-ts`,
installation of `tailwindcss @tailwindcss/vite`, and `npx shadcn@latest init`.

The hero manages its mobile menu with local React state. Motion respects the
visitor's reduced-motion preference. No application state provider is required.
The navigation uses section links and the existing dashboard entry point.

## Mailbox connections

Settings → Email provider embeds `src/components/ui/integrations-02.tsx`, adapted
from the supplied integration list. There is no separate mailbox screen:
`/mailboxes` redirects to `/#settings`, and OAuth returns there too. The
`src/mailbox-entry.tsx` entry mounts the component inside a Shadow DOM so its
Tailwind reset and glass styles cannot change existing dashboard tabs or forms.
All original mailbox forms remain under Advanced mailbox settings. Its Badge and Button dependencies live alongside it
in `src/components/ui` (the existing `@/components/ui` shadcn alias). React,
TypeScript, Tailwind, Lucide and class-variance-authority were already installed;
Radix Slot was added for the composable Button. No remote favicon service or
stock imagery is required for this application screen.

The page uses the existing login session and `/api/mailboxes` endpoints. Credentials
are never echoed back. Provider status reflects the last successful check; checks
older than 24 hours require verification again. Connection checks send no email
and do not fetch replies through the processing pipeline. The explicit Send test
email action sends one message only to the verified mailbox itself.

Gmail and Microsoft use backend authorization-code flows with PKCE, expiring,
single-use state and an HttpOnly browser-binding cookie. The administrator must
configure OAuth applications once and register the exact callback URL displayed
in Administrator setup. Google requests the existing gmail.modify scope;
Microsoft requests the existing Mail.ReadWrite/Mail.Send/User.Read scopes and
offline access. Public launch requires provider production approval as applicable.
Microsoft confidential web apps can set M365_CLIENT_SECRET; existing native app
and CLI integrations still work without it. Tokens use the existing ignored
data/gmail_token.json and data/m365_token.json paths.

Hostinger Email and Fastmail presets fill editable TLS server settings. Other
SMTP/IMAP settings and Instantly remain available. Save → Test connection → Use
this mailbox changes only the selected provider and verified sender address,
preserving approval rules, daily caps, and all other configuration. Restart running
automation to use the new provider. This remains one local installation with one
active sending provider, not the future isolated hosted customer workspaces.

## Assets

Office photograph from Unsplash

https://images.unsplash.com/photo-1497366811353-6870744d04b2

The local copy keeps the page compatible with the app's existing content security
policy and offline rendering. The screenshot contains only generated sample data.

Connection dialogs use a wide desktop layout with side-by-side account context and controls. Administrator setup and alternate inbox credentials are separate keyboard-accessible tabs; form state survives tab changes. Background scrolling is restored when the dialog closes. Narrow/short viewports retain overflow access so no fields are clipped.

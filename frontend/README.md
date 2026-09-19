# Games frontend

The frontend is an Angular 20 application with server-side rendering and Tailwind CSS. It currently
contains the Angular starter screen; application routes and backend integration are planned but not
yet implemented.

See the root [README](../README.md) for the full-stack quick start and
[ARCHITECTURE.md](../ARCHITECTURE.md) for system boundaries.

## Prerequisites

- Node.js 22
- npm, using the committed `package-lock.json`

Install dependencies from this directory:

```bash
npm ci
```

## Native development

```bash
npm start
```

The Angular development server is available at <http://localhost:4200> and reloads source changes.
The native development command does not use the Compose host port.

## Docker Compose

From the repository root:

```bash
docker compose up --build frontend
```

The SSR application is available at <http://localhost:3040> by default. Compose maps host port
`3040` to the container's production server on port `4000` and supplies
`API_BASE_URL=http://api:8000` for future server-side API integration. The current starter frontend
does not make API requests.

Change the host port with `FRONTEND_PORT` in the root `.env`. Do not change the internal port or API
service name without updating the Compose and server configuration together.

## Available commands

```bash
npm run format:check
npm run typecheck
npm run build
npm test -- --watch=false --browsers=ChromeHeadless
```

- `format:check` verifies Prettier formatting.
- `typecheck` runs TypeScript without emitting files.
- `build` creates the production browser and SSR bundles under `dist/`.
- `test` runs the Karma/Jasmine suite once in headless Chrome.

Use `npm run watch` for a development build that rebuilds on changes. Generate Angular artifacts
through the local CLI with `npm run ng -- generate <schematic>`.

Run all four validation commands for frontend changes and add focused specs when behavior changes.
See [CONTRIBUTING.md](../CONTRIBUTING.md) for branch and pull request requirements.

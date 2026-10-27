# Deploying on Render

The platform runs on [Render](https://render.com) as one Docker web service: the API, its engine worker
(Node) and the built web app, served from one address. Everything Render needs is in the repository:

- `render.yaml`: the Blueprint (the service, its plan, its settings);
- `deploy/render/Dockerfile`: the image, built by Render (nothing is built on your computer);
- `deploy/render/start.sh`: starts the API on Render's port with Render's address.

Decisions and limits: ADR-054.

## What the free plan gives (and what it doesn't)

The Blueprint uses the **free** plan, which suits a demo:

| | Free plan |
|---|---|
| CPU, memory | 0.1 CPU, 512 MB |
| Sleep | after 15 minutes without visitors; the next visit waits about a minute |
| Files | **wiped on every sleep, restart and deploy**: accounts, lessons, audio, ratings, credits |
| Hours | 750 a month per workspace (one service running all month fits) |
| Shell, disk | none |

To fit, the free setup differs from a development machine:
- one engine worker instead of four, one lesson made at a time, more time for each engine call;
- **no visual critic**: the image has no browser. The rest of the teaching pipeline, the models, the
  ElevenLabs voice and doubts are as in your `.env`. Video export needs a browser too, so it is not
  available.

Lessons therefore take longer than on your computer, and after the service sleeps you sign up again.

## Deploy

1. **The code on GitHub.** The repository must be on GitHub (or GitLab or Bitbucket); Render builds from
   there. A public repository needs no further setup in Render. `.env` and its backups are git-ignored,
   so no key goes with the code.
2. **Create the service from the Blueprint.** Open
   `https://render.com/deploy?repo=https://github.com/<you>/<repository>`
   (or in the dashboard: **New → Blueprint**, then the repository).
3. **Fill the three values the form asks for:**
   - `OPENAI_API_KEY` and `ELEVENLABS_API_KEY`: as in your `.env`;
   - `PLATFORM_ADMIN_EMAILS`: your email. You sign up with it and are the administrator.
   Render generates `PLATFORM_SECRET_KEY` itself.
4. **Deploy.** The first build takes about 10 minutes (installing Node and Python packages, building
   the web app). It is done when the logs show `Uvicorn running` and the service is **Live**.
5. **Sign up** at `https://<service>.onrender.com/signup` with the email from step 3.

## Who can sign up

The demo is invite-only (`PLATFORM_ALLOW_SIGNUP=false`): every lesson spends your OpenAI and ElevenLabs
keys, and a public address could otherwise be used by anyone. In the service's **Environment** tab:
- `PLATFORM_ADMIN_EMAILS`: administrators (comma separated);
- `PLATFORM_INVITE_EMAILS`: others who may sign up as learners, e.g. `asha@school.org, @partner.edu`
  (a whole domain starts with `@`);
- `PLATFORM_ALLOW_SIGNUP=true` opens sign-ups to everyone. Each learner then has 1,000 credits
  ($10) a month by default (`PLATFORM_MONTHLY_CREDITS_LEARNER`).

Saving the Environment tab restarts the service. On the free plan that also wipes its files.

## Updating

Push to the repository, then **Manual Deploy → Deploy latest commit** in the service's page. Render deploys
pushes by itself only when the repository is connected through Render's GitHub app. Each deploy wipes the
free service's files.

## A lasting setup (paid)

For accounts and lessons that stay, change the service in `render.yaml` (or in the dashboard):

```yaml
    plan: standard            # 1 CPU, 2 GB
    disk:
      name: data
      mountPath: /app/var     # lessons, audio and the platform's database
      sizeGB: 5
```

With 2 GB, also set `ENGINE_WORKER_POOL_SIZE=2` and `ANIMATOR_GENERATION_CONCURRENCY=2`. The visual critic
needs a browser in the image (Playwright's Chromium, as in `docker/export.Dockerfile`), about 400 MB of
memory more while it runs.

## When something goes wrong

- **The build fails:** the **Logs** of the deploy show the step. `pnpm install` and `uv sync` use the
  lockfiles, so a failure there usually means a lockfile was not committed.
- **Every lesson stops before it starts:** open the lesson's details (`/api/generations/<id>`); an
  "internal error: FileNotFoundError" names a file the image leaves out. The server reads a few files
  from the repository's folders (the DSL and component references in `docs/`, example scenes in
  `fixtures/scenes/`, `config/`); `apps/api/tests/test_deploy_image.py` checks the Dockerfile copies
  each of them. (The first deploy lacked `docs/DSL.md`.)
- **The service restarts during a lesson** (out of memory): keep one engine worker. Long lessons use the
  most memory.
- **"Sign-ups are closed":** the email is not in `PLATFORM_ADMIN_EMAILS` or `PLATFORM_INVITE_EMAILS`, or
  differs in spelling.
- **Lessons stop with a model error:** check the keys in the Environment tab (they must not have spaces
  around them).

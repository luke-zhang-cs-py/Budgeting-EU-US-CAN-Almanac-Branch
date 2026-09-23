<!-- Split out of README.md: 150 lines of deployment procedure was
     burying what the project actually is. -->

# Deploying Wallet somewhere other than your own machine

The app binds `127.0.0.1` with no password because it holds your spending
history. Reaching it from anywhere else means turning that off, so it will not
let you do it by accident:

```
loopback, no password   ->  runs, no login.        What it has always done.
loopback, password set  ->  runs, asks for it.
anything else, no hash  ->  refuses to start.
```

That last line is a raise, not a warning. `auth.guard` raises `Unsafe` and the
process exits, telling you what to set. A warning printed into a log nobody
reads is how a financial ledger ends up on the open internet with no password
on it.

### Read this before your first deploy

**Point `WALLET_DATA` at a disk that survives a restart.** This is the single
most likely way to lose the data. Most platforms give each deploy a fresh
filesystem, so `wallet.db`, the rate cache and every uploaded screenshot are
destroyed the next time you push. The `Dockerfile` declares
`VOLUME ["/data"]` and sets `WALLET_DATA=/data` for exactly this reason —
attach something to it. On Fly.io that is `fly volumes create`; on Render, a
disk; on a VPS, a bind mount.

Back it up too. It is one SQLite file and a folder of images.

### Setting it up

```bash
python -m auth                    # asks twice, prints WALLET_PASSWORD_HASH=...
python -c "import secrets; print(secrets.token_hex(32))"   # SECRET_KEY
```

The password is typed, never passed as an argument — a command line ends up in
shell history and in the process list. Only the hash is stored, so there is
nothing anywhere that can recover the password; keep it in a password manager.

Then, in the environment where it runs:

| Variable | |
|---|---|
| `WALLET_PASSWORD_HASH` | the scrypt hash. Required once reachable. |
| `SECRET_KEY` | 32+ characters. Signs the session cookie; a guessable one lets anyone mint a logged-in session. Required once reachable. |
| `WALLET_DATA` | the persistent volume. See above. |
| `HOST` | `0.0.0.0` on a platform that proxies to you. |
| `PORT` | usually set for you. |

```bash
docker build -t wallet .
docker run -p 8000:8000 -v wallet-data:/data \
  -e WALLET_PASSWORD_HASH='scrypt:...' \
  -e SECRET_KEY='...' \
  wallet
```

Or without Docker: `gunicorn wsgi:application --workers 2 --timeout 120`.
`wsgi.py` exists rather than reusing `app.py`'s `__main__` because a
deployment differs in ways that matter — the dev server is single-threaded, and
the folder watcher must not run once per worker, since the right number of
threads writing to one SQLite file is one.

### On Fly.io, in order

`fly.toml` is in the repository. The order of these matters: the app refuses
to start without the two secrets, so setting them after the first deploy
means watching that deploy fail first.

```bash
fly auth login
fly launch --no-deploy          # reads fly.toml; change the app name when asked

fly volumes create wallet_data --size 1 --region yyz    # the disk. Not optional.

python -m auth                                          # prints the hash
fly secrets set WALLET_PASSWORD_HASH='scrypt:32768:8:1$...'
fly secrets set SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"

fly deploy
fly scale count 1               # one machine. SQLite has one writer.
fly open                        # and log in
```

Then `fly logs` if anything is wrong. Two failures are worth recognising on
sight, because both are the app working as intended:

| What you see | What it is |
|---|---|
| `Unsafe: refusing to bind 0.0.0.0 with no password` | `WALLET_PASSWORD_HASH` is not set, or the deploy predates it. The process exits rather than serving an unprotected ledger. |
| the login page accepts the password, then returns to the login page | you reached it over `http://`. The session cookie is `Secure`, so it was never sent. `force_https = true` is in `fly.toml` for this; use the `https://` URL. |

Both platforms now cost a few dollars a month for a machine with a disk
attached — Fly has no free allowance for a new organisation, and Render's
free tier has no disk at all, which for this app means losing everything on
each deploy. `render.yaml` is included with that written at the top of it.

**Consider not hosting it.** The reason to put this on the internet is to
reach it from a phone, and there is now a version of the part you actually
want out of the house — the [capture page](#the-same-job-with-no-server-the-capture-page),
which is already published, costs nothing, holds its purchases in the browser
and exports a CSV this app imports. Hosting the full app means trusting a
host with an unencrypted file of everything you have spent. That is a real
decision and it is worth making deliberately rather than because a deploy
button was there.

These two config files have not been deployed from this repository — no
platform account is attached to it. They are written against each platform's
documented schema, and the first `fly deploy` or the Render dashboard will
tell you if a field name has moved since.

What *is* checked is that they agree with everything they have to agree with.
`tests/test_deployment.py` asserts the port both configs route to is the one
the container exposes, that the volume is mounted exactly where
`WALLET_DATA` points, that both pin a single instance, and that the health
check names a route in `app.OPEN_ENDPOINTS` — because every other route needs
a session, so a health check on one of those would have both platforms
restarting a container that is working perfectly. Each of those was confirmed
by breaking it and watching the test go red.

### HTTPS is the host's job, and the app assumes you did it

Once `HOST` is not loopback the session cookie is marked `Secure`, so **it is
not sent over plain HTTP at all**. If you deploy and the login page accepts
your password and then bounces you straight back to it, that is this — you are
on `http://`. Every platform worth using terminates TLS for you; put it behind
one rather than turning the flag off.

`Strict-Transport-Security` is sent only when public, because promising HTTPS
on a loopback run that has none makes the app unreachable in a browser that
believes it.

### What the protection is, and what it is not

| Threat | What is done about it |
|---|---|
| Guessing the password | scrypt, and three free attempts then a refusal window that doubles to 15 minutes. Refused, not slept — holding the request open would let an attacker exhaust the workers for free. |
| A forged request from another site | A session token that must come back in a header, on all 19 state-changing routes. `SameSite=Lax` too, but that is a second lock rather than the lock: three of those routes take multipart uploads, which a plain cross-origin form can send. |
| A stolen cookie | `HttpOnly` so script cannot read it, `Secure`, and a 14-day lifetime. |
| Someone reading the page | Nothing is reachable without a session except `/login` and `/health`, and that list is checked by a test that walks the real routing table — so a route added later is closed because it was not opted out, rather than exposed because somebody forgot to opt it in. |
| XSS | A CSP with `script-src 'self'`, which is only possible because the page has no inline script at all. |

And what it does not address, because it cannot: **anyone who can read the
disk can read the database.** There is no encryption at rest here. Hosting
this means trusting the host with the file — which is a real decision, and the
reason the app defaults to your own machine.

There is also one account, deliberately. No registration, no password reset,
no email. All of that is attack surface, and a single-user ledger has no use
for any of it.

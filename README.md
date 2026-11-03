# VPN Serverlist Ingestion Job

Sync the [Fastly VPN locations API](https://locations.m1.fastly-masque.net/v1/locations)
into the `vpn-serverlist` collection on Remote Settings.

* [Compliant with specs](https://remote-settings.readthedocs.io/en/latest/support.html#how-do-i-automate-the-publication-of-records-forever)
* Dry run
* Dual sign-off
* Lint
* Github Action CI and CD
* Tests with `kinto_http` mocks
* Sentry integration

## How it works

Each synced Fastly location becomes one record: a country with a single city and server.

- The `closest` location is validated but not synced. The existing `REC` (Recommended) record is
  maintained in Remote Settings and left unchanged.
- A country listed several times keeps only its aggregate entry (`us.m1.fastly-masque.net`).
- Unavailable servers are marked `quarantined: true`.
- Existing records keep their id and their `locked` flag. New countries are created locked.
- The US record is gated to Firefox >= 151, older versions get the hand-maintained US
  "CatchAll Anycast" record. The job keeps that filter, and never touches the CatchAll.
- Nothing is ever deleted.

Before writing anything, the job aborts, reporting to Sentry, if the Fastly response is not
what it expects, or if a live record has a field it does not know about (its update would
erase it).

Changes are then submitted for review, except on `dev` where they are approved right away.

## Run

With local Remote Settings server:

```
$ docker run --rm --detach \
    -p 8888:8888 \
    --env KINTO_INI=config/testing.ini \
    mozilla/remote-settings
```

Create the destination collection:

```
$ curl -X PUT http://localhost:8888/v1/buckets/main-workspace/collections/vpn-serverlist
```

And run the script:

```
$ uv sync
$ SERVER="http://localhost:8888/v1" uv run script.py

Fetch server info...✅
⚠️ Anonymous
Fetch records from source of truth...✅
Fetch current destination records...✅
Batch #0: PUT /buckets/main-workspace/collections/vpn-serverlist/records/... - 201
...
Apply changes...33 operations ✅
Request review...✅
```

Or from the Docker container:

```
$ docker build -t ingestion:latest .
$ docker run -e SERVER=http://host.docker.internal:8888/v1 ingestion:latest
...
Records are in sync. Nothing to do ✅.
```

### On Remote Settings official servers

([List of environment servers](https://remote-settings.readthedocs.io/en/latest/getting-started.html#environments))

**As yourself**:

Login on the Admin UI and copy the Bearer header value (UI top right bar)

And use it to run the script

```
$ read -s BEARER
$ AUTHORIZATION=$BEARER ENVIRONMENT=prod uv run script.py
```

**Using an account**:

```
$ read -s PASSWD
$ AUTHORIZATION=vpn-publisher:$PASSWD ENVIRONMENT=prod uv run script.py
```

## Develop

See available operations using:

```
make help
```

Unit tests are offline (`make test`). And see how unit and integration tests are run on Github
Action CI.
